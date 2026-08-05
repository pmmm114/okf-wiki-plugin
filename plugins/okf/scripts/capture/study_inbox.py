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

import study_blocks
import study_overlap
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

    #369부터 재캡처는 호출부(capture_file)가 출현 전이에서만 전달한다(recurrence = 전이 수).
    동일 id 재캡처는 새 후보를 만들지 않고 **재등장 카운터를 올리며 source·ingested_at을
    최근 캡처값으로 갱신한다**(#132·#255 — rename된 파일을 따라간다). ``line_hashes``
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
    )
    if inserted:
        journal_append(runtime, "capture", ident, source=source)  # 순서·시각 이력(#114 U5)
    return ident


def file_hash(text: str) -> str:
    """파일 내용 해시(원문 그대로 — 변경 감지용, sanitize 없음)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def capture_file(
    runtime: str | Path, source: str, text: str, labels: frozenset[str] | None = None
) -> dict:
    """파일 단위 캡처(#369) — 파일 추적 스냅샷과 diff해 **새로 나타난 블록만** 적재한다.

    반환 ``{event, appended, reappeared}``. ``event``는 ``"unchanged"``(해시 동일 —
    완전 무동작) | ``"added"``(스냅샷 없음 = 이 파일의 첫 캡처) | ``"changed"``.
    ``appended``는 신규 적재 수(새 리뷰거리), ``reappeared``는 기존 후보의 재출현 수
    (전이 +1이되 새 리뷰거리는 아님). **계속 있던 블록(스냅샷 ∩ 현재)은 무동작**이다 —
    recurrence가 저장 이벤트가 아니라 출현 전이를 세게 하는 지점. 원장 처리분은
    전이여도 재적재하지 않는다(discard 존중). 전이는 저널에 남는다(reappear/disappear
    — 파일의 블록 구성 타임라인을 id 수준에서 복원 가능하게).
    """
    source = _sanitize(source)
    if not study_store.available():
        return {"event": "unavailable", "appended": 0, "reappeared": 0}
    digest = file_hash(text)
    prev = study_store.get_file_track(runtime, source)
    if prev is not None and prev["file_hash"] == digest:
        return {"event": "unchanged", "appended": 0, "reappeared": 0}
    prev_ids = set(prev["block_ids"]) if prev else set()
    appended = reappeared = 0
    now_ids: list[str] = []
    handled: set[str] = set()
    for block in study_blocks.concept_blocks(text, labels=labels):
        snippet = " ".join(block)
        if not _sanitize(snippet):
            continue
        ident = content_hash(snippet)[:_ID_LEN]
        now_ids.append(ident)
        if ident in handled or ident in prev_ids:
            continue  # 파일 내 중복 또는 계속 있던 블록 — 전이 아님
        handled.add(ident)
        line_hashes = [content_hash(line)[:_ID_LEN] for line in block]
        if block_resolved(runtime, ident, line_hashes):
            continue
        if study_store.has_candidate(runtime, ident):
            reappeared += 1
            journal_append(runtime, "reappear", ident, source=source)  # 전이 저널(#369)
        else:
            appended += 1
        append(runtime, snippet, source, line_hashes=line_hashes)
    for gone in sorted(prev_ids - set(now_ids)):
        journal_append(runtime, "disappear", gone, source=source)
    study_store.set_file_track(runtime, source, digest, list(dict.fromkeys(now_ids)), _now())
    return {
        "event": "changed" if prev is not None else "added",
        "appended": appended,
        "reappeared": reappeared,
    }


def track_file(
    runtime: str | Path, source: str, text: str, labels: frozenset[str] | None = None
) -> None:
    """스냅샷만 갱신한다(#369) — 적재를 자체 경로로 마친 소비자용(scan ``--enqueue``).

    갱신 없이 캡처와 별도 경로로 적재하면 다음 훅 캡처가 같은 블록을 전이로 다시
    세어 이중 계수된다. 관측 전용 scan(무 enqueue)은 이 함수를 부르지 않는다 —
    관측은 상태를 바꾸지 않는다.
    """
    if not study_store.available():
        return
    ids: list[str] = []
    for block in study_blocks.concept_blocks(text, labels=labels):
        snippet = " ".join(block)
        if _sanitize(snippet):
            ids.append(content_hash(snippet)[:_ID_LEN])
    study_store.set_file_track(
        runtime, _sanitize(source), file_hash(text), list(dict.fromkeys(ids)), _now()
    )


def _overlap_index(runtime: str | Path):
    """후보 원문을 **1회** 로드해 어휘 겹침 색인을 만든다(#392).

    지문 캐시가 아니라 원문에서 매번 만든다 — BM25의 IDF가 코퍼스 전체에 걸려 있어
    미리 계산해 저장할 수 없기 때문이다. 그 대가로 저장분과 원문이 어긋나는 소급
    결함이 원천에서 사라진다(구 지문 잔존 실측 105건).
    """
    return study_overlap.build_index(dict(study_store.list_snippets(runtime)))


def near_duplicates(
    runtime: str | Path, ident: str, top_k: int = study_overlap.DEFAULT_TOP_K
) -> list[dict]:
    """``ident``와 어휘가 겹치는 다른 후보 **상위 K** — ``[{id, overlap}]``, 내림차순.

    재서술된 근사중복(정확 해시가 놓치는 것)을 표면화한다. **자문 전용** — 자동병합·
    게이팅 없고 정확 해시 앵커를 대체하지 않는다(#133).

    임계 필터가 아니라 상위 K인 이유는 #306에 있고 지표를 BM25로 바꿔도 유지된다
    (#387): 점수가 높을수록 의미가 같다는 관계 자체가 없어 어떤 값으로 잘라도 진짜와
    가짜가 섞인다. 값을 함께 실어 판정을 사람·모델에게 넘긴다.

    토큰이 없는 후보(판정 불가)는 **양쪽 모두 제외**한다.

    **단건 질의용**이다 — 호출마다 코퍼스를 다시 로드하므로, 여러 후보를 한 번에 볼
    때는 `near_duplicate_pairs`를 쓴다(#382).
    """
    if not study_store.available():
        return []
    return study_overlap.nearest(_overlap_index(runtime), ident, top_k)


def near_duplicate_pairs(
    runtime: str | Path, idents: list[str], top_k: int = study_overlap.DEFAULT_TOP_K
) -> dict[str, list[dict]]:
    """``idents`` 각각의 근사중복 상위 K — ``{id: [{id, overlap}]}``, 빈 결과는 뺀다.

    자문 성격·순위·판정 불가 제외는 `near_duplicates`와 같은 계약이고, 다른 것은
    코퍼스를 **한 번만** 확보한다는 것뿐이다(#382). 후보마다 단건 질의를 부르면 단건
    용으로 옳은 "1회 로드 + N 비교"가 N번 반복되며 비용이 제곱이 된다.

    대칭 평균의 역방향도 재계산하지 않는다 — 각 후보가 한 번씩 질의가 되므로 반대
    방향은 그 패스에서 이미 나왔고, 전치로 재사용한다(`study_overlap.nearest_all`).
    """
    if not study_store.available():
        return {}
    return study_overlap.nearest_all(_overlap_index(runtime), idents, top_k)


def set_layer(runtime: str | Path, ident: str, layer: str | None) -> None:
    """후보의 인식층(정보/지식/지혜)을 기록한다(Epic #189 U5 — 승격 판정 결과)."""
    if not study_store.available():
        return
    study_store.set_layer(runtime, ident, layer)


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


def list_candidates(runtime: str | Path) -> list[dict]:
    """inbox의 후보를 ``[{id, date, snippet, source, recurrence}]``로 반환한다(최신 우선).

    **분류 축(주제·타입·인식층)은 후보에 없다** — 스니펫 본문뿐이다. 커맨드의
    `<topic>`·`--type`·`--layer` 인자는 이 필드들을 거르는 것이 아니라 스니펫을 읽고
    판정해 좁히는 필터다(문서가 필드 필터로 읽히면 실행 불가능한 계약이 된다).
    """
    if not study_store.available():
        return []
    return study_store.list_candidates(runtime)


def candidate_groups(runtime: str | Path) -> list[dict]:
    """파일 그룹 헤더 ``[{source, count}]``만 반환한다(#383).

    ``list_candidates``를 source로 묶은 것과 그룹·건수·순서가 일치하되 **스니펫 본문을
    싣지 않는다** — 판정 동선의 1단(어느 그룹을 펼칠지 고르기)이 전량 인출 없이 선다.
    """
    if not study_store.available():
        return []
    return study_store.candidate_groups(runtime)


def list_candidates_by_source(runtime: str | Path, source: str) -> list[dict]:
    """한 파일 그룹의 후보만 ``list_candidates``와 동일한 shape·정렬로 반환한다(#383).

    ``source``는 **저장값 정확 일치**로 매칭된다 — 호출부가 ``_sanitize``를 통과시킨 값을
    넘긴다(저장 시 같은 정규화를 거쳤다).
    """
    if not study_store.available():
        return []
    return study_store.list_candidates_by_source(runtime, source)


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


def dedup_miss(
    runtime: str | Path, concepts: list[str], captured: str | None = None
) -> dict | None:
    """뒤늦게 발견한 중복을 저널에 남긴다(#393) — 관측 기록, 원장·후보 무변경.

    "이 두 개념이 같은 말이었다"를 승격 뒤에 알아챈 사건이다. 자문이 놓친 것이므로
    의미 기반(임베딩) 도입 필요성의 **직접 근거**가 된다. 지금은 어휘 겹침만 재는 지표라
    "번들 검증은 엄격 모드로"와 "지식 저장소 확인은 strict 옵션을"처럼 같은 말인데 겹치는
    토큰이 없으면 0점이고, 그런 쌍이 실제로 얼마나 되는지는 값싸게 잴 수 없다(#387).

    **건수만 센다** — 기록 시점에 그때의 지표 순위를 계산해 넣지 않는다. 쌓인 사례 자체가
    골든셋이 되어 그때 오프라인으로 한 번에 평가하는 편이 낫고, 실시간 계산은 목적이
    아니다. 판정하지 않으므로 종료코드로 다투지 않고 기록만 남긴다.
    """
    if len(concepts) < 2:
        return None
    if not study_store.available():
        return None
    entry = {"concepts": list(concepts), "captured": captured}
    journal_append(runtime, "dedup_miss", captured or "-", concepts=list(concepts))
    return entry


def dedup_stats(runtime: str | Path) -> dict:
    """중복 판정 실수요 집계(#393) — ``{promoted: {...}, misses: N, cases: [...]}``.

    ``promoted``는 승격의 mode별 계수(분모), ``misses``는 뒤늦게 발견한 중복 건수(분자)다.
    비율·문턱·제안은 내지 않는다 — 관측이고 판정은 사람이 한다.
    """
    counts = {"update": 0, "create": 0, "unstated": 0}
    cases: list[dict] = []
    if not study_store.available():
        return {"promoted": counts, "misses": 0, "cases": cases}
    for event in study_store.events_with_action(runtime, "promoted"):
        mode = event.get("mode")
        counts[mode if mode in ("update", "create") else "unstated"] += 1
    for event in study_store.events_with_action(runtime, "dedup_miss"):
        cases.append(
            {
                "ts": event.get("ts"),
                "concepts": event.get("concepts", []),
                "captured": None if event.get("id") == "-" else event.get("id"),
            }
        )
    return {"promoted": counts, "misses": len(cases), "cases": cases}


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


def record(
    runtime: str | Path,
    ident: str,
    status: str,
    ref: str | None = None,
    layer: str | None = None,
    mode: str | None = None,
) -> None:
    """id를 promoted/discarded로 원장에 기록한다(이미 있으면 무시).

    기록은 후보가 잡힌 스코프의 런타임 원장이 정본이고, vault 옵트인 시 공유(유저 스코프)
    원장에도 write-through한다. 교차 승격(#91 §4)은 이 함수로 원 스코프에 기록하되
    ``ref``에 vault 개념 경로를 담는 규약이다. ``layer``(정보/지식/지혜)가 주어지면 promote
    이벤트에 함께 새겨 후보 드레인 후에도 저널에 인식층 provenance가 남는다(#189 U5).

    ``mode``(create/update)는 승격이 신설이었는지 **기존 개념 흡수**였는지다(#393). 흡수는
    "이미 있다"를 알아챈 사건이라 중복 판정 실수요의 분모가 된다. apply 시점에 남길 수
    없어(``okf_promote``는 캡처 런타임 무-import) 커맨드가 아는 값을 여기로 넘긴다.
    미지정은 **미상**으로 두고 계수하지 않는다 — path 실존으로 암묵 판별하지 않는다(#351).
    """
    if status not in ("promoted", "discarded"):
        raise ValueError(f"알 수 없는 status: {status}")
    if not study_store.available():
        return
    # A2′(#131): 블록 id + 자식 줄-해시를 함께 원장에 — 미래에 같은 줄이 **다른 그룹핑**
    # 으로 재캡처돼도 줄-단위로 dedup되어 재부상하지 않는다(ledger 연속성).
    children = [h for h in study_store.candidate_lines(runtime, ident) if h != ident]
    source = study_store.candidate_source(runtime, ident)  # 파일 타임라인 병기(#369)
    study_store.insert_resolution(runtime, ident, status, ref)
    journal_append(runtime, status, ident, ref=ref, layer=layer, source=source, mode=mode)
    for child in children:
        study_store.insert_resolution(runtime, child, status, None)
    shared = _global_ledger_root(runtime)
    if shared is not None:
        study_store.insert_resolution(shared, ident, status, ref)
        for child in children:
            study_store.insert_resolution(shared, child, status, None)
