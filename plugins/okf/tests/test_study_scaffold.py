"""study_scaffold 멱등·비파괴·gitignore 규칙 테스트 (S1, #73)."""

from __future__ import annotations

import json
import subprocess

import pytest
import study_scaffold


def test_gitignore_created_with_exact_body(tmp_path):
    study_scaffold.scaffold(tmp_path)
    gitignore = tmp_path / ".okf-study" / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == "*\n!.gitignore\n"


def test_config_created_with_study(tmp_path):
    study_scaffold.scaffold(tmp_path)
    data = json.loads((tmp_path / ".okf-wiki.json").read_text(encoding="utf-8"))
    assert data["study"] == {"capture": "off", "handlers": []}


def test_idempotent_second_run_is_byte_identical(tmp_path):
    study_scaffold.scaffold(tmp_path)
    gitignore = tmp_path / ".okf-study" / ".gitignore"
    config = tmp_path / ".okf-wiki.json"
    gi_before = gitignore.read_text(encoding="utf-8")
    cfg_before = config.read_text(encoding="utf-8")

    study_scaffold.scaffold(tmp_path)

    assert gitignore.read_text(encoding="utf-8") == gi_before
    assert config.read_text(encoding="utf-8") == cfg_before


def test_preserves_existing_config_keys(tmp_path):
    config = tmp_path / ".okf-wiki.json"
    config.write_text(
        json.dumps({"bundlePath": "docs/.okf", "inject": False}) + "\n", encoding="utf-8"
    )

    study_scaffold.scaffold(tmp_path)

    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["bundlePath"] == "docs/.okf"
    assert data["inject"] is False
    assert data["study"] == {"capture": "off", "handlers": []}


def test_existing_study_block_untouched(tmp_path):
    config = tmp_path / ".okf-wiki.json"
    config.write_text(
        json.dumps({"study": {"capture": "review", "handlers": [{"name": "x", "command": "s.sh"}]}})
        + "\n",
        encoding="utf-8",
    )
    before = config.read_text(encoding="utf-8")

    study_scaffold.scaffold(tmp_path)

    assert config.read_text(encoding="utf-8") == before  # study 존재 → 재작성 없음


def test_bad_config_raises_value_error(tmp_path):
    (tmp_path / ".okf-wiki.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        study_scaffold.scaffold(tmp_path)


# --- CLI 가드 (#104 — 판정은 스크립트, fail-closed) -------------------------


def test_cli_refuses_non_git_dir(tmp_path, capsys):
    # 비-git 위치: 거부(exit 3) + 파일 생성 0 — 홈 폴백 안내 포함
    assert study_scaffold.main([str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "git repo가 아님" in out and "--home" in out and "--force" in out
    assert not (tmp_path / ".okf-study").exists()
    assert not (tmp_path / ".okf-wiki.json").exists()


def test_cli_force_bypasses_guard(tmp_path):
    assert study_scaffold.main([str(tmp_path), "--force"]) == 0
    assert (tmp_path / ".okf-study" / ".gitignore").exists()
    assert (tmp_path / ".okf-wiki.json").exists()


def test_cli_git_repo_proceeds(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("OKF_HOME_PROJECT", raising=False)
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    assert study_scaffold.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert ".okf-study/.gitignore: 생성" in out
    assert "주의" not in out  # 포인터 없으면 고지 없음


def test_cli_home_pointer_notice(tmp_path, monkeypatch, capsys):
    # git repo + 유효 홈 포인터: 진행하되 우선순위 고지를 기계 출력
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OKF_HOME_PROJECT", str(home))
    project = tmp_path / "work"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    assert study_scaffold.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "주의" in out and "홈 캡처보다 우선" in out


def test_guard_accepts_git_file_worktree(tmp_path):
    # worktree/서브모듈의 .git 파일도 인정
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert study_scaffold.guard(tmp_path) is None


def _is_ignored(cwd, rel: str) -> bool:
    result = subprocess.run(["git", "check-ignore", rel], cwd=cwd, capture_output=True, text=True)
    return result.returncode == 0


def test_gitignore_rules_hide_runtime_state(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    study_scaffold.scaffold(tmp_path)

    # 런타임 상태는 무시, 무시 규칙 파일 자신은 추적 대상(=미무시).
    assert _is_ignored(tmp_path, ".okf-study/inbox.md")
    assert _is_ignored(tmp_path, ".okf-study/ledger")
    assert _is_ignored(tmp_path, ".okf-study/trust")
    assert not _is_ignored(tmp_path, ".okf-study/.gitignore")
