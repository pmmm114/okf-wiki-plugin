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
