"""study_inbox — 적재·목록·선택 삭제·clear·resolved 원장 테스트 (S3, #75)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import study_inbox
import study_store

PLUGIN = Path(__file__).resolve().parent.parent
FIELD_DOCS = (PLUGIN / "commands" / "study.md", PLUGIN / "skills" / "okf" / "SKILL.md")
# 후보 필드 목록이 뒤따르는 문장 — 목록 자리를 찾는 앵커
FIELD_LIST_MARKER = "후보에는 축 값이 없다"


def test_append_and_list_roundtrip(tmp_path):
    ident = study_inbox.append(
        tmp_path, "테스트 명령은 uv run pytest", "MEMORY.md", date="2026-07-19"
    )
    cands = study_inbox.list_candidates(tmp_path)
    assert len(cands) == 1
    assert cands[0] == {
        "id": ident,
        "date": "2026-07-19",
        "snippet": "테스트 명령은 uv run pytest",
        "source": "MEMORY.md",
        "recurrence": 1,
    }


def test_docs_list_actual_candidate_fields(tmp_path):
    """후보 필드를 열거하는 문서의 **목록**이 실제 스키마와 정확히 같다.

    문서가 없는 필드를 전제하면 **실행 불가능한 계약**이 된다 — 실제로 커맨드가
    `<topic>`·`--type`으로 "후보를 한정한다"고 쓰는 동안 후보에는 그 축이 없었다.
    반대로 스키마에 필드가 늘었는데 문서가 옛 목록을 유지하는 것도 같은 괴리다.

    문서 전체에서 필드명을 찾으면 안 된다 — 다른 절이 같은 이름을 언급하기만 해도
    목록에서 빠진 것을 놓친다(감도 실증에서 실제로 걸렸다). 목록 자리만 떼어
    집합으로 비교하므로 누락도 과잉도 red다.
    """
    study_inbox.append(tmp_path, "스니펫", "MEMORY.md", date="2026-07-19")
    actual = set(study_inbox.list_candidates(tmp_path)[0])
    checked = 0
    for doc in FIELD_DOCS:
        text = doc.read_text(encoding="utf-8")
        found = text.find(FIELD_LIST_MARKER)
        if found < 0:  # 필드를 열거하는 문서만 대상
            continue
        # 마커 직후 좁은 창만 본다 — 넓게 잡으면 뒤쪽 절의 백틱 토큰까지 딸려 온다
        window = text[found : found + len(FIELD_LIST_MARKER) + 120]
        listed = set(re.findall(r"`([a-z_]+)`", window))
        assert listed == actual, f"{doc.name}의 후보 필드 목록이 스키마와 다르다: {listed}"
        checked += 1
    assert checked, "후보 필드를 열거하는 문서가 하나도 없다 — 계약이 문서화되지 않았다"


def test_id_is_content_hash_and_stable(tmp_path):
    first = study_inbox.append(tmp_path, "same snippet", "s1", date="2026-07-19")
    study_inbox.clear(tmp_path)
    second = study_inbox.append(tmp_path, "same snippet", "other", date="2026-07-20")
    assert first == second  # id는 내용만으로 결정(출처·날짜 무관)
    assert first == study_inbox.content_hash("same snippet")[:12]


def test_append_dedup_same_id(tmp_path):
    study_inbox.append(tmp_path, "dup", "s", date="2026-07-19")
    study_inbox.append(tmp_path, "dup", "s", date="2026-07-19")
    assert len(study_inbox.list_candidates(tmp_path)) == 1


def test_concurrent_append_no_loss(tmp_path):
    # #91 #6 — vault inbox 공유 핫스팟: 동시 append에도 후보 유실이 없어야 한다
    import threading

    def worker(index: int) -> None:
        study_inbox.append(tmp_path, f"snippet {index}", "src", date="2026-07-20")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(study_inbox.list_candidates(tmp_path)) == 16


def test_newest_first_across_dates(tmp_path):
    study_inbox.append(tmp_path, "old", "s", date="2026-07-18")
    study_inbox.append(tmp_path, "new", "s", date="2026-07-19")
    assert [c["snippet"] for c in study_inbox.list_candidates(tmp_path)] == ["new", "old"]


def test_snippet_with_separator_roundtrips(tmp_path):
    study_inbox.append(tmp_path, "a — b — c", "MEMORY.md", date="2026-07-19")
    cand = study_inbox.list_candidates(tmp_path)[0]
    assert cand["snippet"] == "a — b — c"
    assert cand["source"] == "MEMORY.md"


def test_drop_removes_selected(tmp_path):
    a = study_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    b = study_inbox.append(tmp_path, "b", "s", date="2026-07-19")
    assert study_inbox.drop(tmp_path, [a]) == [a]
    assert [c["id"] for c in study_inbox.list_candidates(tmp_path)] == [b]


def test_clear_empties_and_removes_file(tmp_path):
    # tmp_path는 런타임 루트 — inbox는 <runtime>/inbox.md 직접(#114, .okf-study 세그먼트 없음)
    study_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    assert len(study_inbox.clear(tmp_path)) == 1
    assert study_inbox.list_candidates(tmp_path) == []
    assert not (tmp_path / "inbox.md").exists()


def test_drop_last_removes_file(tmp_path):
    a = study_inbox.append(tmp_path, "a", "s", date="2026-07-19")
    study_inbox.drop(tmp_path, [a])
    assert not (tmp_path / "inbox.md").exists()


def test_ledger_record_and_query(tmp_path):
    assert not study_inbox.is_resolved(tmp_path, "abc123")
    study_inbox.record(tmp_path, "abc123", "promoted", ".okf/engine/x.md")
    assert study_inbox.is_resolved(tmp_path, "abc123")


def test_ledger_dedup_and_bad_status(tmp_path):
    study_inbox.record(tmp_path, "id1", "discarded")
    study_inbox.record(tmp_path, "id1", "discarded")  # 재기록 무시 (resolution PK)
    resolved = [r for r in study_store.list_resolutions(tmp_path) if r[0] == "id1"]
    assert len(resolved) == 1
    with pytest.raises(ValueError):
        study_inbox.record(tmp_path, "id2", "weird")


# --- 이벤트 저널 (#114 U5) — 순서·시각·이력 -----------------------------------


def test_journal_records_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox, "_now", lambda: "2026-07-22T10:00:00")
    ident = study_inbox.append(tmp_path, "snippet", "MEMORY.md")
    events = study_inbox.read_journal(tmp_path)
    assert len(events) == 1
    assert events[0] == {
        "ts": "2026-07-22T10:00:00",
        "action": "capture",
        "id": ident,
        "source": "MEMORY.md",
    }


def test_journal_records_promote_and_discard(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox, "_now", lambda: "2026-07-22T11:00:00")
    study_inbox.record(tmp_path, "aaaa11112222", "promoted", ref=".okf/x.md")
    study_inbox.record(tmp_path, "bbbb33334444", "discarded")
    events = study_inbox.read_journal(tmp_path)
    assert [e["action"] for e in events] == ["promoted", "discarded"]
    assert events[0]["ref"] == ".okf/x.md"
    assert "ref" not in events[1]  # None extra는 기록하지 않음


def test_journal_dedup_capture_not_doubled(tmp_path):
    study_inbox.append(tmp_path, "dup", "s")
    study_inbox.append(tmp_path, "dup", "s")  # 동일 id 재적재 = 저널에 안 남음
    captures = [e for e in study_inbox.read_journal(tmp_path) if e["action"] == "capture"]
    assert len(captures) == 1


def test_journal_limit_returns_latest(tmp_path):
    for i in range(3):
        study_inbox.append(tmp_path, f"line {i}", "s")
    latest = study_inbox.read_journal(tmp_path, limit=2)
    assert len(latest) == 2 and [e["source"] for e in latest] == ["s", "s"]
    assert study_inbox.read_journal(tmp_path / "nope") == []  # 부재 = 빈 목록
