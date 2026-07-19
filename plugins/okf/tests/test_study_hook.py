"""study_hook — 메모리 경로 매칭·capture 분기·dedup·fail-fast 테스트 (S2, #74)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import okf_inbox
import study_hook

MEM = "/home/u/.claude/projects/proj/memory/MEMORY.md"
SCRIPT = Path(study_hook.__file__)


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
    cands = okf_inbox.list_candidates(tmp_path)
    assert len(cands) == 1
    assert cands[0]["snippet"] == "테스트 명령은 uv run pytest"
    assert cands[0]["source"] == MEM
    assert message and "인박스" in message


def test_capture_off_is_noop(tmp_path):
    _cfg(tmp_path, "off")
    payload = {"tool_input": {"file_path": MEM, "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(tmp_path) == []


def test_study_block_absent_is_noop(tmp_path):
    (tmp_path / ".okf-wiki.json").write_text(json.dumps({"bundlePath": ".okf"}), encoding="utf-8")
    payload = {"tool_input": {"file_path": MEM, "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None


def test_non_memory_path_is_noop(tmp_path):
    _cfg(tmp_path, "review")
    payload = {"tool_input": {"file_path": str(tmp_path / ".okf" / "foo.md"), "content": "* x\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(tmp_path) == []


def test_heading_only_content_is_noop(tmp_path):
    _cfg(tmp_path, "review")
    payload = {"tool_input": {"file_path": MEM, "content": "# Memory\n## Topic\n"}}
    assert study_hook.run(payload, tmp_path) is None


def test_resolved_memory_not_reappended(tmp_path):
    _cfg(tmp_path, "review")
    snippet = "already handled"
    okf_inbox.record(tmp_path, okf_inbox.content_hash(snippet)[:12], "discarded")
    payload = {"tool_input": {"file_path": MEM, "content": f"* {snippet}\n"}}
    assert study_hook.run(payload, tmp_path) is None
    assert okf_inbox.list_candidates(tmp_path) == []


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
