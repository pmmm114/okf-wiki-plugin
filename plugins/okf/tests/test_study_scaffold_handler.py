"""writable-vault 스캐폴드 테스트 — 핸들러 생성·배선·멱등·비파괴·무참조·격리."""

from __future__ import annotations

import json
import stat

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
