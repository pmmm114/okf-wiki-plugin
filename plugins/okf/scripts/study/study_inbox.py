"""study inbox·resolved 원장·이벤트 저널 공개 API (S3 #75 · #91 V4 · U1 #130).

캡처 후보 큐(inbox)·처리 원장(ledger)·이벤트 저널을 **하나의 SQLite 스토어**
(``study.db``, ``study_store``)로 관리한다. 예전엔 각각 markdown·평문·jsonl 파일
셋이었으나(U1에서 대체), 공개 API 시그니처는 그대로 유지해 상위 호출부
(study·hook·session·doctor·trust)를 건드리지 않는다.

첫 인자는 **런타임 루트**(스토어가 사는 디렉토리)다 — 자기 파이프라인 repo면
``<repo>/.okf-study``, vault/폴백이면 유저 스코프(``study_scope.resolve_capture``의
``runtime_root``). 승격 대상 repo와 분리된다(#114).

``id``는 스니펫 **내용 해시**(sha256) 앞 12자로, 선택 승격·폐기·중복 판정의 안정
키다. resolved 원장은 promoted/discarded된 id를 기록해 동일 스니펫 재적재를 막는다
(다르게 고쳐 쓴 메모는 해시가 달라 새 항목).

결정성이 필요한 값(``_now``·``content_hash``)은 **이 모듈이 소유**한다 — 테스트가
``study_inbox._now``를 monkeypatch하는 계약을 지키기 위해서다(스토어는 순수 영속 계층
이라 SQL ``CURRENT_TIMESTAMP``를 쓰지 않는다). ``_sqlite3`` 부재 파이썬에서는
``study_store.available()``가 False가 되고 모든 조작이 **fail-closed 무동작**한다.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

import study_simhash
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


def append(
    runtime: str | Path,
    snippet: str,
    source: str,
    date: str | None = None,
    line_hashes: list[str] | None = None,
    captured_at: str | None = None,
) -> str:
    """후보(개념 블록)를 inbox에 적재하고 id를 반환한다.

    동일 id 재캡처는 새 후보를 만들지 않고 **재등장 카운터를 올린다**(#132). ``line_hashes``
    는 블록의 자식 줄-해시(A2′, #131); 미지정이면 단일 줄 블록으로 보고 id 자신을 자식으로
    둔다. ``captured_at``(valid-time)은 미지정 시 현재 시각 — 마이그레이션은 원 캡처 시각을
    넘긴다.
    """
    snippet = _sanitize(snippet)
    source = _sanitize(source)
    ident = content_hash(snippet)[:_ID_LEN]
    if not study_store.available():
        return ident  # fail-closed: sqlite3 부재 → 무적재(캡처 off와 동형)
    children = line_hashes if line_hashes is not None else [ident]
    now = _now()
    inserted = study_store.insert_candidate(
        runtime,
        ident,
        snippet,
        source,
        date or _today(),
        children,
        captured_at=captured_at or now,
        ingested_at=now,
        simhash=study_simhash.fingerprint_hex(snippet),  # 근사중복 자문 지문(#133)
    )
    if inserted:
        journal_append(runtime, "capture", ident, source=source)  # 순서·시각 이력(#114 U5)
    return ident


def near_duplicates(
    runtime: str | Path, ident: str, threshold: int = study_simhash.DEFAULT_THRESHOLD
) -> list[str]:
    """``ident``와 SimHash 해밍거리 ``threshold`` 이하인 다른 후보 id들 — **자문 전용**.

    재서술된 근사중복(정확 해시가 놓치는 것)을 표면화한다. 자동병합·게이팅 없음,
    정확 해시 앵커를 대체하지 않는다(#133). 임계는 실측 튜닝 대상.
    """
    if not study_store.available():
        return []
    fingerprints = study_store.list_fingerprints(runtime)
    target = next((hx for cid, hx in fingerprints if cid == ident), None)
    if not target:
        return []
    target_int = int(target, 16)
    return [
        cid
        for cid, hx in fingerprints
        if cid != ident and hx and study_simhash.hamming(target_int, int(hx, 16)) <= threshold
    ]


def candidate_meta(runtime: str | Path, ident: str) -> dict:
    """후보의 시간축·승격 메타 {captured_at, ingested_at, recurrence, supersedes}(#132)."""
    if not study_store.available():
        return {}
    return study_store.candidate_meta(runtime, ident)


def set_supersedes(runtime: str | Path, ident: str, target: str | None) -> None:
    """후보가 갱신하는 기존 개념 id를 기록한다(#132 supersedes 링크)."""
    if not study_store.available():
        return
    study_store.set_supersedes(runtime, ident, target)


def invalidate(runtime: str | Path, ident: str) -> None:
    """원장 항목을 무효화(보존) — 갱신·초과된 판정을 지우지 않고 시각만 새긴다(#132)."""
    if not study_store.available():
        return
    study_store.invalidate_resolution(runtime, ident, _now())


def block_resolved(
    runtime: str | Path, block_id: str, line_hashes: list[str] | None = None
) -> bool:
    """개념 블록이 이미 처리됐는지 — 블록 id 자체가 resolved거나 **모든 자식 줄**이
    resolved면 True. 자식 중 하나라도 미해소면 False → 리뷰로 올린다(A2′ #131)."""
    if not study_store.available():
        return False
    if is_resolved(runtime, block_id):
        return True
    if not line_hashes:
        return False
    return all(is_resolved(runtime, h) for h in line_hashes)


def candidate_lines(runtime: str | Path, ident: str) -> list[str]:
    """후보(블록)의 자식 줄-해시를 순서대로 반환한다(A2′)."""
    if not study_store.available():
        return []
    return study_store.candidate_lines(runtime, ident)


def block_known_lines(runtime: str | Path, ident: str) -> list[str]:
    """후보의 자식 줄 중 이미 처리(resolved)된 줄-해시 — 혼합-이력 표식(A2′)."""
    return [h for h in candidate_lines(runtime, ident) if is_resolved(runtime, h)]


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
# 전역 원장(#91 V4): 유효 vault가 있으면 promote/discard를 공유(유저 스코프) 원장에도
# 기록(write-through)하고, 판정은 활성 원장 ∪ 공유 원장 조회다 — "repo A에서 promote한
# 스니펫을 나중에 다른 위치에서 재캡처 → 재큐"라는 시간축 dedup 구멍(#2)을 막는다.
# 내용해시 키라 안전하고, vault 미옵트인 시 현행 단일 원장으로 자연 저하.


def _global_ledger_root(runtime: str | Path) -> str | None:
    """교차 스코프 dedup용 **공유(유저 스코프) 원장 루트**를 반환한다(#114).

    vault 미옵트인이면 None(현행 단일 원장으로 자연 저하). 활성 런타임이 곧 공유 원장
    (vault/폴백 캡처)이면 write-through가 자기 자신이라 None. 자기 파이프라인 repo의
    in-repo 런타임에서만 유저 스코프 공유 원장을 반환한다.
    """
    try:
        import okf_vault
        import study_scope
    except ImportError:  # pragma: no cover - 단독 배포 등 비정상 배치 관용
        return None
    vault, _reason = okf_vault.vault_state()
    if vault is None:
        return None
    shared = str(study_scope.user_scope_runtime())
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

    기록은 후보가 잡힌 스코프의 런타임 원장이 정본이고, vault 옵트인 시 공유(유저 스코프)
    원장에도 write-through한다. 교차 승격(#91 §4)은 이 함수로 원 스코프에 기록하되
    ``ref``에 vault 개념 경로를 담는 규약이다.
    """
    if status not in ("promoted", "discarded"):
        raise ValueError(f"알 수 없는 status: {status}")
    if not study_store.available():
        return
    # A2′(#131): 블록 id + 자식 줄-해시를 함께 원장에 — 미래에 같은 줄이 **다른 그룹핑**
    # 으로 재캡처돼도 줄-단위로 dedup되어 재부상하지 않는다(ledger 연속성).
    children = [h for h in study_store.candidate_lines(runtime, ident) if h != ident]
    study_store.insert_resolution(runtime, ident, status, ref)
    journal_append(runtime, status, ident, ref=ref)  # 순서·시각 이력(#114 U5) — 블록만
    for child in children:
        study_store.insert_resolution(runtime, child, status, None)
    shared = _global_ledger_root(runtime)
    if shared is not None:
        study_store.insert_resolution(shared, ident, status, ref)
        for child in children:
            study_store.insert_resolution(shared, child, status, None)
