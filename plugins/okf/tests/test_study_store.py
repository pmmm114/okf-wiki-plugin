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
    assert study_store.candidate_groups(runtime) == []
    assert study_store.list_candidates_by_source(runtime, "M.md") == []
    assert study_store.read_events(runtime) == []
    assert study_store.has_resolution(runtime, "x") is False
    assert study_store.has_candidate(runtime, "x") is False
    assert not (runtime / study_store.DB_NAME).exists()  # 읽기는 파일을 만들지 않는다


# --- 그룹 집계·필터 (#383) --------------------------------------------------


def _seed_groups(runtime):
    """그룹 순서의 두 함정을 동시에 밟는 배치 — a.md는 **가장 오래된 후보**(적재 1번)와
    **가장 최신 후보**를 함께 갖고, b.md는 같은 최신 날짜를 a.md보다 **먼저** 적재했다.
    `MAX(날짜)`만 보면 a·b 순서가 갈리고, `MIN(seq)`만 보면 a가 옛 후보로 앞질러 온다.
    """
    study_store.insert_candidate(runtime, "a1", "s1", "/mem/a.md", "2026-07-18")
    study_store.insert_candidate(runtime, "b1", "s2", "/mem/b.md", "2026-07-20")
    study_store.insert_candidate(runtime, "c1", "s3", "/mem/c.md", "2026-07-19")
    study_store.insert_candidate(runtime, "a2", "s4", "/mem/a.md", "2026-07-20")


def test_candidate_groups_order_by_group_lead_candidate(tmp_path):
    # 그룹 순서 = 평탄 뷰에서 그 그룹이 처음 나오는 자리 = 최신 날짜 안에서의 최소 seq
    _seed_groups(tmp_path)
    assert study_store.candidate_groups(tmp_path) == [
        {"source": "/mem/b.md", "count": 1},
        {"source": "/mem/a.md", "count": 2},
        {"source": "/mem/c.md", "count": 1},
    ]


def test_list_candidates_by_source_filters_exactly(tmp_path):
    _seed_groups(tmp_path)
    flat = study_store.list_candidates(tmp_path)
    picked = study_store.list_candidates_by_source(tmp_path, "/mem/a.md")
    assert picked == [c for c in flat if c["source"] == "/mem/a.md"]  # shape·정렬 동일
    assert study_store.list_candidates_by_source(tmp_path, "/mem/a") == []  # 접두 아닌 정확 일치


# --- fail-closed 가드 (_sqlite3 부재) --------------------------------------


def test_sqlite_absent_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(study_store, "sqlite3", None)  # C확장 부재 시뮬레이션
    assert study_store.available() is False

    ident = study_inbox.append(tmp_path, "snippet", "src")  # 크래시 없이 id 반환
    assert ident == study_inbox.content_hash("snippet")[:12]
    assert study_inbox.list_candidates(tmp_path) == []
    assert study_inbox.candidate_groups(tmp_path) == []
    assert study_inbox.list_candidates_by_source(tmp_path, "src") == []
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


def _candidate_columns(runtime, ident):
    """시간축·layer 컬럼 직접 조회 — 기록 전용 provenance라 공개 API가 노출하지 않는다."""
    import sqlite3

    conn = sqlite3.connect(str(runtime / study_store.DB_NAME))
    try:
        row = conn.execute(
            "SELECT captured_at, ingested_at, recurrence, layer FROM candidate WHERE id=?",
            (ident,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return {"captured_at": row[0], "ingested_at": row[1], "recurrence": row[2], "layer": row[3]}


def test_recurrence_counts_recapture(tmp_path):
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-22", captured_at="t0")
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-23", captured_at="t9")
    study_store.insert_candidate(tmp_path, "aa", "s", "src", "2026-07-24", captured_at="t9")
    meta = _candidate_columns(tmp_path, "aa")
    assert meta["recurrence"] == 3  # 재캡처마다 카운터 증가(새 후보 X)
    assert meta["captured_at"] == "t0"  # 첫 캡처 시각 불변(valid-time 원점)
    assert len(study_store.list_candidates(tmp_path)) == 1


def test_recapture_refreshes_source_and_ingested_at(tmp_path):
    # U1(#255): rename·이동 후 재캡처된 후보가 죽은 경로에 영구 귀속되지 않는다 —
    # transaction-time 계열(source·ingested_at)은 최근 캡처, valid-time 계열
    # (captured_at·captured_date)은 첫 캡처가 정본.
    study_store.insert_candidate(
        tmp_path, "aa", "s", "/mem/old-name.md", "2026-07-22", captured_at="t0", ingested_at="i0"
    )
    study_store.insert_candidate(
        tmp_path, "aa", "s", "/mem/new-name.md", "2026-07-23", captured_at="t9", ingested_at="i9"
    )
    cand = study_store.list_candidates(tmp_path)[0]
    meta = _candidate_columns(tmp_path, "aa")
    assert cand["source"] == "/mem/new-name.md"
    assert meta["ingested_at"] == "i9"
    assert meta["captured_at"] == "t0"
    assert cand["date"] == "2026-07-22"  # 갱신 시 _ORDER 정렬로 목록이 재배열되는 부작용 차단
    assert meta["recurrence"] == 2


def test_bitemporal_timestamps_attached(tmp_path):
    study_inbox.append(tmp_path, "concept", "M.md", captured_at="2026-07-22T09:00:00")
    ident = study_inbox.content_hash("concept")[:12]
    meta = _candidate_columns(tmp_path, ident)
    assert meta["captured_at"] == "2026-07-22T09:00:00"  # 넘긴 valid-time
    assert meta["ingested_at"] is not None  # transaction-time은 현재 시각


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
    assert _candidate_columns(tmp_path, "new")["captured_at"] == "t"
    assert _candidate_columns(tmp_path, "old")["layer"] is None  # #189 U5 layer ALTER 이관


def test_layer_column_set_and_read(tmp_path):
    # 승격 판정 인식층을 후보에 기록(#189 U5) — 저널 promote 이벤트와 같은 축, list 계약은 불변
    ident = study_inbox.append(tmp_path, "concept body", "M.md")
    assert _candidate_columns(tmp_path, ident)["layer"] is None
    study_inbox.set_layer(tmp_path, ident, "knowledge")
    assert _candidate_columns(tmp_path, ident)["layer"] == "knowledge"
    # list_candidates 출력 shape는 layer 추가 전과 동일(계약 불변)
    assert set(study_inbox.list_candidates(tmp_path)[0]) == {
        "id",
        "date",
        "snippet",
        "source",
        "recurrence",
    }
