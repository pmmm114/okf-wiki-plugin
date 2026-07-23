"""study.py 오케스트레이션 CLI + study_session 나즈 테스트 (S5, #77)."""

from __future__ import annotations

import json
import subprocess

import okf_vault
import pytest
import study
import study_inbox
import study_session


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # vault 포인터·설정이 새어들지 않게 격리 → 바 프로젝트는 in-repo 런타임으로 해소
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _rt(project):
    """CLI가 쓰는 런타임 루트 — 바 프로젝트(설정·vault 없음)는 in-repo <repo>/.okf-study(#114)."""
    return project / ".okf-study"


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def _cfg(project, capture, handlers):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": handlers}}), encoding="utf-8"
    )


def test_list_outputs_candidates(tmp_path, capsys):
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    study.main(["list", str(tmp_path)])
    out = _out(capsys)
    assert len(out) == 1
    assert out[0]["snippet"] == "a"


def test_resolve_records_and_drops(tmp_path, capsys):
    ident = study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    study.main(
        ["resolve", str(tmp_path), "--id", ident, "--status", "promoted", "--ref", ".okf/x.md"]
    )
    assert _out(capsys)["dropped"] == [ident]
    assert study_inbox.is_resolved(_rt(tmp_path), ident)
    assert study_inbox.list_candidates(_rt(tmp_path)) == []


def test_clear_discards_all(tmp_path, capsys):
    i1 = study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    i2 = study_inbox.append(_rt(tmp_path), "b", "s", date="2026-07-19")
    study.main(["clear", str(tmp_path)])
    assert set(_out(capsys)["discarded"]) == {i1, i2}
    assert study_inbox.is_resolved(_rt(tmp_path), i1)
    assert study_inbox.is_resolved(_rt(tmp_path), i2)
    assert study_inbox.list_candidates(_rt(tmp_path)) == []


def test_dispatch_no_handlers(tmp_path, capsys):
    _cfg(tmp_path, "off", [])
    study.main(["dispatch", str(tmp_path), "--source", "manual"])
    assert _out(capsys)["note"] == "핸들러 없음"


def test_dispatch_untrusted_reports_note(tmp_path, capsys):
    def _git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    _git("init")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "t")
    script = tmp_path / "scripts" / "h.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    script.chmod(0o755)
    _git("add", "-A")
    _git("commit", "-m", "handler")
    _cfg(tmp_path, "review", [{"name": "h", "command": "scripts/h.sh"}])

    study.main(["dispatch", str(tmp_path), "--source", "manual", "--concept-path", ".okf/x.md"])
    res = _out(capsys)
    assert any(s["reason"] == "trust 미승인" for s in res["skipped"])
    assert "미승인" in res["note"]


def test_session_nudges_when_auto_and_pending(tmp_path):
    _cfg(tmp_path, "auto", [])
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    message = study_session.run(tmp_path)
    assert message and "1개" in message


def test_session_silent_when_review(tmp_path):
    _cfg(tmp_path, "review", [])
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    assert study_session.run(tmp_path) is None


def test_session_silent_when_no_candidates(tmp_path):
    _cfg(tmp_path, "auto", [])
    assert study_session.run(tmp_path) is None


def test_log_outputs_journal(tmp_path, capsys):
    ident = study_inbox.append(_rt(tmp_path), "a", "s")
    study.main(["log", str(tmp_path)])
    out = _out(capsys)
    assert len(out) == 1
    assert out[0]["action"] == "capture" and out[0]["id"] == ident
