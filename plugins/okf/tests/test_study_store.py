"""study_store SQLite 스토어 + okf_inbox fail-closed 가드 테스트 (U1 #130).

markdown/평문/jsonl 3종을 대체한 study.db의 CRUD·읽기무생성·이벤트, 그리고
``_sqlite3`` C확장 부재 파이썬에서 okf_inbox가 크래시 없이 무동작(fail-closed)함을
고정한다.
"""

from __future__ import annotations

import okf_inbox
import study_store

# --- 스토어 CRUD ------------------------------------------------------------


def test_candidate_crud_roundtrip(tmp_path):
    assert study_store.insert_candidate(tmp_path, "aa", "snip", "src", "2026-07-22") is True
    assert study_store.insert_candidate(tmp_path, "aa", "snip", "src", "2026-07-22") is False  # dup
    assert study_store.has_candidate(tmp_path, "aa") is True
    assert study_store.list_candidates(tmp_path) == [
        {"id": "aa", "date": "2026-07-22", "snippet": "snip", "source": "src"}
    ]
    assert study_store.delete_candidates(tmp_path, ["aa"]) == ["aa"]
    assert study_store.list_candidates(tmp_path) == []


def test_resolution_dedup(tmp_path):
    assert study_store.insert_resolution(tmp_path, "id1", "promoted", ".okf/x.md") is True
    assert study_store.insert_resolution(tmp_path, "id1", "discarded", None) is False  # PK 고정
    assert study_store.has_resolution(tmp_path, "id1") is True
    assert study_store.list_resolutions(tmp_path) == [("id1", "promoted", ".okf/x.md")]


def test_event_roundtrip_and_extra(tmp_path):
    study_store.append_event(tmp_path, "2026-07-22T10:00:00", "capture", "aa", {"source": "M.md"})
    study_store.append_event(tmp_path, "2026-07-22T11:00:00", "promoted", "aa", None)
    events = study_store.read_events(tmp_path)
    assert events[0] == {
        "ts": "2026-07-22T10:00:00",
        "action": "capture",
        "id": "aa",
        "source": "M.md",
    }
    assert events[1] == {"ts": "2026-07-22T11:00:00", "action": "promoted", "id": "aa"}
    assert study_store.read_events(tmp_path, limit=1) == [events[1]]  # 최신 N


def test_read_on_missing_db_does_not_create(tmp_path):
    runtime = tmp_path / "empty"
    assert study_store.list_candidates(runtime) == []
    assert study_store.read_events(runtime) == []
    assert study_store.has_resolution(runtime, "x") is False
    assert study_store.has_candidate(runtime, "x") is False
    assert not (runtime / study_store.DB_NAME).exists()  # 읽기는 파일을 만들지 않는다


# --- fail-closed 가드 (_sqlite3 부재) --------------------------------------


def test_sqlite_absent_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(study_store, "sqlite3", None)  # C확장 부재 시뮬레이션
    assert study_store.available() is False

    ident = okf_inbox.append(tmp_path, "snippet", "src")  # 크래시 없이 id 반환
    assert ident == okf_inbox.content_hash("snippet")[:12]
    assert okf_inbox.list_candidates(tmp_path) == []
    okf_inbox.record(tmp_path, ident, "promoted")  # 무동작
    assert okf_inbox.is_resolved(tmp_path, ident) is False
    assert okf_inbox.read_journal(tmp_path) == []
    assert not (tmp_path / study_store.DB_NAME).exists()  # 아무 파일도 만들지 않는다


def test_bad_status_raises_even_when_sqlite_absent(monkeypatch, tmp_path):
    # status 검증은 영속 이전에 — 부재 환경에서도 계약 위반은 즉시 드러난다
    monkeypatch.setattr(study_store, "sqlite3", None)
    import pytest

    with pytest.raises(ValueError):
        okf_inbox.record(tmp_path, "id", "weird")
