"""레거시 markdown 리더 테스트 (U5, #134)."""

from __future__ import annotations

import okf_inbox
import study_legacy


def _write_inbox(directory, entries):
    """entries: [(snippet, source, date)] → 옛 inbox.md 포맷으로 쓴다."""
    lines = ["# Study Inbox", ""]
    for snippet, source, date in entries:
        ident = okf_inbox.content_hash(snippet)[:12]
        lines.append(f"## {date}")
        lines.append(f"* **memory**: {snippet} — {source} <!-- id:{ident} -->")
    (directory / study_legacy.INBOX_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_has_legacy_detects_files(tmp_path):
    assert study_legacy.has_legacy(tmp_path) is False
    (tmp_path / study_legacy.LEDGER_NAME).write_text("x promoted\n", encoding="utf-8")
    assert study_legacy.has_legacy(tmp_path) is True


def test_read_candidates_parses_old_bullets(tmp_path):
    _write_inbox(tmp_path, [("테스트 명령", "MEMORY.md", "2026-07-01")])
    cands = study_legacy.read_candidates(tmp_path)
    assert cands == [
        {
            "id": okf_inbox.content_hash("테스트 명령")[:12],
            "date": "2026-07-01",
            "snippet": "테스트 명령",
            "source": "MEMORY.md",
        }
    ]


def test_read_candidates_snippet_with_separator(tmp_path):
    _write_inbox(tmp_path, [("a — b — c", "MEMORY.md", "2026-07-01")])
    cand = study_legacy.read_candidates(tmp_path)[0]
    assert cand["snippet"] == "a — b — c" and cand["source"] == "MEMORY.md"


def test_read_resolutions_parses_ledger(tmp_path):
    (tmp_path / study_legacy.LEDGER_NAME).write_text(
        "aaaa11112222 promoted .okf/x.md\nbbbb33334444 discarded\n", encoding="utf-8"
    )
    assert study_legacy.read_resolutions(tmp_path) == [
        ("aaaa11112222", "promoted", ".okf/x.md"),
        ("bbbb33334444", "discarded", None),
    ]


def test_remove_legacy_deletes_files(tmp_path):
    _write_inbox(tmp_path, [("x", "M", "2026-07-01")])
    (tmp_path / study_legacy.LEDGER_NAME).write_text("id promoted\n", encoding="utf-8")
    (tmp_path / study_legacy.JOURNAL_NAME).write_text("{}\n", encoding="utf-8")
    removed = study_legacy.remove_legacy(tmp_path)
    assert set(removed) == {"inbox.md", "ledger", "journal.jsonl"}
    assert study_legacy.has_legacy(tmp_path) is False
