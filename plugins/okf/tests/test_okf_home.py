"""okf_home — 포인터·홈 판정·주입 해소 테스트 (#91 V2, generic 층).

매트릭스 대응: #9(침묵), 주입 3단 규칙, 무효 사유 코드. 캡처 스코프·캡처 입구·
캡처 활성(study 층)은 #145 U3 분할로 test_study_scope.py로 이동했다 — 이 파일은
feature 지식이 없는 generic 절반만 고정한다. CLI(set/status)의 py 단독 동작은
test_okf_hooks_home.py가 담당한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import okf_home
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """실환경 포인터·설정이 새어들지 않게 HOME·env를 테스트별로 격리한다."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _home(tmp_path, config: dict | None) -> Path:
    """유효 홈 골격(git + 설정)을 만든다. config=None이면 설정 파일 생략."""
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    if config is not None:
        (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def _project(tmp_path, config: dict | None = None) -> Path:
    project = tmp_path / "work"
    project.mkdir(exist_ok=True)
    if config is not None:
        (project / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return project


# --- 포인터·home_state -----------------------------------------------------


def test_no_pointer_is_silent():
    assert okf_home.home_state() == (None, None)


def test_env_pointer_valid(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert okf_home.home_state() == (str(home), None)


def test_pointer_file_with_whitespace(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    fake_home = tmp_path / "isolated-home"
    pointer = fake_home / ".claude" / "okf" / "home-project"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"  {home}\n", encoding="utf-8")
    assert okf_home.home_state() == (str(home), None)


def test_pointer_tilde_expansion(monkeypatch, tmp_path):
    fake_home = tmp_path / "isolated-home"
    home = fake_home / "kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(okf_home.POINTER_ENV, "~/kb")
    assert okf_home.home_state() == (str(home), None)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("missing", okf_home.INVALID_MISSING),
        ("not-git", okf_home.INVALID_NOT_GIT),
        ("no-config", okf_home.INVALID_NO_CONFIG),
        ("relative", okf_home.INVALID_MISSING),
    ],
)
def test_invalid_pointer_reasons(monkeypatch, tmp_path, setup, reason):
    if setup == "missing":
        target = str(tmp_path / "nowhere")
    elif setup == "relative":
        target = "relative/path"
    else:
        home = tmp_path / "home-kb"
        home.mkdir()
        if setup == "no-config":
            (home / ".git").mkdir()
        else:  # not-git
            (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
        target = str(home)
    monkeypatch.setenv(okf_home.POINTER_ENV, target)
    assert okf_home.home_state() == (None, reason)


# --- resolve_inject (#91 §2 주입 3단) ---------------------------------------


def test_inject_project_config_wins(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"bundlePath": ".okf"})
    assert okf_home.resolve_inject(project)["target"] == str(project)


def test_inject_falls_back_to_home(monkeypatch, tmp_path):
    home = _home(tmp_path, {"bundlePath": ".okf"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    result = okf_home.resolve_inject(_project(tmp_path))
    assert (result["target"], result["scope"]) == (str(home), "home")


def test_inject_false_disables_home(monkeypatch, tmp_path):
    home = _home(tmp_path, {"inject": False})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert okf_home.resolve_inject(_project(tmp_path))["target"] is None


def test_inject_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    result = okf_home.resolve_inject(_project(tmp_path))
    assert result["target"] is None
    assert result["warning"] is not None


# --- 경계 (#145 U3) — generic 층은 feature 지식이 없다 ------------------------


def test_generic_module_has_no_study_symbols():
    # 분할 완료 계약: 캡처 정책·런타임 루트·메모리 경로 판정은 study_scope 소관.
    for moved in (
        "study_block",
        "resolve_capture",
        "home_capture_state",
        "enable_home_capture",
        "user_scope_runtime",
        "memory_dir_candidates",
        "is_memory_path",
    ):
        assert not hasattr(okf_home, moved), f"okf_home에 study 심볼 잔존: {moved}"
