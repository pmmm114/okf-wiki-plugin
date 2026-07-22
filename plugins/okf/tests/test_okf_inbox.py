"""okf_inbox — 적재·목록·선택 삭제·clear·resolved 원장 테스트 (S3, #75)."""

from __future__ import annotations

import okf_inbox
import pytest


def test_append_and_list_roundtrip(tmp_path):
    ident = okf_inbox.append(
        tmp_path, "테스트 명령은 uv run pytest", "MEMORY.md", date="2026-07-19"
    )
    cands = okf_inbox.list_candidates(tmp_path)
    assert len(cands) == 1
    assert cands[0] == {
        "id": ident,
        "date": "2026-07-19",
        "snippet": "테스트 명령은 uv run pytest",
        "source": "MEMORY.md",
    }


def test_id_is_content_hash_and_stable(tmp_path):
    first = okf_inbox.append(tmp_path, "same snippet", "s1", date="2026-07-19")
    okf_inbox.clear(tmp_path)
    second = okf_inbox.append(tmp_path, "same snippet", "other", date="2026-07-20")
    assert first == second  # id는 내용만으로 결정(출처·날짜 무관)
    assert first == okf_inbox.content_hash("same snippet")[:12]


def test_append_dedup_same_id(tmp_path):
    okf_inbox.append(tmp_path, "dup", "s", date="2026-07-19")
    okf_inbox.append(tmp_path, "dup", "s", date="2026-07-19")
    assert len(okf_inbox.list_candidates(tmp_path)) == 1


def test_concurrent_append_no_loss(tmp_path):
    # #91 #6 — 홈 inbox 공유 핫스팟: 동시 append에도 후보 유실이 없어야 한다
    import threading

    def worker(index: int) -> None:
        okf_inbox.append(tmp_path, f"snippet {index}", "src", date="2026-07-20")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(okf_inbox.list_candidates(tmp_path)) == 16


def test_newest_first_across_dates(tmp_path):
    okf_inbox.append(tmp_path, "old", "s", date="2026-07-18")
    okf_inbox.append(tmp_path, "new", "s", date="2026-07-19")
    assert [c["snippet"] for c in okf_inbox.list_candidates(tmp_path)] == ["new", "old"]


def test_snippet_with_separator_roundtrips(tmp_path):
    okf_inbox.append(tmp_path, "a — b — c", "MEMORY.md", date="2026-07-19")
    cand = okf_inbox.list_candidates(tmp_path)[0]
    assert cand["snippet"] == "a — b — c"
    assert cand["source"] == "MEMORY.md"


def test_drop_removes_selected(tmp_path):
    a = okf_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    b = okf_inbox.append(tmp_path, "b", "s", date="2026-07-19")
    assert okf_inbox.drop(tmp_path, [a]) == [a]
    assert [c["id"] for c in okf_inbox.list_candidates(tmp_path)] == [b]


def test_clear_empties_and_removes_file(tmp_path):
    # tmp_path는 런타임 루트 — inbox는 <runtime>/inbox.md 직접(#114, .okf-study 세그먼트 없음)
    okf_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    assert len(okf_inbox.clear(tmp_path)) == 1
    assert okf_inbox.list_candidates(tmp_path) == []
    assert not (tmp_path / "inbox.md").exists()


def test_drop_last_removes_file(tmp_path):
    a = okf_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    okf_inbox.drop(tmp_path, [a])
    assert not (tmp_path / "inbox.md").exists()


def test_ledger_record_and_query(tmp_path):
    assert not okf_inbox.is_resolved(tmp_path, "abc123")
    okf_inbox.record(tmp_path, "abc123", "promoted", ".okf/engine/x.md")
    assert okf_inbox.is_resolved(tmp_path, "abc123")


def test_ledger_dedup_and_bad_status(tmp_path):
    okf_inbox.record(tmp_path, "id1", "discarded")
    okf_inbox.record(tmp_path, "id1", "discarded")  # 재기록 무시
    ledger = (tmp_path / "ledger").read_text(encoding="utf-8")
    assert ledger.count("id1") == 1
    with pytest.raises(ValueError):
        okf_inbox.record(tmp_path, "id2", "weird")


# --- 이벤트 저널 (#114 U5) — 순서·시각·이력 -----------------------------------


def test_journal_records_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(okf_inbox, "_now", lambda: "2026-07-22T10:00:00")
    ident = okf_inbox.append(tmp_path, "snippet", "MEMORY.md")
    events = okf_inbox.read_journal(tmp_path)
    assert len(events) == 1
    assert events[0] == {
        "ts": "2026-07-22T10:00:00",
        "action": "capture",
        "id": ident,
        "source": "MEMORY.md",
    }


def test_journal_records_promote_and_discard(monkeypatch, tmp_path):
    monkeypatch.setattr(okf_inbox, "_now", lambda: "2026-07-22T11:00:00")
    okf_inbox.record(tmp_path, "aaaa11112222", "promoted", ref=".okf/x.md")
    okf_inbox.record(tmp_path, "bbbb33334444", "discarded")
    events = okf_inbox.read_journal(tmp_path)
    assert [e["action"] for e in events] == ["promoted", "discarded"]
    assert events[0]["ref"] == ".okf/x.md"
    assert "ref" not in events[1]  # None extra는 기록하지 않음


def test_journal_dedup_capture_not_doubled(tmp_path):
    okf_inbox.append(tmp_path, "dup", "s")
    okf_inbox.append(tmp_path, "dup", "s")  # 동일 id 재적재 = 저널에 안 남음
    captures = [e for e in okf_inbox.read_journal(tmp_path) if e["action"] == "capture"]
    assert len(captures) == 1


def test_journal_limit_returns_latest(tmp_path):
    for i in range(3):
        okf_inbox.append(tmp_path, f"line {i}", "s")
    latest = okf_inbox.read_journal(tmp_path, limit=2)
    assert len(latest) == 2 and [e["source"] for e in latest] == ["s", "s"]
    assert okf_inbox.read_journal(tmp_path / "nope") == []  # 부재 = 빈 목록
