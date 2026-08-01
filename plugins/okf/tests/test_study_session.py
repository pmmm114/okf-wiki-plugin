"""study_session — vault 폴백 넛지·무효 포인터 경고 방출 테스트 (#91 V2).

매트릭스 대응: #9·#19(경고 방출 지점 = SessionStart 계열), vault auto 넛지.
"""

from __future__ import annotations

import json

import okf_vault
import pytest
import study_inbox
import study_scope
import study_session
import study_store


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _vault(tmp_path, config: dict):
    vault = tmp_path / "vault-kb"
    (vault / ".git").mkdir(parents=True)
    (vault / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return vault


def _rt(project):
    return study_scope.resolve_capture(project)["runtime_root"]


def test_project_auto_nudges(tmp_path):
    (tmp_path / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "auto"}}), encoding="utf-8"
    )
    study_inbox.append(_rt(tmp_path), "candidate", "src")  # 프로젝트 스코프 = <repo>/.okf-study
    message = study_session.run(tmp_path)
    assert message and "승격 대기 후보 1개" in message


def test_vault_auto_nudges_from_configless_dir(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    # vault 폴백 = 유저 스코프
    study_inbox.append(study_scope.user_scope_runtime(), "candidate", "src")
    project = tmp_path / "scratch"
    project.mkdir()
    message = study_session.run(project)
    assert message and "승격 대기 후보 1개" in message


def test_vault_review_emits_observation(monkeypatch, tmp_path):
    # #352 — review는 지시 없는 관측 1줄: 저장 없는 세션도 대기 규모를 본다
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    study_inbox.append(study_scope.user_scope_runtime(), "candidate", "src")
    project = tmp_path / "scratch"
    project.mkdir()
    message = study_session.run(project)
    assert message and "후보 1개" in message and "review" in message
    assert "trust" not in message  # auto의 지시형 문구가 아니다


def test_vault_review_empty_inbox_silent(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = tmp_path / "scratch"
    project.mkdir()
    assert study_session.run(project) is None


def test_invalid_pointer_emits_warning(monkeypatch, tmp_path):
    # #9·#19 — SessionStart 계열이 경고 방출 지점이다
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "nowhere"))
    project = tmp_path / "scratch"
    project.mkdir()
    message = study_session.run(project)
    assert message and "Vault 포인터 무효" in message and "doctor" in message


def test_no_pointer_stays_silent(tmp_path):
    project = tmp_path / "scratch"
    project.mkdir()
    assert study_session.run(project) is None


def test_sqlite3_absence_warns_when_capture_active(monkeypatch, tmp_path):
    # 캡처 옵트인 + 스테이징 비활성 = "옵트인 후 고장" — 경고 방출 지점은 SessionStart
    (tmp_path / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    monkeypatch.setattr(study_store, "available", lambda: False)
    message = study_session.run(tmp_path)
    assert message and "sqlite3" in message and "doctor" in message


def test_sqlite3_absence_silent_without_capture_optin(monkeypatch, tmp_path):
    # 옵트인이 없으면 고장도 아니다 — 무음 유지(캡처 off와 동형)
    monkeypatch.setattr(study_store, "available", lambda: False)
    project = tmp_path / "scratch"
    project.mkdir()
    assert study_session.run(project) is None
