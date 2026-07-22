"""study inbox·resolved 원장·이벤트 저널 공개 API (S3 #75 · #91 V4 · U1 #130).

캡처 후보 큐(inbox)·처리 원장(ledger)·이벤트 저널을 **하나의 SQLite 스토어**
(``study.db``, ``study_store``)로 관리한다. 예전엔 각각 markdown·평문·jsonl 파일
셋이었으나(U1에서 대체), 공개 API 시그니처는 그대로 유지해 상위 호출부
(study·hook·session·doctor·trust)를 건드리지 않는다.

첫 인자는 **런타임 루트**(스토어가 사는 디렉토리)다 — 자기 파이프라인 repo면
``<repo>/.okf-study``, 홈/폴백이면 유저 스코프(``okf_home.resolve_capture``의
``runtime_root``). 승격 대상 repo와 분리된다(#114).

``id``는 스니펫 **내용 해시**(sha256) 앞 12자로, 선택 승격·폐기·중복 판정의 안정
키다. resolved 원장은 promoted/discarded된 id를 기록해 동일 스니펫 재적재를 막는다
(다르게 고쳐 쓴 메모는 해시가 달라 새 항목).

결정성이 필요한 값(``_now``·``content_hash``)은 **이 모듈이 소유**한다 — 테스트가
``okf_inbox._now``를 monkeypatch하는 계약을 지키기 위해서다(스토어는 순수 영속 계층
이라 SQL ``CURRENT_TIMESTAMP``를 쓰지 않는다). ``_sqlite3`` 부재 파이썬에서는
``study_store.available()``가 False가 되고 모든 조작이 **fail-closed 무동작**한다.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

import study_store

_ID_LEN = 12


def content_hash(snippet: str) -> str:
    """스니펫 내용 해시(sha256 hex 전체)."""
    return hashlib.sha256(_sanitize(snippet).encode("utf-8")).hexdigest()


def _sanitize(text: str) -> str:
    """개행·연속 공백을 단일 공백으로 정규화하고 양끝을 다듬는다."""
    return " ".join(str(text).split())


def _today() -> str:
    return datetime.date.today().isoformat()


def _now() -> str:
    """이벤트 저널 타임스탬프(ISO, 초 단위). 테스트는 monkeypatch로 결정론화한다."""
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- inbox ----------------------------------------------------------------


def append(runtime: str | Path, snippet: str, source: str, date: str | None = None) -> str:
    """후보 스니펫을 inbox에 적재하고 id를 반환한다. 동일 id는 재적재하지 않는다."""
    snippet = _sanitize(snippet)
    source = _sanitize(source)
    ident = content_hash(snippet)[:_ID_LEN]
    if not study_store.available():
        return ident  # fail-closed: sqlite3 부재 → 무적재(캡처 off와 동형)
    inserted = study_store.insert_candidate(runtime, ident, snippet, source, date or _today())
    if inserted:
        journal_append(runtime, "capture", ident, source=source)  # 순서·시각 이력(#114 U5)
    return ident


def list_candidates(runtime: str | Path) -> list[dict]:
    """inbox의 후보를 [{id, date, snippet, source}] 목록으로 반환한다(최신 우선)."""
    if not study_store.available():
        return []
    return study_store.list_candidates(runtime)


def drop(runtime: str | Path, ids: list[str] | set[str]) -> list[str]:
    """주어진 id의 후보를 제거하고 실제로 제거된 id를 반환한다."""
    if not study_store.available():
        return []
    return study_store.delete_candidates(runtime, ids)


def clear(runtime: str | Path) -> list[str]:
    """inbox의 모든 후보를 제거하고 제거된 id를 반환한다."""
    if not study_store.available():
        return []
    return study_store.clear_candidates(runtime)


# --- 이벤트 저널 (#114 U5) — 순서·시각·이력 -----------------------------------


def journal_append(runtime: str | Path, action: str, ident: str, **extra) -> None:
    """이벤트 저널에 한 줄 기록한다({ts, action, id, ...}). best-effort."""
    if not study_store.available():
        return
    filtered = {key: value for key, value in extra.items() if value is not None}
    study_store.append_event(runtime, _now(), action, ident, filtered)


def read_journal(runtime: str | Path, limit: int | None = None) -> list[dict]:
    """이벤트 저널을 시간순(오래된→최신)으로 읽는다. limit면 최신 N개."""
    if not study_store.available():
        return []
    return study_store.read_events(runtime, limit)


# --- resolved 원장 --------------------------------------------------------
#
# 전역 원장(#91 V4): 유효 홈이 있으면 promote/discard를 공유(유저 스코프) 원장에도
# 기록(write-through)하고, 판정은 활성 원장 ∪ 공유 원장 조회다 — "repo A에서 promote한
# 스니펫을 나중에 다른 위치에서 재캡처 → 재큐"라는 시간축 dedup 구멍(#2)을 막는다.
# 내용해시 키라 안전하고, 홈 미옵트인 시 현행 단일 원장으로 자연 저하.


def _global_ledger_root(runtime: str | Path) -> str | None:
    """교차 스코프 dedup용 **공유(유저 스코프) 원장 루트**를 반환한다(#114).

    홈 미옵트인이면 None(현행 단일 원장으로 자연 저하). 활성 런타임이 곧 공유 원장
    (홈/폴백 캡처)이면 write-through가 자기 자신이라 None. 자기 파이프라인 repo의
    in-repo 런타임에서만 유저 스코프 공유 원장을 반환한다.
    """
    try:
        import okf_home
    except ImportError:  # pragma: no cover - 단독 배포 등 비정상 배치 관용
        return None
    home, _reason = okf_home.home_state()
    if home is None:
        return None
    shared = str(okf_home.user_scope_runtime())
    try:
        if Path(runtime).resolve() == Path(shared).resolve():
            return None
    except OSError:
        return None
    return shared


def is_resolved(runtime: str | Path, ident: str) -> bool:
    """id가 promoted/discarded로 기록됐는지 — 활성 원장 ∪ 공유(유저 스코프) 원장."""
    if not study_store.available():
        return False
    if study_store.has_resolution(runtime, ident):
        return True
    shared = _global_ledger_root(runtime)
    return shared is not None and study_store.has_resolution(shared, ident)


def record(runtime: str | Path, ident: str, status: str, ref: str | None = None) -> None:
    """id를 promoted/discarded로 원장에 기록한다(이미 있으면 무시).

    기록은 후보가 잡힌 스코프의 런타임 원장이 정본이고, 홈 옵트인 시 공유(유저 스코프)
    원장에도 write-through한다. 교차 승격(#91 §4)은 이 함수로 원 스코프에 기록하되
    ``ref``에 홈 개념 경로를 담는 규약이다.
    """
    if status not in ("promoted", "discarded"):
        raise ValueError(f"알 수 없는 status: {status}")
    if not study_store.available():
        return
    study_store.insert_resolution(runtime, ident, status, ref)
    journal_append(runtime, status, ident, ref=ref)  # 순서·시각 이력(#114 U5)
    shared = _global_ledger_root(runtime)
    if shared is not None:
        study_store.insert_resolution(shared, ident, status, ref)
