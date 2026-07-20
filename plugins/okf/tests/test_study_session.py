"""study_session — 홈 폴백 넛지·무효 포인터 경고 방출 테스트 (#91 V2).

매트릭스 대응: #9·#19(경고 방출 지점 = SessionStart 계열), 홈 auto 넛지.
"""

from __future__ import annotations

import json

import okf_home
import okf_inbox
import pytest
import study_session


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _home(tmp_path, config: dict):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def test_project_auto_nudges(tmp_path):
    (tmp_path / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "auto"}}), encoding="utf-8"
    )
    okf_inbox.append(tmp_path, "candidate", "src")
    message = study_session.run(tmp_path)
    assert message and "승격 대기 후보 1개" in message


def test_home_auto_nudges_from_configless_dir(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.append(home, "candidate", "src")
    project = tmp_path / "scratch"
    project.mkdir()
    message = study_session.run(project)
    assert message and "승격 대기 후보 1개" in message


def test_home_review_no_nudge(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.append(home, "candidate", "src")
    project = tmp_path / "scratch"
    project.mkdir()
    assert study_session.run(project) is None


def test_invalid_pointer_emits_warning(monkeypatch, tmp_path):
    # #9·#19 — SessionStart 계열이 경고 방출 지점이다
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    project = tmp_path / "scratch"
    project.mkdir()
    message = study_session.run(project)
    assert message and "홈 포인터 무효" in message and "doctor" in message


def test_no_pointer_stays_silent(tmp_path):
    project = tmp_path / "scratch"
    project.mkdir()
    assert study_session.run(project) is None
