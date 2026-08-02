"""study 오케스트레이션 CLI (S5, #77).

``/study`` 커맨드·승격 스킬이 부르는 **기계적 조작**을 제공한다. 판정(어떤 후보를
어떤 개념으로 만들지)은 모델의 몫이고, 여기서는 목록·원장·드레인·디스패치만 한다.

  study list     <project> [--by-file]                  후보 JSON(평탄 | 파일 그룹 뷰)
  study resolve  <project> (--id ID [--id ...] | --source PATH) --status S [--ref R] [--layer L]
                                                         원장 기록 + inbox 드레인(일괄 가능)
  study clear    <project>                              현재 후보 전부 discard
  study dispatch <project> --source S --concept-{path,type,topic,layer} <vals>
                                                         핸들러 실행(경로·git·trust 게이트)
  study scan     <project> [--enqueue]                   미큐잉 후보 결정론 탐지(+재적재)
  study log      <project> [--limit N]                    이벤트 저널(capture/promote/discard)
  study near     <project> [--top-k N]                    근사중복 자문(가까운 상위 K + 거리)
  study near-bundle <bundle> --snippet S --layer L [--top-k N]  후보↔같은 층 번들 근사중복(자문)
  study migrate  [<project>]                              vault .okf-study → 유저 스코프 멱등 이동
  study prune    [<project>] [--dry-run]                  기적재 노이즈 후보 정리(원장 무기록 drop)

``dispatch``는 trust 미승인 핸들러가 있으면 결과에 안내를 붙인다(가시적 저하) —
개념은 이미 스킬이 로컬 번들에 승격·검증했고, 여기서 핸들러만 보류된다.

``scan``(#91 V6, #20)은 메모리 파일의 **개념 블록**(#131)을 내용해시로 원장∪inbox와
차집합해 **파이프라인에 들어온 적 없는 후보**를 찾는다. ``--enqueue``는 멱등
재적재다 — discard된 id는 원장이 영구 차단하고, 승격·디스패치는 하지 않는다
(훅과 같은 계층의 기계 큐잉만).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import okf_layers
import okf_vault
import study_blocks
import study_dispatch
import study_inbox
import study_legacy
import study_scope
import study_simhash
import study_store
import study_trust


def memory_dirs(project: str | Path) -> list[Path]:
    """스캔 대상 메모리 디렉토리 — L0 명시 후보 + 기본형 글롭(전 프로젝트)."""
    dirs = [Path(d) for d in study_scope.memory_dir_candidates(project)]
    config = Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
    projects = config / "projects"
    if projects.is_dir():
        for child in sorted(projects.iterdir()):
            memory = child / "memory"
            if memory.is_dir():
                dirs.append(memory)
    return dirs


def _scope(project: str | Path) -> tuple[str | None, str]:
    """(promote_target, runtime_root) 해소 — 인박스는 runtime_root, 설정·핸들러는
    promote_target(#114). 스코프 미해소(설정·vault 없음)면 런타임은 in-repo로 폴백해
    바 프로젝트의 인박스 조회를 유지한다(무회귀)."""
    scope = study_scope.resolve_capture(project)
    runtime = scope["runtime_root"] or str(Path(project) / ".okf-study")
    return scope["target"], runtime


def scan_memory(
    project: str | Path, runtime: str | Path | None = None, enqueue: bool = False
) -> dict:
    """미큐잉 후보를 결정론적으로 탐지(+선택 재적재)한다. 승격은 하지 않는다.

    메모리 디렉토리는 현재 위치(``project``)의 L0 설정·글롭에서, 인박스·원장은
    ``runtime``(미지정 시 해소)에서 본다 — vault/폴백이면 유저 스코프(#114).
    """
    if runtime is None:
        runtime = _scope(project)[1]
    labels = study_blocks.effective_labels(study_scope.declared_noise_labels(project))  # #370
    known = {c["id"] for c in study_inbox.list_candidates(runtime)} if runtime else set()
    unqueued: list[dict] = []
    seen: set[str] = set()
    scanned: list[tuple[str, str]] = []  # (source, text) — --enqueue의 스냅샷 갱신용(#369)
    files = 0
    for directory in memory_dirs(project):
        for path in sorted(directory.rglob("*.md")):
            if not path.is_file():
                continue
            files += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned.append((str(path), text))
            for block in study_blocks.concept_blocks(text, labels=labels):  # 개념 블록 단위(#131)
                snippet = " ".join(block)
                if not snippet:
                    continue
                ident = study_inbox.content_hash(snippet)[:12]
                if ident in seen or ident in known:
                    continue
                line_hashes = [study_inbox.content_hash(line)[:12] for line in block]
                if runtime and study_inbox.block_resolved(runtime, ident, line_hashes):
                    continue  # 블록/자식 전부 promoted·discarded — 영구 제외
                seen.add(ident)
                unqueued.append(
                    {"id": ident, "snippet": snippet, "source": str(path), "lines": line_hashes}
                )
    enqueued: list[str] = []
    if enqueue and runtime:
        for cand in unqueued:
            study_inbox.append(runtime, cand["snippet"], cand["source"], line_hashes=cand["lines"])
            enqueued.append(cand["id"])
        # #369 — 적재를 마친 파일의 추적 스냅샷을 동시 갱신한다(다음 훅 캡처의 전이
        # 이중 계수 방지). 관측 전용 scan(무 enqueue)은 상태를 바꾸지 않는다.
        for source, text in scanned:
            study_inbox.track_file(runtime, source, text, labels=labels)
    return {"scanned_files": files, "unqueued": unqueued, "enqueued": enqueued}


def _load_study(project: str | Path) -> tuple[str, list[dict]]:
    config = Path(project) / ".okf-wiki.json"
    data = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    study = (data.get("study") if isinstance(data, dict) else None) or {}
    return study.get("capture", "off"), study.get("handlers") or []


def cmd_list(args) -> int:
    _promote, runtime = _scope(args.project)
    cands = study_inbox.list_candidates(runtime) if runtime else []
    if args.by_file:
        # 파일 그룹 뷰(#257) — 리뷰 결정 단위(파일)로 묶되 후보 전 필드를 보존해
        # provenance·후보별 resolve 소비를 유지한다. 그룹은 캡처 스냅샷의 누적이라
        # 파일의 현재 상태가 아니다(같은 줄의 편집 전 후보가 공존할 수 있다).
        groups: dict[str, list[dict]] = {}
        for cand in cands:
            groups.setdefault(cand["source"], []).append(cand)
        by_file = [
            {"source": source, "count": len(items), "candidates": items}
            for source, items in groups.items()
        ]
        print(json.dumps(by_file, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(cands, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(args) -> int:
    _promote, runtime = _scope(args.project)
    if args.source is not None:
        # 파일 단위 일괄(#258): 저장 source는 _sanitize 통과본 — 동일 정규화 후 **정확
        # 문자열 일치**. 경로 해석(Path.resolve·실존 검사)은 금물 — rename·삭제된 옛
        # 경로의 잔존 후보 일괄 정리가 주 용례다. dispatch의 --source(캡처 채널)와는
        # 다른 축이다(이쪽은 candidate.source 컬럼).
        wanted = study_inbox._sanitize(args.source)
        cands = study_inbox.list_candidates(runtime) if runtime else []
        ids = [c["id"] for c in cands if c["source"] == wanted]
        if not ids:
            # 매칭 0건은 무음 성공이 아니라 가시적 실패 — 현존 source를 보여 오타를 드러낸다
            print(
                json.dumps(
                    {
                        "error": f"source 일치 후보 없음: {wanted}",
                        "sources": sorted({c["source"] for c in cands}),
                    },
                    ensure_ascii=False,
                )
            )
            return 1
    else:
        ids = list(dict.fromkeys(args.id))
        # 존재 검사(#305). 없으면 오타·환각 id가 **exit 0으로** 원장·저널에 promoted로
        # 기록되고, 진짜 후보는 인박스에 남아 doctor 이력이 거짓이 된다. 바로 위
        # `--source` 경로는 무매칭을 가시적 실패로 다루므로 같은 커맨드 안에서 비대칭이었다.
        #
        # **all-or-nothing**이다 — 일부만 적용하면 어디까지 기록됐는지 알 수 없는 상태가
        # 남는다. 그 상태의 복구는 원장을 손으로 읽는 일이 된다.
        unknown = [i for i in ids if not (runtime and study_store.has_candidate(runtime, i))]
        if unknown:
            print(
                json.dumps(
                    {
                        "error": (
                            f"존재하지 않는 후보 id {len(unknown)}건 — 아무것도 기록하지 않았다"
                        ),
                        "unknown_ids": unknown,
                        "known_ids": [i for i in ids if i not in unknown],
                    },
                    ensure_ascii=False,
                )
            )
            return 1
    dropped: list[str] = []
    if runtime:
        for ident in ids:
            if args.layer:
                study_inbox.set_layer(runtime, ident, args.layer)  # 후보에 인식층 영속(#189 U5)
            # 원장·저널은 id별 계약 유지(측정 원자 = 블록 id) — 일괄 + 단일 --ref는
            # "N후보 → 1개념 병합 승격"을 의미한다(#258).
            study_inbox.record(runtime, ident, args.status, args.ref, layer=args.layer)
        dropped = study_inbox.drop(runtime, ids)
    print(
        json.dumps(
            {
                # "id"는 기존 단일-호출 출력 계약의 하위호환 — 배치에서는 null(DA #262)
                "id": ids[0] if len(ids) == 1 else None,
                "ids": ids,
                "status": args.status,
                "layer": args.layer,
                "dropped": dropped,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_clear(args) -> int:
    _promote, runtime = _scope(args.project)
    discarded: list[str] = []
    if runtime:
        for cand in study_inbox.list_candidates(runtime):
            study_inbox.record(runtime, cand["id"], "discarded")
        discarded = study_inbox.clear(runtime)
    print(json.dumps({"discarded": discarded}, ensure_ascii=False))
    return 0


def cmd_scan(args) -> int:
    _promote, runtime = _scope(args.project)
    print(json.dumps(scan_memory(args.project, runtime, enqueue=args.enqueue), ensure_ascii=False))
    return 0


def cmd_prune(args) -> int:
    # 기적재 노이즈 정리(#256) — **원장 기록 없는 drop**: 재유입은 추출 필터가 차단하므로
    # discard(영구 원장 + 공유 원장 write-through)는 노이즈 id 오염이 된다. 저널은
    # per-id 대신 집계 1건 — doctor의 최근-이력 뷰를 수백 행으로 덮지 않는다.
    _promote, runtime = _scope(args.project)
    labels = study_blocks.effective_labels(study_scope.declared_noise_labels(args.project))  # #370
    matches: list[dict] = []
    if runtime:
        matches = [
            c
            for c in study_inbox.list_candidates(runtime)
            if study_blocks.is_noise_snippet(c["snippet"], labels=labels)
        ]
    if args.dry_run:
        # 오폭 검토(#263): is_noise_snippet은 텍스트 근사라 `--- ` 접두 실사실·diff 헤더
        # 인용이 섞일 수 있다 — drop·저널 없이 매치 원문을 보인다. 키는 실행 모드와
        # 분리("pruned" 미사용) — 매치 0건 실행({"pruned": []})과 혼동하지 않는다.
        print(json.dumps({"dry_run": True, "matches": matches}, ensure_ascii=False))
        return 0
    removed = study_inbox.drop(runtime, [c["id"] for c in matches]) if runtime else []
    if removed:
        study_inbox.journal_append(runtime, "prune", "-", count=len(removed))
    print(json.dumps({"pruned": removed}, ensure_ascii=False))
    return 0


def cmd_near(args) -> int:
    # 근사중복 자문(#133) — 재서술 후보를 트리아지에서 표면화한다(자동병합·게이팅 없음).
    _promote, runtime = _scope(args.project)
    pairs: dict[str, list[str]] = {}
    if runtime:
        for cand in study_inbox.list_candidates(runtime):
            dups = study_inbox.near_duplicates(runtime, cand["id"], top_k=args.top_k)
            if dups:
                pairs[cand["id"]] = dups
    print(json.dumps(pairs, ensure_ascii=False, indent=2))
    return 0


def _line_path_gist(line: str) -> tuple[str, str]:
    """``okf context`` 개념 줄 ``<경로> [<type>] — <핵심>``을 (경로, 핵심)으로 쪼갠다."""
    path = line.split(" [", 1)[0].strip()
    gist = line.split(" — ", 1)[1].strip() if " — " in line else ""
    return path, gist


def same_layer_near(snippet: str, layer_lines: list[str], top_k: int) -> list[dict]:
    """``snippet``을 **같은 층** 번들 개념 줄들과 대조해 가까운 **상위 K**를 반환한다.

    자문 전용 — 자동병합·게이팅 없음(#133 철학). 반환은 거리 오름차순
    ``[{path, gist, distance}]``이고, SimHash는 근사라 오탐·누락이 있으므로 판정은
    사람·모델이 한다(재확인·supersede·별개).

    임계 필터가 아니라 상위 K인 이유는 `near_duplicates`와 같다(#306). 지문 판정
    불가(토큰 0개)는 양쪽 모두 제외한다.
    """
    target = study_simhash.fingerprint(snippet)
    if target is None:
        return []
    hits: list[dict] = []
    for line in layer_lines:
        path, gist = _line_path_gist(line)
        fp = study_simhash.fingerprint(gist or path)
        if fp is None:
            continue
        hits.append({"path": path, "gist": gist, "distance": study_simhash.hamming(target, fp)})
    return sorted(hits, key=lambda h: (h["distance"], h["path"]))[:top_k]


def cmd_near_bundle(args) -> int:
    # 같은 층 번들 근사중복 자문(Epic #189 U3) — "동일 정보면 다시 습득 안 함"의 신호.
    # exact 내용해시 하드 게이트(재부상 차단)는 불변, 여기선 semantic 자문만 더한다.
    sections = okf_layers.bundle_layer_sections(args.bundle)
    hits = same_layer_near(args.snippet, sections.get(args.layer, []), args.top_k)
    print(json.dumps({"layer": args.layer, "near": hits}, ensure_ascii=False, indent=2))
    return 0


def cmd_log(args) -> int:
    # 이벤트 저널(capture/promote/discard 이력) — 비-git 스테이징의 순서·로그(#114 U5)
    _promote, runtime = _scope(args.project)
    events = study_inbox.read_journal(runtime, limit=args.limit) if runtime else []
    print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


def _import_into(dst: str, cands: list[dict], resolutions: list, moved: dict) -> None:
    """레거시 후보·원장을 dst study.db로 dedup 이관한다(단일 줄 → 단일 줄 블록, 연속성).

    옛 후보 스니펫은 단일 줄이라 id = content_hash(snippet)[:12]가 자식 줄-해시와 같다
    → 재부상 차단(A2′)이 자동으로 이어진다.

    이관은 **insert-only**(#255) — dst에 이미 사는 후보를 append로 재조우시키면
    레거시 source(구분자 없는 옛 줄이면 빈 문자열)가 라이브 값을 덮고 recurrence
    (재등장 신호)가 부풀므로, 기존 id는 건드리지 않는다.
    """
    for cand in cands:
        if study_inbox.is_resolved(dst, cand["id"]) or study_store.has_candidate(dst, cand["id"]):
            continue
        before = len(study_inbox.list_candidates(dst))
        study_inbox.append(dst, cand["snippet"], cand["source"], date=cand["date"])
        if len(study_inbox.list_candidates(dst)) > before:
            moved["candidates"] += 1
            if study_blocks.is_noise_snippet(cand["snippet"]):
                moved["noise"] += 1  # 직이관은 추출 필터를 우회한다(#263) — prune 검토 신호
    for ident, status, ref in resolutions:
        if not study_inbox.is_resolved(dst, ident):
            study_inbox.record(dst, ident, status, ref)
            moved["ledger"] += 1


def cmd_migrate(args) -> int:
    # 레거시 스테이징을 유저 스코프 study.db로 멱등 이관(#114 U4 · #134 U5). 2원천:
    # (a) pre-0.4 vault <vault>/.okf-study, (b) 0.4.x 유저 스코프 markdown. 둘 다 옛 3종 파일.
    import shutil

    dst = str(study_scope.user_scope_runtime())
    vault, reason = okf_vault.vault_state()
    moved = {"candidates": 0, "ledger": 0, "noise": 0, "trust": False, "sources": []}

    # (b) 유저 스코프 자체의 옛 markdown → 같은 디렉토리 study.db로 인플레이스 이관 후 소모.
    if study_legacy.has_legacy(dst):
        _import_into(
            dst, study_legacy.read_candidates(dst), study_legacy.read_resolutions(dst), moved
        )
        study_legacy.remove_legacy(dst)
        moved["sources"].append("user-scope-markdown")

    # (a) vault <vault>/.okf-study → 유저 스코프. markdown·study.db·trust 모두 흡수 후 rmtree.
    # URL 모드(#153 U2-5): 관리형 clone은 건너뛴다 — clone의 .okf-study는 목적지 repo가
    # 커밋한 git-추적 자원이라 rmtree하면 clone이 dirty(추적 파일 삭제)가 되고, 애초에
    # URL 모드는 신설이라 이관할 pre-0.4 레거시 런타임이 없다.
    if vault is not None and not okf_vault.is_managed_clone(vault):
        src = Path(vault) / ".okf-study"
        if src.exists():
            _import_into(
                dst, study_legacy.read_candidates(src), study_legacy.read_resolutions(src), moved
            )
            _import_into(
                dst, study_inbox.list_candidates(src), study_store.list_resolutions(src), moved
            )
            src_trust, dst_trust = src / "trust", Path(dst) / "trust"
            if src_trust.is_file() and not dst_trust.is_file():
                dst_trust.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_trust, dst_trust)
                moved["trust"] = True
            shutil.rmtree(src)  # vault를 순수 목적지로 되돌린다
            moved["sources"].append("vault")

    result = {"migrated": bool(moved["sources"]), "moved": moved}
    if moved["noise"]:
        # 명령 전체 경로는 인용하지 않는다 — 이관 목적지(유저 스코프)에 닿는 project
        # 인자가 배치마다 달라(#263 스코프 함정) 오도가 된다. doctor가 매 실행 재안내한다.
        result["note"] = (
            f"이관 후보(유저 스코프)에 기적재 노이즈 {moved['noise']}건 — 캡처가 유저 "
            "스코프로 해소되는 위치에서 `study prune --dry-run`으로 확인 후 정리"
        )
    if not moved["sources"]:
        result["reason"] = reason or "이관할 레거시 스테이징 없음"
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _verdict_payload(
    skipped: list[dict], *, ran: list | None = None, failed: list | None = None, unwired=False
) -> dict:
    """미반영 판결 — ``reflected``·``blockers``·``note``(#266 U2).

    두 쓰기 경로(``/study``·``/okf-promote``)가 이 지점으로 수렴하므로 여기가 "왜 원격에
    안 갔는가"를 말할 유일한 자리다. 그런데 지금까지 note를 붙이는 조건이 trust 하나뿐이라
    **가장 흔한 상태**(스캐폴드 직후 = 미추적)가 완전 무음이었다.

    ``blockers[].code``가 소비처의 분기 축이다 — 자연어 ``note``는 사람용 표시로 남긴다.
    문자열을 판정에 쓰면 문구를 다듬는 일이 고장이 되고, 게이트로 잠글 수도 없다.

    ``reflected``는 "차단 없이 전 핸들러가 exit 0"이다 — **push 영수증이 아니다**(#237:
    계약상 0은 성공일 뿐이고 정본 템플릿조차 '변경 0'이면 push 없이 0을 낸다).
    """
    # 차단 상태는 셋이다: 미배선 · 게이트 반려(`skipped`) · 실행 실패(`failed`).
    # `failed`가 이 목록 밖에 있던 동안, 배선·커밋·trust가 전부 끝난 정상 상태에서
    # 핸들러가 죽으면 `reflected: false`인데 `blockers`도 `note`도 비어 복구 지시가
    # 하나도 나가지 않았다(#296). 세 상태가 같은 축으로 말해져야 한다.
    codes = (
        [{"code": study_dispatch.CODE_UNWIRED, "reason": "핸들러 없음", "name": None}]
        if unwired
        else [
            {"code": s.get("code", ""), "reason": s.get("reason", ""), "name": s.get("name")}
            for s in [*skipped, *(failed or [])]
        ]
    )
    blockers = [dict(c, recovery=study_dispatch.BLOCKERS.get(c["code"], "")) for c in codes]
    payload = {
        "reflected": bool(ran) and not blockers and not failed,
        "blockers": blockers,
    }
    if unwired:
        payload.update({"ran": [], "failed": [], "skipped": []})
    if blockers:
        # 사람용 한 줄 — 같은 사유가 여러 핸들러에 걸리면 한 번만 말한다.
        payload["note"] = " / ".join(
            dict.fromkeys(b["recovery"] for b in blockers if b["recovery"])
        )
    return payload


def cmd_dispatch(args) -> int:
    # 설정·핸들러·해시 루트는 승격 대상 repo, trust 파일은 런타임 루트(#114).
    promote, runtime = _scope(args.project)
    repo = promote or str(args.project)
    rt = runtime or str(Path(args.project) / ".okf-study")
    capture, handlers = _load_study(repo)
    if not handlers:
        # 조기 종료는 유지한다 — 걷어내면 `note` 계약과 그 문자열에 걸린 소비처가 깨진다.
        # 대신 그 자리의 payload를 채운다: 이 상태(미배선)가 Epic이 지목한 본체인데
        # 지금까지 "무엇을 하면 되는지"가 없었다(#266 U2).
        print(json.dumps(_verdict_payload([], unwired=True), ensure_ascii=False))
        return 0
    item = {
        "source": args.source,
        "project": repo,
        "concept": {
            "path": args.concept_path,
            "type": args.concept_type,
            "topic": args.concept_topic,
            "layer": args.concept_layer,
        },
    }
    check = study_trust.make_trust_check(repo, handlers, capture, rt)
    result = study_dispatch.dispatch(repo, item, handlers, check)
    result.update(_verdict_payload(result["skipped"], ran=result["ran"], failed=result["failed"]))
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="study", description="study 오케스트레이션")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # 층 어휘는 LAYERS.md 기계 판독 블록이 단일원천 — 여기 하드코딩하지 않는다.
    # 입구에서 막지 않으면 한국어 라벨이 원장·저널·OKF_CONCEPT_LAYER까지 조용히 흐른다(#278).
    layers = okf_layers.load_layers_spec()["values"]

    lst = sub.add_parser("list", help="후보 목록(JSON) — --by-file이면 파일 그룹 뷰")
    lst.add_argument("project", nargs="?", default=".")
    lst.add_argument("--by-file", action="store_true", dest="by_file")

    res = sub.add_parser("resolve", help="원장 기록 + inbox 드레인(다중 --id·--source 일괄)")
    res.add_argument("project", nargs="?", default=".")
    sel = res.add_mutually_exclusive_group(required=True)
    sel.add_argument("--id", action="append", help="후보 id(반복 가능) — --source와 배타")
    sel.add_argument(
        "--source",
        help="candidate.source(파일 경로) 정확 일치 일괄 — dispatch --source(캡처 채널)와 다름",
    )
    res.add_argument("--status", required=True, choices=["promoted", "discarded"])
    res.add_argument("--ref")
    res.add_argument("--layer", choices=layers, help="인식층 — 저널·후보에 provenance로 새김")

    clr = sub.add_parser("clear", help="후보 전부 discard")
    clr.add_argument("project", nargs="?", default=".")

    dsp = sub.add_parser("dispatch", help="핸들러 실행(게이트)")
    dsp.add_argument("project", nargs="?", default=".")
    # 캡처 채널(→ OKF_TRIGGER). resolve --source(파일 경로)와 다르다. 계약 문서가 약속하는
    # 어휘와 여기 choices가 같아야 한다 — 소비처가 없는 값으로 분기하지 않도록(게이트가 대조).
    dsp.add_argument("--source", default="manual", choices=["manual"])
    dsp.add_argument("--concept-path", default="")
    dsp.add_argument("--concept-type", default="")
    dsp.add_argument("--concept-topic", default="")
    dsp.add_argument("--concept-layer", default="", choices=layers)

    scn = sub.add_parser("scan", help="미큐잉 후보 탐지(+--enqueue 재적재)")
    scn.add_argument("project", nargs="?", default=".")
    scn.add_argument("--enqueue", action="store_true")

    lg = sub.add_parser("log", help="이벤트 저널(capture/promote/discard 이력) 출력")
    lg.add_argument("project", nargs="?", default=".")
    lg.add_argument("--limit", type=int, default=None)

    nr = sub.add_parser("near", help="근사중복 자문(SimHash 해밍거리) — 재서술 후보 표면화")
    nr.add_argument("project", nargs="?", default=".")
    nr.add_argument("--top-k", type=int, default=study_simhash.DEFAULT_TOP_K)

    nb = sub.add_parser("near-bundle", help="후보 스니펫↔같은 층 번들 개념 근사중복(자문)")
    nb.add_argument("bundle", help="번들 디렉터리 경로")
    nb.add_argument("--snippet", required=True, help="대조할 후보 스니펫")
    nb.add_argument("--layer", required=True, choices=layers, help="후보의 인식층(같은 층만 대조)")
    nb.add_argument("--top-k", type=int, default=study_simhash.DEFAULT_TOP_K)

    mig = sub.add_parser("migrate", help="기존 vault .okf-study 런타임 → 유저 스코프 멱등 이동")
    mig.add_argument("project", nargs="?", default=".")

    prn = sub.add_parser("prune", help="기적재 노이즈 후보 정리 — 원장 무기록 drop(#256)")
    prn.add_argument("project", nargs="?", default=".")
    prn.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="매치 목록만 출력(삭제·저널 없음) — 텍스트 근사 오폭 검토(#263)",
    )

    args = ap.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "resolve": cmd_resolve,
        "clear": cmd_clear,
        "dispatch": cmd_dispatch,
        "scan": cmd_scan,
        "log": cmd_log,
        "near": cmd_near,
        "near-bundle": cmd_near_bundle,
        "migrate": cmd_migrate,
        "prune": cmd_prune,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
