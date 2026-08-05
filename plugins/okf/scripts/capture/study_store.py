"""study 스테이징 SQLite 스토어 (U1, #130).

markdown inbox·평문 ledger·jsonl journal **3종을 대체**하는 런타임 staging store.
staging은 지식 SoT가 아니라 소모성 런타임 상태다(지식 정본은 git 번들 + ``log.md``)
— ``study.db``는 오늘의 세 파일과 같은 층위이며 gitignore로 커밋 제외된다.

이 모듈은 **순수 영속 계층**이다. 타임스탬프·내용해시처럼 결정성이 필요한 값은
호출부(``study_inbox``)가 만들어 넘긴다 → monkeypatch 계약(SQL ``CURRENT_TIMESTAMP``
금지)을 보존한다. ``_sqlite3`` C확장 부재 파이썬은 ``available()``가 False가 되고
상위에서 fail-closed(무동작)로 흡수한다(#108 교훈: 환경 무가정).

읽기 함수는 DB 파일이 없으면 **파일을 만들지 않고** 빈 결과를 돌려준다(부재=빈 상태).
쓰기 함수만 필요 시 디렉토리·DB를 생성한다.
"""

from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path

try:  # _sqlite3 C확장은 파이썬이 SQLite 포함 빌드여야 import된다
    import sqlite3
except ImportError:  # pragma: no cover - SQLite 미포함 파이썬
    sqlite3 = None  # type: ignore[assignment]

DB_NAME = "study.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    id            TEXT NOT NULL UNIQUE,
    snippet       TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    captured_date TEXT NOT NULL,
    captured_at   TEXT,
    ingested_at   TEXT,
    recurrence    INTEGER NOT NULL DEFAULT 1,
    layer         TEXT
);
CREATE TABLE IF NOT EXISTS candidate_line (
    candidate_id TEXT NOT NULL,
    line_hash    TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    PRIMARY KEY (candidate_id, seq)
);
CREATE TABLE IF NOT EXISTS resolution (
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    ref            TEXT
);
CREATE TABLE IF NOT EXISTS event (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    action TEXT NOT NULL,
    ident  TEXT NOT NULL,
    extra  TEXT
);
CREATE TABLE IF NOT EXISTS file_track (
    source     TEXT PRIMARY KEY,
    file_hash  TEXT NOT NULL,
    block_ids  TEXT NOT NULL,
    updated_at TEXT
);
"""

# 기존 db(구 유닛 스키마) 업그레이드용 — CREATE TABLE IF NOT EXISTS는 컬럼을 더하지
# 않으므로 누락 컬럼을 ALTER ADD로 채운다(#132 bitemporal·recurrence).
_ADDED_COLUMNS = {
    "candidate": {
        "captured_at": "TEXT",
        "ingested_at": "TEXT",
        "recurrence": "INTEGER NOT NULL DEFAULT 1",
        "layer": "TEXT",
    },
}
# 구 db의 `simhash` 컬럼은 지우지 않는다 — 근사중복 지표가 BM25로 바뀌며 쓰기가
# 끊겼고(#392), study.db는 소모성 런타임이라 남은 열이 읽히지 않으면 무해하다.
# 새 db에는 애초에 만들지 않는다(_SCHEMA).

_ORDER = "ORDER BY captured_date DESC, seq ASC"  # 최신 날짜 우선, 동일 날짜는 적재순
_FIELDS = "id, captured_date, snippet, source, recurrence"  # 후보 출력 shape의 단일원천

# 그룹 뷰의 정렬은 **평탄 뷰의 첫 등장 순서**여야 한다(같은 목록의 두 뷰이므로) — 그래서
# 그룹 키는 그룹의 선두 후보, 곧 `_ORDER`가 그 source에서 처음 뱉는 행이다. `MAX(날짜)`
# 하나로는 부족하고(동일 날짜 그룹의 순서가 갈린다) `MIN(seq)`만으로도 안 된다(옛 후보가
# 낮은 seq로 그룹을 끌어올린다) — **최신 날짜 안에서의 최소 seq**가 선두다.
_GROUP_ORDER = """
SELECT c.source, COUNT(*),
       MIN(CASE WHEN c.captured_date = m.newest THEN c.seq END) AS lead_seq
  FROM candidate c
  JOIN (SELECT source, MAX(captured_date) AS newest FROM candidate GROUP BY source) m
    ON m.source = c.source
 GROUP BY c.source
 ORDER BY MAX(c.captured_date) DESC, lead_seq ASC
"""

# 스키마 초기화를 경로당 1회로 직렬화한다(프로세스 내). 매 연산 연결에서 DDL을 돌리면
# 16-스레드 동시 쓰기가 락 경합으로 후보를 유실했다 — 초기화만 잠그고 실제 쓰기는 WAL
# + busy_timeout으로 병행한다. 다중 프로세스 경합은 busy_timeout이 흡수한다.
_init_lock = threading.Lock()
_initialized: set[str] = set()


def available() -> bool:
    """이 파이썬이 sqlite3(``_sqlite3`` C확장)를 갖췄는지."""
    return sqlite3 is not None


def _db_path(runtime: str | Path) -> Path:
    return Path(runtime) / DB_NAME


def _exists(runtime: str | Path) -> bool:
    return _db_path(runtime).is_file()


def _ensure_ready(path: Path) -> None:
    """DB가 없으면(또는 이 프로세스가 처음 보면) WAL + 스키마를 1회 초기화한다."""
    key = str(path)
    if key in _initialized and path.is_file():
        return
    with _init_lock:  # DDL을 직렬화 — 동시 CREATE로 인한 쓰기 락 경합 방지
        if key in _initialized and path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)  # CREATE TABLE IF NOT EXISTS — 멱등
            for table, columns in _ADDED_COLUMNS.items():  # 구 db 컬럼 보강(#132)
                have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            conn.commit()
        finally:
            conn.close()
        _initialized.add(key)


@contextlib.contextmanager
def _connect(runtime: str | Path):
    """짧은 수명 커넥션(스레드마다 자기 것 → ``check_same_thread`` 안전).

    스키마는 ``_ensure_ready``가 경로당 1회 만든다. 실제 쓰기는 WAL + ``busy_timeout``
    으로 병행하며 정상 종료 시 커밋, 예외 시 롤백 후 항상 close.
    """
    path = _db_path(runtime)
    _ensure_ready(path)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- candidate (inbox) ----------------------------------------------------


def insert_candidate(
    runtime: str | Path,
    ident: str,
    snippet: str,
    source: str,
    captured_date: str,
    line_hashes: list[str] | None = None,
    captured_at: str | None = None,
    ingested_at: str | None = None,
) -> bool:
    """후보를 적재하고 **새로 들어갔는지**(True) 재등장인지(False) 반환한다.

    #369부터 호출부(capture_file)가 **출현 전이에서만** 재캡처를 전달한다 — 이 함수의
    카운터 증가는 기계 동작일 뿐, recurrence의 의미는 저장 이벤트가 아니라 전이 수다.
    동일 id 재캡처는 **재등장 카운터를 올리고 source·ingested_at을 최근 캡처값으로
    갱신한다**(#132·#255) — rename·이동된 파일의 후보가 죽은 경로에 영구 귀속되지
    않는다. ``captured_at``(valid-time, 첫 캡처)·``ingested_at``(transaction-time,
    최근 적재)은 후보에 부착되는 이원 타임스탬프다. ``captured_date``는 valid-time
    계열이라 재캡처에 불변(갱신하면 목록 정렬이 재배열된다). ``line_hashes``는 자식
    줄-해시(A2′, #131). 근사중복 자문용 지문은 더 이상 저장하지 않는다 — BM25가
    코퍼스 통계에 의존해 미리 계산할 수 없다(#392).

    동일 내용 블록이 **여러 파일에 공존**하면 source는 최후 캡처 파일이 이긴다
    (last-write-wins) — 실측상 교차-파일 중복은 전부 보일러플레이트라 노이즈 필터
    (#256)가 선행 제거하는 전제이고, 정당 중복이 생기면 귀속은 최근 저장 기준이 된다.
    """
    with _connect(runtime) as conn:
        existed = (
            conn.execute("SELECT 1 FROM candidate WHERE id=?", (ident,)).fetchone() is not None
        )
        conn.execute(
            "INSERT INTO candidate(id, snippet, source, captured_date, captured_at, "
            "ingested_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET recurrence = recurrence + 1, "
            "source = excluded.source, ingested_at = excluded.ingested_at",
            (ident, snippet, source, captured_date, captured_at, ingested_at),
        )
        if not existed and line_hashes:
            conn.executemany(
                "INSERT OR IGNORE INTO candidate_line(candidate_id, line_hash, seq) VALUES(?,?,?)",
                [(ident, lh, i) for i, lh in enumerate(line_hashes)],
            )
        return not existed


def set_layer(runtime: str | Path, ident: str, layer: str | None) -> None:
    """후보의 인식층(정보/지식/지혜)을 기록한다(Epic #189 U5 — 승격 판정 결과 영속)."""
    with _connect(runtime) as conn:
        conn.execute("UPDATE candidate SET layer=? WHERE id=?", (layer, ident))


def list_snippets(runtime: str | Path) -> list[tuple[str, str]]:
    """(id, snippet) 목록 — 근사중복 자문의 코퍼스(#392).

    지문이 아니라 **원문**을 낸다. BM25는 IDF가 코퍼스 전체에 걸려 있어 미리 계산해
    저장할 수 없고, 그 덕에 저장분과 원문이 어긋나는 소급 결함이 생기지 않는다.
    """
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        return [(r[0], r[1]) for r in conn.execute("SELECT id, snippet FROM candidate").fetchall()]


def candidate_lines(runtime: str | Path, ident: str) -> list[str]:
    """후보의 자식 줄-해시를 순서대로 반환한다(A2′)."""
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT line_hash FROM candidate_line WHERE candidate_id=? ORDER BY seq", (ident,)
            ).fetchall()
        ]


def has_candidate(runtime: str | Path, ident: str) -> bool:
    if not _exists(runtime):
        return False
    with _connect(runtime) as conn:
        return conn.execute("SELECT 1 FROM candidate WHERE id=?", (ident,)).fetchone() is not None


def list_candidates(runtime: str | Path) -> list[dict]:
    """[{id, date, snippet, source, recurrence}] 최신 우선.

    ``recurrence``(출현 전이 수 — 파일 저장 횟수가 아니라 블록이 파일에 새로 나타난
    횟수, #369)는 승격 판단 신호로 인라인 노출한다(#132). 시각 메타
    (captured_at/ingested_at)는 출력 결정성을 위해 노출하지 않는다(기록 전용 provenance).
    """
    return _select_candidates(runtime, "")


def list_candidates_by_source(runtime: str | Path, source: str) -> list[dict]:
    """한 파일 그룹의 후보만 ``list_candidates``와 동일한 shape·정렬로 반환한다(#383).

    매칭은 **저장값 정확 일치**다 — 경로 해석(``Path.resolve``·실존 검사)을 하지 않으므로
    rename·삭제된 옛 경로의 잔존 후보도 그대로 집힌다(``resolve --source``와 같은 계약).
    """
    return _select_candidates(runtime, "WHERE source=?", (source,))


def _select_candidates(runtime: str | Path, where: str, params: tuple = ()) -> list[dict]:
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        rows = conn.execute(f"SELECT {_FIELDS} FROM candidate {where} {_ORDER}", params).fetchall()
    return [
        {"id": r[0], "date": r[1], "snippet": r[2], "source": r[3], "recurrence": r[4]}
        for r in rows
    ]


def candidate_groups(runtime: str | Path) -> list[dict]:
    """파일 그룹 헤더 ``[{source, count}]`` — 스니펫 본문이 DB를 떠나지 않는 집계(#383).

    같은 목록의 헤더 뷰이므로 그룹 집합·건수·순서는 ``list_candidates``를 source로 묶은
    것과 정확히 일치한다(정렬 근거는 ``_GROUP_ORDER`` 주석).
    """
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        rows = conn.execute(_GROUP_ORDER).fetchall()
    return [{"source": r[0], "count": r[1]} for r in rows]


def delete_candidates(runtime: str | Path, ids: list[str] | set[str]) -> list[str]:
    ids = list(dict.fromkeys(ids))
    if not ids or not _exists(runtime):
        return []
    marks = ",".join("?" * len(ids))
    with _connect(runtime) as conn:
        removed = [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM candidate WHERE id IN ({marks}) {_ORDER}", ids
            ).fetchall()
        ]
        conn.execute(f"DELETE FROM candidate WHERE id IN ({marks})", ids)
        conn.execute(f"DELETE FROM candidate_line WHERE candidate_id IN ({marks})", ids)
    return removed


def clear_candidates(runtime: str | Path) -> list[str]:
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        ids = [r[0] for r in conn.execute(f"SELECT id FROM candidate {_ORDER}").fetchall()]
        conn.execute("DELETE FROM candidate")
        conn.execute("DELETE FROM candidate_line")
    return ids


def candidate_source(runtime: str | Path, ident: str) -> str | None:
    """후보의 현재 source 경로 — resolve 저널의 파일 병기용(#369)."""
    if not _exists(runtime):
        return None
    with _connect(runtime) as conn:
        row = conn.execute("SELECT source FROM candidate WHERE id=?", (ident,)).fetchone()
    return row[0] if row else None


# --- file_track (파일 추적 스냅샷, #369) ------------------------------------
#
# 파일별 **최신 상태 지문**(내용 해시 + 블록 id 집합)만 유지한다 — 버전 이력이 아니다
# (staging은 소모성, 이력은 승격 후 git 번들의 몫). 캡처가 이 스냅샷과 diff해
# 새로 나타난 블록만 적재한다.


def get_file_track(runtime: str | Path, source: str) -> dict | None:
    """소스 파일의 최근 캡처 스냅샷 {file_hash, block_ids} — 없으면 None."""
    if not _exists(runtime):
        return None
    with _connect(runtime) as conn:
        row = conn.execute(
            "SELECT file_hash, block_ids FROM file_track WHERE source=?", (source,)
        ).fetchone()
    if row is None:
        return None
    return {"file_hash": row[0], "block_ids": json.loads(row[1])}


def set_file_track(
    runtime: str | Path, source: str, file_hash: str, block_ids: list[str], updated_at: str
) -> None:
    """소스 파일의 캡처 스냅샷을 upsert한다(#369)."""
    with _connect(runtime) as conn:
        conn.execute(
            "INSERT INTO file_track(source, file_hash, block_ids, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(source) DO UPDATE SET file_hash=excluded.file_hash, "
            "block_ids=excluded.block_ids, updated_at=excluded.updated_at",
            (source, file_hash, json.dumps(block_ids), updated_at),
        )


# --- resolution (ledger) --------------------------------------------------


def has_resolution(runtime: str | Path, ident: str) -> bool:
    if not _exists(runtime):
        return False
    with _connect(runtime) as conn:
        return conn.execute("SELECT 1 FROM resolution WHERE id=?", (ident,)).fetchone() is not None


def insert_resolution(runtime: str | Path, ident: str, status: str, ref: str | None) -> bool:
    with _connect(runtime) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO resolution(id, status, ref) VALUES(?,?,?)",
            (ident, status, ref),
        )
        return cur.rowcount > 0


def list_resolutions(runtime: str | Path) -> list[tuple[str, str, str | None]]:
    """(id, status, ref) 목록 — 마이그레이션 이관용."""
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute("SELECT id, status, ref FROM resolution ORDER BY id").fetchall()
        ]


# --- event (journal) ------------------------------------------------------


def append_event(runtime: str | Path, ts: str, action: str, ident: str, extra: dict | None) -> None:
    payload = json.dumps(extra, ensure_ascii=False) if extra else None
    with _connect(runtime) as conn:
        conn.execute(
            "INSERT INTO event(ts, action, ident, extra) VALUES(?,?,?,?)",
            (ts, action, ident, payload),
        )


def read_events(runtime: str | Path, limit: int | None = None) -> list[dict]:
    """[{ts, action, id, ...extra}] 시간순(오래된→최신). limit면 최신 N개."""
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        rows = conn.execute(
            "SELECT ts, action, ident, extra FROM event ORDER BY seq ASC"
        ).fetchall()
    events = []
    for ts, action, ident, extra in rows:
        entry = {"ts": ts, "action": action, "id": ident}
        if extra:
            entry.update(json.loads(extra))
        events.append(entry)
    return events[-limit:] if limit else events
