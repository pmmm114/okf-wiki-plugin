"""study_hook — 메모리 경로 매칭·capture 분기·dedup·fail-fast 테스트 (S2, #74)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import okf_home
import okf_inbox
import study_hook

MEM = "/home/u/.claude/projects/proj/memory/MEMORY.md"
SCRIPT = Path(study_hook.__file__)


def _rt(project):
    """해소된 런타임 루트 — 인박스·원장이 실제로 사는 곳(#114)."""
    return okf_home.resolve_capture(project)["runtime_root"]


def _cfg(project, capture):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": []}}), encoding="utf-8"
    )


def test_review_appends_last_line(tmp_path):
    _cfg(tmp_path, "review")
    payload = {
        "tool_input": {"file_path": MEM, "content": "# Memory\n\n* 테스트 명령은 uv run pytest\n"}
    }
    message = study_hook.run(payload, tmp_path)
    cands = okf_inbox.list_candidates(_rt(tmp_path))
    assert len(cands) == 1
    assert cands[0]["snippet"] == "테스트 명령은 uv run pytest"
    assert cands[0]["source"] == MEM
    assert message and "인박스" in message


def test_capture_off_is_noop(tmp_path):
    _cfg(tmp_path, "off")
    payload = {"tool_input": {"file_path": MEM, "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(_rt(tmp_path)) == []


def test_study_block_absent_is_noop(tmp_path):
    (tmp_path / ".okf-wiki.json").write_text(json.dumps({"bundlePath": ".okf"}), encoding="utf-8")
    payload = {"tool_input": {"file_path": MEM, "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None


def test_non_memory_path_is_noop(tmp_path):
    _cfg(tmp_path, "review")
    payload = {"tool_input": {"file_path": str(tmp_path / ".okf" / "foo.md"), "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(_rt(tmp_path)) == []


def test_heading_only_content_is_noop(tmp_path):
    _cfg(tmp_path, "review")
    payload = {"tool_input": {"file_path": MEM, "content": "# Memory\n## Topic\n"}}
    assert study_hook.run(payload, tmp_path) is None


def test_resolved_memory_not_reappended(tmp_path):
    _cfg(tmp_path, "review")
    snippet = "already handled"
    okf_inbox.record(_rt(tmp_path), okf_inbox.content_hash(snippet)[:12], "discarded")
    payload = {"tool_input": {"file_path": MEM, "content": f"* {snippet}\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(_rt(tmp_path)) == []


def _run_hook(project, stdin: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        text=True,
        capture_output=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
    )


def test_main_bad_stdin_exit0_no_output(tmp_path):
    result = _run_hook(tmp_path, "{ not json")
    assert result.returncode == 0
    assert result.stdout == ""


def test_main_emits_additional_context(tmp_path):
    _cfg(tmp_path, "review")
    payload = json.dumps({"tool_input": {"file_path": MEM, "content": "* hello world\n"}})
    result = _run_hook(tmp_path, payload)
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "인박스" in out["hookSpecificOutput"]["additionalContext"]


# --- 홈 폴백 (#91 V2) -------------------------------------------------------


def _make_home(tmp_path, config: dict):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def test_scope_home_delegates_inbox_to_home(monkeypatch, tmp_path):
    # #91 §2 규칙 1 — 위임 repo의 캡처가 홈 inbox로 (레벨은 위임 블록 값)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    home = _make_home(tmp_path, {"study": {"capture": "off"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = tmp_path / "work"
    project.mkdir()
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review", "scope": "home"}}), encoding="utf-8"
    )
    payload = {"tool_input": {"file_path": MEM, "content": "* delegated knowledge\n"}}
    message = study_hook.run(payload, project)
    assert message and "인박스" in message
    assert len(okf_inbox.list_candidates(_rt(project))) == 1
    assert okf_inbox.list_candidates(project) == []  # 프로젝트 쪽엔 흔적 없음(#1)


def test_configless_dir_falls_back_to_home(monkeypatch, tmp_path):
    # R1 핵심 — 설정 없는 위치(비-repo 동치)에서도 캡처가 홈으로
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    home = _make_home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    payload = {"tool_input": {"file_path": MEM, "content": "* anywhere knowledge\n"}}
    message = study_hook.run(payload, scratch)
    assert message and "인박스" in message
    assert len(okf_inbox.list_candidates(_rt(scratch))) == 1


def test_capture_never_writes_to_home_repo(monkeypatch, tmp_path):
    # #114 U2 — 홈 폴백 캡처는 유저 스코프에만 적재, 홈 repo에 런타임을 만들지 않는다
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    home = _make_home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    payload = {"tool_input": {"file_path": MEM, "content": "* home-clean check\n"}}
    assert study_hook.run(payload, scratch)
    assert len(okf_inbox.list_candidates(okf_home.user_scope_runtime())) == 1
    assert not (home / ".okf-study").exists()  # 홈 repo 깨끗(런타임 미생성)


def test_invalid_pointer_is_silent_in_posttooluse(monkeypatch, tmp_path):
    # #9·#19 — PostToolUse 캡처 훅은 무효 포인터에도 무음 스킵
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    payload = {"tool_input": {"file_path": MEM, "content": "* x\n"}}
    assert study_hook.run(payload, scratch) is None


def test_auto_memory_directory_capture(monkeypatch, tmp_path):
    # #17 e2e — autoMemoryDirectory로 옮긴 메모리도 캡처된다
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    memdir = tmp_path / "custom-memory"
    (cfg / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(memdir)}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    _cfg(tmp_path, "review")
    payload = {
        "tool_input": {"file_path": str(memdir / "MEMORY.md"), "content": "* moved memory\n"}
    }
    message = study_hook.run(payload, tmp_path)
    assert message and "인박스" in message
    assert okf_inbox.list_candidates(_rt(tmp_path))[0]["snippet"] == "moved memory"
