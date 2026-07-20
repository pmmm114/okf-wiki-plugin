"""okf_home — 포인터·스코프 해소·캡처 입구 판정 테스트 (#91 V2).

매트릭스 대응: #1·#3(단일 스코프), #5(주입 전용 repo), #9(침묵), #10(순환 선취),
#13(반쪽 상태), #14(자기 위임), #15~#17(캡처 입구), #18(scope 의미 한정).
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


# --- resolve_capture (#91 §2 캡처 4단) --------------------------------------


def test_rule2_project_block_wins(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"study": {"capture": "review"}})
    scope = okf_home.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (
        str(project),
        "review",
        "project",
    )


def test_rule2_capture_off_silences_home_too(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"study": {"capture": "off"}})
    scope = okf_home.resolve_capture(project)
    assert scope["scope"] == "project"
    assert scope["capture"] == "off"  # 명시가 이긴다 — 홈이 auto여도 침묵


def test_rule1_scope_home_delegates(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "off"}})  # 레벨은 블록 값임을 증명
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "home"}})
    scope = okf_home.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (str(home), "review", "home")


def test_rule1_scope_home_without_capture_is_off(monkeypatch, tmp_path):
    # #18 — scope는 목적지 위임 키일 뿐 활성화 키가 아니다
    home = _home(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"study": {"scope": "home"}})
    scope = okf_home.resolve_capture(project)
    assert scope["capture"] == "off"
    assert scope["scope"] == "home"  # 목적지는 홈이되 비활성


def test_rule1_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "home"}})
    scope = okf_home.resolve_capture(project)
    assert scope["target"] is None
    assert "무효" in scope["warning"] and "doctor" in scope["warning"]


def test_rule1_no_pointer_is_silent_noop(tmp_path):
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "home"}})
    scope = okf_home.resolve_capture(project)
    assert scope["target"] is None
    assert scope["warning"] is None  # 옵트인 안 한 협업자 — 무음


def test_rule3_fallback_to_home(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path)  # 설정 없음
    scope = okf_home.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (str(home), "review", "home")


def test_rule3_inject_only_project_falls_home(monkeypatch, tmp_path):
    # #5 — 주입 전용 얇은 설정(study 블록 없음)은 캡처를 홈으로 폴백
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"bundlePath": ".okf"})
    scope = okf_home.resolve_capture(project)
    assert scope["target"] == str(home)


def test_rule3_half_state_home_is_normal_silence(monkeypatch, tmp_path):
    # #13 — 홈에 study 블록 없음 = 주입 전용 홈(정상, 무경고)
    home = _home(tmp_path, {"bundlePath": ".okf"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scope = okf_home.resolve_capture(_project(tmp_path))
    assert scope["target"] is None
    assert scope["warning"] is None


def test_rule3_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    scope = okf_home.resolve_capture(_project(tmp_path))
    assert scope["target"] is None
    assert scope["warning"] is not None


def test_self_delegation_is_harmless(monkeypatch, tmp_path):
    # #14 — 홈 repo 자신의 scope:"home"은 자기 자신으로의 위임
    home = _home(tmp_path, {"study": {"capture": "review", "scope": "home"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scope = okf_home.resolve_capture(home)
    assert (scope["target"], scope["scope"]) == (str(home), "home")


def test_home_equals_project_rule2_precedes(monkeypatch, tmp_path):
    # #10 — 홈=현재 프로젝트여도 규칙 2가 선취(동작 동일)
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scope = okf_home.resolve_capture(home)
    assert (scope["target"], scope["scope"]) == (str(home), "project")


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


# --- 캡처 입구 판정 (#15~#17) ----------------------------------------------


def test_entrance_legacy_default_path(tmp_path):
    path = "/home/u/.claude/projects/proj/memory/MEMORY.md"
    assert okf_home.is_memory_path(path, {}, tmp_path)


def test_entrance_claude_config_dir(monkeypatch, tmp_path):
    # #16 — 리터럴 `.claude`가 아닌 config dir에서도 캡처 성립
    cfg = tmp_path / "custom-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    path = str(cfg / "projects" / "proj" / "memory" / "MEMORY.md")
    assert okf_home.is_memory_path(path, {}, tmp_path)


def test_entrance_auto_memory_directory_user_settings(monkeypatch, tmp_path):
    # #17 — 공식 autoMemoryDirectory 설정 반영
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    memdir = tmp_path / "my-memory"
    (cfg / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(memdir)}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert okf_home.is_memory_path(str(memdir / "MEMORY.md"), {}, tmp_path)
    assert okf_home.is_memory_path(str(memdir / "sub" / "notes.md"), {}, tmp_path)
    assert not okf_home.is_memory_path(str(tmp_path / "elsewhere" / "x.md"), {}, tmp_path)


def test_entrance_project_settings(monkeypatch, tmp_path):
    project = _project(tmp_path)
    memdir = tmp_path / "proj-memory"
    settings = project / ".claude"
    settings.mkdir()
    (settings / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(memdir)}), encoding="utf-8"
    )
    assert okf_home.is_memory_path(str(memdir / "MEMORY.md"), {}, project)


def test_entrance_transcript_sibling(tmp_path):
    payload = {"transcript_path": str(tmp_path / "projdir" / "session.jsonl")}
    path = str(tmp_path / "projdir" / "memory" / "MEMORY.md")
    assert okf_home.is_memory_path(path, payload, tmp_path)


def test_entrance_home_pattern_override(monkeypatch, tmp_path):
    home = _home(
        tmp_path, {"study": {"capture": "review", "memoryPathPattern": r"/weird-mem/.*\.md$"}}
    )
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert okf_home.is_memory_path("/opt/weird-mem/x.md", {}, tmp_path)


def test_entrance_invalid_pattern_tolerated(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path, {"study": {"capture": "review", "memoryPathPattern": "("}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert not okf_home.is_memory_path("/opt/anything/x.md", {}, tmp_path)
    assert "memoryPathPattern" in capsys.readouterr().err


def test_entrance_non_md_rejected(tmp_path):
    assert not okf_home.is_memory_path("/home/u/.claude/projects/p/memory/m.txt", {}, tmp_path)
