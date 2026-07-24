"""writable-vault 스캐폴드 테스트 — 핸들러 생성·배선·멱등·비파괴·무참조·격리."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import okf_vault
import pytest
import study_scaffold_handler as ssh


def _make_vault(path, config=None):
    """유효 vault(실재 + .git + .okf-wiki.json)를 만든다 — valid_vault 판정 통과용."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    (path / ".okf-wiki.json").write_text(
        json.dumps(config if config is not None else {}) + "\n", encoding="utf-8"
    )
    return path


def _read(vault):
    return json.loads((vault / ".okf-wiki.json").read_text(encoding="utf-8"))


# --- 임베드된 핸들러 템플릿 (무참조·계약·유효성) ------------------------------


def test_handler_template_is_valid_python():
    compile(ssh.HANDLER_TEMPLATE, "okf-open-pr.py", "exec")


def test_handler_template_contract_and_no_destination():
    t = ssh.HANDLER_TEMPLATE
    # 계약 표면
    for marker in ("OKF_CONCEPT_PATH", "OKF_PROJECT", "worktree", "origin"):
        assert marker in t, f"핸들러에 {marker} 없음"
    # 목적지 무참조 — 특정 소비처/wiki/제3자 repo명을 박지 않는다(origin 상대만).
    lowered = t.lower()
    for banned in ("github.com/", "gitlab.com/", "http://", "https://github"):
        assert banned not in lowered, f"핸들러에 하드코딩 목적지 흔적: {banned}"


def test_handler_template_matches_docs_example():
    """임베드 템플릿과 docs 예시가 드리프트하지 않도록 잠근다(단일 정본).

    HANDLER_TEMPLATE이 정본이고 docs/examples/okf-open-pr.py.example은 그 산출 사본이다
    (예시는 템플릿에서 생성). 한쪽만 고치면 이 테스트가 깨져 동기화를 강제한다.
    """
    example = Path(__file__).resolve().parents[3] / "docs" / "examples" / "okf-open-pr.py.example"
    if not example.is_file():  # 레포 외(설치형 플러그인) 배치면 잠글 대상이 없다 — 스킵
        pytest.skip("docs 예시 부재(레포 외 배치)")
    assert example.read_text(encoding="utf-8") == ssh.HANDLER_TEMPLATE


# --- writable_state (마법사 기계 판정) ---------------------------------------


def test_writable_state_absent(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    st = ssh.writable_state(vault)
    assert st == {
        "handler_wired": False,
        "capture": "off",
        "ready": False,
        "managed": False,
    }


def test_writable_state_ready_after_scaffold(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    ssh.scaffold(vault)
    st = ssh.writable_state(vault)
    assert st["handler_wired"] is True
    assert st["capture"] == "review"
    assert st["ready"] is True


# --- scaffold 오케스트레이션 -------------------------------------------------


def test_scaffold_creates_handler_and_wires(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    result = ssh.scaffold(vault)
    assert result["ok"] is True

    handler = vault / ssh.DEFAULT_COMMAND
    assert handler.is_file()
    assert handler.read_text(encoding="utf-8") == ssh.HANDLER_TEMPLATE
    assert handler.stat().st_mode & stat.S_IXUSR  # 실행 비트(디스패처가 직접 실행)

    study = _read(vault)["study"]
    assert study["handlers"] == [{"name": ssh.DEFAULT_NAME, "command": ssh.DEFAULT_COMMAND}]
    assert study["capture"] == "review"


def test_scaffold_invalid_vault_no_write(tmp_path):
    bare = tmp_path / "not-a-vault"
    bare.mkdir()
    result = ssh.scaffold(bare)
    assert result["ok"] is False
    assert not (bare / ssh.DEFAULT_COMMAND).exists()


def test_scaffold_idempotent_second_run(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    ssh.scaffold(vault)
    handler = vault / ssh.DEFAULT_COMMAND
    cfg_before = (vault / ".okf-wiki.json").read_text(encoding="utf-8")
    h_before = handler.read_text(encoding="utf-8")

    ssh.scaffold(vault)

    assert handler.read_text(encoding="utf-8") == h_before
    assert (vault / ".okf-wiki.json").read_text(encoding="utf-8") == cfg_before


# --- 비파괴: 기존 파일·키·capture 보존 ---------------------------------------


def test_existing_handler_not_overwritten(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    handler = vault / ssh.DEFAULT_COMMAND
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text("# 기존 사용자 핸들러\n", encoding="utf-8")

    status = ssh.ensure_handler(vault)

    assert "유지" in status
    assert handler.read_text(encoding="utf-8") == "# 기존 사용자 핸들러\n"


def test_wiring_preserves_other_config_keys(tmp_path):
    vault = _make_vault(tmp_path / "kb", {"bundlePath": "docs/.okf", "inject": False})
    ssh.ensure_wiring(vault)
    data = _read(vault)
    assert data["bundlePath"] == "docs/.okf"
    assert data["inject"] is False
    assert data["study"]["capture"] == "review"


def test_wiring_does_not_demote_capture(tmp_path):
    vault = _make_vault(tmp_path / "kb", {"study": {"capture": "auto", "handlers": []}})
    out = ssh.ensure_wiring(vault, level="review")
    assert _read(vault)["study"]["capture"] == "auto"  # 격하 금지
    assert any("유지(auto" in line for line in out)


def test_wiring_no_duplicate_handler(tmp_path):
    existing = {
        "study": {
            "capture": "review",
            "handlers": [{"name": "kb-pr", "command": ssh.DEFAULT_COMMAND}],
        }
    }
    vault = _make_vault(tmp_path / "kb", existing)
    ssh.ensure_wiring(vault)
    assert _read(vault)["study"]["handlers"] == [{"name": "kb-pr", "command": ssh.DEFAULT_COMMAND}]


def test_wiring_bad_config_raises(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    (vault / ".okf-wiki.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ssh.ensure_wiring(vault)


# --- 관리형 clone vs 로컬 경로 안내(diverge 방지) ----------------------------


def test_guidance_local_vault_says_commit(tmp_path):
    vault = _make_vault(tmp_path / "kb")
    result = ssh.scaffold(vault)
    assert result["managed"] is False
    joined = "\n".join(result["guidance"])
    assert "커밋" in joined and "diverge" not in joined


def test_guidance_managed_clone_warns_diverge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(okf_vault.managed_root() / "host-org-kb-abcd1234")
    result = ssh.scaffold(vault)
    assert result["managed"] is True
    joined = "\n".join(result["guidance"])
    assert "diverge" in joined and "setup/okf-writable" in joined


# --- CLI ---------------------------------------------------------------------


def test_cli_status_json(tmp_path, capsys):
    vault = _make_vault(tmp_path / "kb")
    assert ssh.main(["status", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ready"] is False


def test_cli_scaffold_json(tmp_path, capsys):
    vault = _make_vault(tmp_path / "kb")
    assert ssh.main(["scaffold", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == ssh.DEFAULT_COMMAND


def test_scaffold_malformed_config_writes_no_handler(tmp_path):
    """깨진 .okf-wiki.json이면 scaffold는 write 전에 raise하고 핸들러 orphan을 안 남긴다.

    valid_vault는 존재만 보고 파싱은 안 하므로, present-but-corrupt config는 게이트를
    통과한다. scaffold가 ensure_handler(파일 write)보다 _read_config(파싱)를 먼저 하지
    않으면 '실패 보고했는데 트리는 변경됨' orphan이 남는다.
    """
    vault = _make_vault(tmp_path / "kb")
    (vault / ".okf-wiki.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ssh.scaffold(vault)
    assert not (vault / ssh.DEFAULT_COMMAND).exists()  # orphan 없음


# --- 핸들러 실행 회귀(BLOCKER): 한글 경로 개념이 유실 없이 PR 브랜치로 -----------


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 필요")
def test_handler_carries_korean_path_concept_end_to_end(tmp_path):
    """핸들러를 실제 실행해 **한글 주제 디렉터리** 개념이 PR 브랜치로 올라가는지 잠근다.

    회귀 방지: `git status --porcelain`은 비ASCII 경로를 따옴표+백슬래시 이스케이프로
    내므로 -z(무인용) 없이 파싱하면 개념이 조용히 누락되고 clone이 dirty로 남았다.
    이 repo는 한글 주제 디렉터리가 표준이라 흔한 케이스 — 실행 계약을 통째로 검증한다.
    """
    origin, seed, clone, stub = (tmp_path / n for n in ("o.git", "seed", "clone", "bin"))
    stub.mkdir()
    _git("init", "--bare", "--quiet", str(origin), cwd=tmp_path)
    _git("init", "--quiet", str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@e", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    (seed / ".okf").mkdir()
    (seed / ".okf-wiki.json").write_text('{"bundlePath": ".okf"}', encoding="utf-8")
    (seed / ".okf/index.md").write_text("# index\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git("commit", "--quiet", "-m", "init", cwd=seed)
    _git("branch", "-M", "trunk", cwd=seed)
    _git("push", "--quiet", "-u", "origin", "trunk", cwd=seed)
    _git("symbolic-ref", "HEAD", "refs/heads/trunk", cwd=origin)  # bare 기본 브랜치 = trunk
    _git("clone", "--quiet", str(origin), str(clone), cwd=tmp_path)
    _git("config", "user.email", "t@e", cwd=clone)
    _git("config", "user.name", "t", cwd=clone)

    # 승격 산출물: 한글 주제 디렉터리의 새 개념(미커밋) + 수정된 index
    (clone / ".okf/한글주제").mkdir(parents=True)
    (clone / ".okf/한글주제/my-idea.md").write_text("# 개념\n본문\n", encoding="utf-8")
    (clone / ".okf/index.md").write_text("# index\n- my-idea\n", encoding="utf-8")

    (stub / "gh").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    (stub / "gh").chmod(0o755)

    handler = clone / "scripts" / "okf-open-pr.py"
    handler.parent.mkdir()
    handler.write_text(ssh.HANDLER_TEMPLATE, encoding="utf-8")  # 스캐폴드가 까는 그 텍스트
    env = dict(
        os.environ,
        PATH=f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
        OKF_PROJECT=str(clone),
        OKF_CONCEPT_PATH=".okf/한글주제/my-idea.md",
        OKF_CONCEPT_TYPE="concept",
        OKF_CONCEPT_TOPIC="한글주제",
    )
    result = subprocess.run(
        [sys.executable, str(handler)],
        input="{}",
        text=True,
        env=env,
        cwd=str(clone),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr

    # 한글 경로 개념이 PR 브랜치에 실제 커밋됐고(누락 없음), clone은 clean(잔재 없음).
    committed = _git("show", "study/한글주제/my-idea:.okf/한글주제/my-idea.md", cwd=origin).stdout
    assert "개념" in committed
    assert _git("status", "--porcelain", cwd=clone).stdout == ""
