"""study 스테이징 SQLite 스토어 (U1, #130).

markdown inbox·평문 ledger·jsonl journal **3종을 대체**하는 런타임 staging store.
staging은 지식 SoT가 아니라 소모성 런타임 상태다(지식 정본은 git 번들 + ``log.md``)
— ``study.db``는 오늘의 세 파일과 같은 층위이며 gitignore로 커밋 제외된다.

이 모듈은 **순수 영속 계층**이다. 타임스탬프·내용해시처럼 결정성이 필요한 값은
호출부(``okf_inbox``)가 만들어 넘긴다 → monkeypatch 계약(SQL ``CURRENT_TIMESTAMP``
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
    supersedes    TEXT,
    simhash       TEXT
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
    ref            TEXT,
    invalidated_at TEXT
);
CREATE TABLE IF NOT EXISTS event (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    action TEXT NOT NULL,
    ident  TEXT NOT NULL,
    extra  TEXT
);
"""

# 기존 db(구 유닛 스키마) 업그레이드용 — CREATE TABLE IF NOT EXISTS는 컬럼을 더하지
# 않으므로 누락 컬럼을 ALTER ADD로 채운다(#132 bitemporal·recurrence·supersedes).
_ADDED_COLUMNS = {
    "candidate": {
        "captured_at": "TEXT",
        "ingested_at": "TEXT",
        "recurrence": "INTEGER NOT NULL DEFAULT 1",
        "supersedes": "TEXT",
        "simhash": "TEXT",
    },
    "resolution": {"invalidated_at": "TEXT"},
}

_ORDER = "ORDER BY captured_date DESC, seq ASC"  # 최신 날짜 우선, 동일 날짜는 적재순

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
    simhash: str | None = None,
) -> bool:
    """후보를 적재하고 **새로 들어갔는지**(True) 재등장인지(False) 반환한다.

    동일 id 재캡처는 **재등장 카운터를 올린다**(#132) — 승격 판단 신호. ``captured_at``
    (valid-time, 첫 캡처)·``ingested_at``(transaction-time)은 후보에 부착되는 이원
    타임스탬프다. ``line_hashes``는 자식 줄-해시(A2′, #131). ``simhash``는 근사중복
    자문용 지문(#133).
    """
    with _connect(runtime) as conn:
        existed = (
            conn.execute("SELECT 1 FROM candidate WHERE id=?", (ident,)).fetchone() is not None
        )
        conn.execute(
            "INSERT INTO candidate(id, snippet, source, captured_date, captured_at, ingested_at, "
            "simhash) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET recurrence = recurrence + 1",
            (ident, snippet, source, captured_date, captured_at, ingested_at, simhash),
        )
        if not existed and line_hashes:
            conn.executemany(
                "INSERT OR IGNORE INTO candidate_line(candidate_id, line_hash, seq) VALUES(?,?,?)",
                [(ident, lh, i) for i, lh in enumerate(line_hashes)],
            )
        return not existed


def candidate_meta(runtime: str | Path, ident: str) -> dict:
    """후보의 시간축·승격 메타 — {captured_at, ingested_at, recurrence, supersedes}."""
    if not _exists(runtime):
        return {}
    with _connect(runtime) as conn:
        row = conn.execute(
            "SELECT captured_at, ingested_at, recurrence, supersedes FROM candidate WHERE id=?",
            (ident,),
        ).fetchone()
    if row is None:
        return {}
    return {
        "captured_at": row[0],
        "ingested_at": row[1],
        "recurrence": row[2],
        "supersedes": row[3],
    }


def set_supersedes(runtime: str | Path, ident: str, target: str | None) -> None:
    """후보가 갱신하는 기존 개념 id를 기록한다(#132 supersedes 링크)."""
    with _connect(runtime) as conn:
        conn.execute("UPDATE candidate SET supersedes=? WHERE id=?", (target, ident))


def list_fingerprints(runtime: str | Path) -> list[tuple[str, str | None]]:
    """(id, simhash) 목록 — 근사중복 자문 스캔용(#133)."""
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        return [(r[0], r[1]) for r in conn.execute("SELECT id, simhash FROM candidate").fetchall()]


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

    ``recurrence``(재등장 수)는 승격 판단 신호로 인라인 노출한다(#132). 시각 메타
    (captured_at/ingested_at/supersedes)는 결정성 위해 ``candidate_meta``로 분리.
    """
    if not _exists(runtime):
        return []
    with _connect(runtime) as conn:
        rows = conn.execute(
            f"SELECT id, captured_date, snippet, source, recurrence FROM candidate {_ORDER}"
        ).fetchall()
    return [
        {"id": r[0], "date": r[1], "snippet": r[2], "source": r[3], "recurrence": r[4]}
        for r in rows
    ]


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


def invalidate_resolution(runtime: str | Path, ident: str, ts: str) -> None:
    """원장 항목을 **무효화하되 삭제하지 않는다**(invalidate-don't-delete, #132).

    개념이 갱신·초과되면 옛 판정을 지우지 않고 무효화 시각만 새겨 이력을 보존한다.
    dedup 판정(``has_resolution``)에는 그대로 남아 재부상은 계속 막는다.
    """
    with _connect(runtime) as conn:
        conn.execute("UPDATE resolution SET invalidated_at=? WHERE id=?", (ts, ident))


def resolution_invalidated_at(runtime: str | Path, ident: str) -> str | None:
    if not _exists(runtime):
        return None
    with _connect(runtime) as conn:
        row = conn.execute("SELECT invalidated_at FROM resolution WHERE id=?", (ident,)).fetchone()
    return row[0] if row else None


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
