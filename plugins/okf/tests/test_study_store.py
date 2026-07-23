"""study_store SQLite 스토어 + study_inbox fail-closed 가드 테스트 (U1 #130).

markdown/평문/jsonl 3종을 대체한 study.db의 CRUD·읽기무생성·이벤트, 그리고
``_sqlite3`` C확장 부재 파이썬에서 study_inbox가 크래시 없이 무동작(fail-closed)함을
고정한다.
"""

from __future__ import annotations

import study_inbox
import study_store

# --- 스토어 CRUD ------------------------------------------------------------


def test_candidate_crud_roundtrip(tmp_path):
    assert study_store.insert_candidate(tmp_path, "aa", "snip", "src", "2026-07-22") is True
    assert (
        study_store.insert_candidate(tmp_path, "aa", "snip", "src", "2026-07-22") is False
    )  # 재등장
    assert study_store.has_candidate(tmp_path, "aa") is True
    assert study_store.list_candidates(tmp_path) == [
        {"id": "aa", "date": "2026-07-22", "snippet": "snip", "source": "src", "recurrence": 2}
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

    ident = study_inbox.append(tmp_path, "snippet", "src")  # 크래시 없이 id 반환
    assert ident == study_inbox.content_hash("snippet")[:12]
    assert study_inbox.list_candidates(tmp_path) == []
    study_inbox.record(tmp_path, ident, "promoted")  # 무동작
    assert study_inbox.is_resolved(tmp_path, ident) is False
    assert study_inbox.read_journal(tmp_path) == []
    assert not (tmp_path / study_store.DB_NAME).exists()  # 아무 파일도 만들지 않는다


def test_bad_status_raises_even_when_sqlite_absent(monkeypatch, tmp_path):
    # status 검증은 영속 이전에 — 부재 환경에서도 계약 위반은 즉시 드러난다
    monkeypatch.setattr(study_store, "sqlite3", None)
    import pytest

    with pytest.raises(ValueError):
        study_inbox.record(tmp_path, "id", "weird")


# --- 시간축·승격 메타 (U3 #132) --------------------------------------------


def test_recurrence_counts_recapture(tmp_path):
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-22", captured_at="t0")
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-23", captured_at="t9")
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-24", captured_at="t9")
    meta = study_store.candidate_meta(tmp_path, "aa")
    assert meta["recurrence"] == 3  # 재캡처마다 카운터 증가(새 후보 X)
    assert meta["captured_at"] == "t0"  # 첫 캡처 시각 불변(valid-time 원점)
    assert len(study_store.list_candidates(tmp_path)) == 1


def test_bitemporal_timestamps_attached(tmp_path):
    study_inbox.append(tmp_path, "concept", "M.md", captured_at="2026-07-22T09:00:00")
    ident = study_inbox.content_hash("concept")[:12]
    meta = study_inbox.candidate_meta(tmp_path, ident)
    assert meta["captured_at"] == "2026-07-22T09:00:00"  # 넘긴 valid-time
    assert meta["ingested_at"] is not None  # transaction-time은 현재 시각


def test_supersedes_link_roundtrip(tmp_path):
    study_inbox.append(tmp_path, "new concept", "M.md")
    ident = study_inbox.content_hash("new concept")[:12]
    assert study_inbox.candidate_meta(tmp_path, ident)["supersedes"] is None
    study_inbox.set_supersedes(tmp_path, ident, "old-concept-id")
    assert study_inbox.candidate_meta(tmp_path, ident)["supersedes"] == "old-concept-id"


def test_invalidate_does_not_delete(tmp_path):
    study_inbox.record(tmp_path, "id1", "promoted", ".okf/x.md")
    study_inbox.invalidate(tmp_path, "id1")
    assert study_inbox.is_resolved(tmp_path, "id1") is True  # dedup 판정엔 그대로(재부상 계속 차단)
    assert study_store.resolution_invalidated_at(tmp_path, "id1") is not None  # 무효화 시각 보존


def test_migration_adds_columns_to_old_db(tmp_path):
    # 구 유닛(U1/U2) 스키마 db가 U3 코드에서 컬럼 보강돼 동작한다(#132)
    import sqlite3

    db = tmp_path / study_store.DB_NAME
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE candidate (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,"
        " snippet TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', captured_date TEXT NOT NULL);"
        " CREATE TABLE resolution (id TEXT PRIMARY KEY, status TEXT NOT NULL, ref TEXT);"
    )
    conn.execute(
        "INSERT INTO candidate(id, snippet, source, captured_date)"
        " VALUES('old','s','src','2026-07-01')"
    )
    conn.commit()
    conn.close()

    assert study_store.list_candidates(tmp_path) == [
        {"id": "old", "date": "2026-07-01", "snippet": "s", "source": "src", "recurrence": 1}
    ]
    assert (
        study_store.insert_candidate(tmp_path, "new", "n", "src", "2026-07-02", captured_at="t")
        is True
    )
    assert study_store.candidate_meta(tmp_path, "new")["captured_at"] == "t"
