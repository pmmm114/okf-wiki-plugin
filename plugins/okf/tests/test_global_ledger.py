"""전역 원장 write-through·양쪽 조회 테스트 (#91 V4, 매트릭스 #2 시간축).

promote/discard가 홈 원장에도 append되고, is_resolved가 활성∪홈을 조회해
"repo A에서 처리한 스니펫이 다른 위치에서 재큐"되는 구멍을 막는지 고정한다.
"""

from __future__ import annotations

import json

import okf_home
import okf_inbox
import pytest
import study_hook


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))


def _valid_home(tmp_path, capture="review"):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture}}), encoding="utf-8"
    )
    return home


def _ledger_text(project):
    path = project / okf_inbox.STUDY_DIR / okf_inbox.LEDGER_NAME
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_record_writes_through_to_home(monkeypatch, tmp_path):
    home = _valid_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = tmp_path / "repo-a"
    project.mkdir()
    okf_inbox.record(project, "abc123def456", "promoted", ref=".okf/x.md")
    assert "abc123def456 promoted .okf/x.md" in _ledger_text(project)  # 원 스코프 = 정본
    assert "abc123def456 promoted .okf/x.md" in _ledger_text(home)  # write-through


def test_is_resolved_consults_home_ledger(monkeypatch, tmp_path):
    home = _valid_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.record(home, "feedbeefcafe", "discarded")
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert okf_inbox.is_resolved(other, "feedbeefcafe")  # 홈 원장 경유


def test_time_axis_requeue_blocked_end_to_end(monkeypatch, tmp_path):
    # repo A에서 promote → 다른 위치(홈 폴백 스코프)의 재캡처가 원장에 막힌다
    home = _valid_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    snippet = "promoted knowledge"
    ident = okf_inbox.content_hash(snippet)[:12]
    okf_inbox.record(repo_a, ident, "promoted")  # write-through로 홈 원장에도 기록
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    payload = {
        "tool_input": {
            "file_path": "/home/u/.claude/projects/p/memory/MEMORY.md",
            "content": f"* {snippet}\n",
        }
    }
    assert study_hook.run(payload, scratch) is None  # 재큐 없음
    assert okf_inbox.list_candidates(home) == []


def test_record_on_home_itself_no_duplicate(monkeypatch, tmp_path):
    home = _valid_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.record(home, "aaaa11112222", "promoted")
    assert _ledger_text(home).count("aaaa11112222") == 1  # 자기 자신 이중 기록 없음


def test_no_pointer_keeps_local_only(tmp_path):
    project = tmp_path / "repo-a"
    project.mkdir()
    okf_inbox.record(project, "bbbb33334444", "discarded")
    assert "bbbb33334444" in _ledger_text(project)
    assert okf_inbox.is_resolved(project, "bbbb33334444")  # 현행 동작 무회귀
