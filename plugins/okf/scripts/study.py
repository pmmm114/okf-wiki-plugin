"""study 오케스트레이션 CLI (S5, #77).

``/study`` 커맨드·승격 스킬이 부르는 **기계적 조작**을 제공한다. 판정(어떤 후보를
어떤 개념으로 만들지)은 모델의 몫이고, 여기서는 목록·원장·드레인·디스패치만 한다.

  study list     <project>                              후보를 JSON으로 출력
  study resolve  <project> --id ID --status S [--ref R] 원장 기록 + inbox 드레인
  study clear    <project>                              현재 후보 전부 discard
  study dispatch <project> --source S --concept-path P --concept-type T --concept-topic X
                                                         핸들러 실행(경로·git·trust 게이트)
  study scan     <project> [--enqueue]                   미큐잉 후보 결정론 탐지(+재적재)
  study log      <project> [--limit N]                    이벤트 저널(capture/promote/discard)
  study near     <project> [--threshold N]                근사중복 자문(SimHash 해밍거리)
  study migrate  [<project>]                              홈 .okf-study → 유저 스코프 멱등 이동

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

import okf_home
import okf_inbox
import study_blocks
import study_dispatch
import study_legacy
import study_simhash
import study_store
import study_trust


def _memory_dirs(project: str | Path) -> list[Path]:
    """스캔 대상 메모리 디렉토리 — L0 명시 후보 + 기본형 글롭(전 프로젝트)."""
    dirs = [Path(d) for d in okf_home.memory_dir_candidates(project)]
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
    promote_target(#114). 스코프 미해소(설정·홈 없음)면 런타임은 in-repo로 폴백해
    바 프로젝트의 인박스 조회를 유지한다(무회귀)."""
    scope = okf_home.resolve_capture(project)
    runtime = scope["runtime_root"] or str(Path(project) / ".okf-study")
    return scope["target"], runtime


def scan_memory(
    project: str | Path, runtime: str | Path | None = None, enqueue: bool = False
) -> dict:
    """미큐잉 후보를 결정론적으로 탐지(+선택 재적재)한다. 승격은 하지 않는다.

    메모리 디렉토리는 현재 위치(``project``)의 L0 설정·글롭에서, 인박스·원장은
    ``runtime``(미지정 시 해소)에서 본다 — 홈/폴백이면 유저 스코프(#114).
    """
    if runtime is None:
        runtime = _scope(project)[1]
    known = {c["id"] for c in okf_inbox.list_candidates(runtime)} if runtime else set()
    unqueued: list[dict] = []
    seen: set[str] = set()
    files = 0
    for directory in _memory_dirs(project):
        for path in sorted(directory.rglob("*.md")):
            if not path.is_file():
                continue
            files += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for block in study_blocks.concept_blocks(text):  # 개념 블록 단위(#131)
                snippet = " ".join(block)
                if not snippet:
                    continue
                ident = okf_inbox.content_hash(snippet)[:12]
                if ident in seen or ident in known:
                    continue
                line_hashes = [okf_inbox.content_hash(line)[:12] for line in block]
                if runtime and okf_inbox.block_resolved(runtime, ident, line_hashes):
                    continue  # 블록/자식 전부 promoted·discarded — 영구 제외
                seen.add(ident)
                unqueued.append(
                    {"id": ident, "snippet": snippet, "source": str(path), "lines": line_hashes}
                )
    enqueued: list[str] = []
    if enqueue and runtime:
        for cand in unqueued:
            okf_inbox.append(runtime, cand["snippet"], cand["source"], line_hashes=cand["lines"])
            enqueued.append(cand["id"])
    return {"scanned_files": files, "unqueued": unqueued, "enqueued": enqueued}


def _load_study(project: str | Path) -> tuple[str, list[dict]]:
    config = Path(project) / ".okf-wiki.json"
    data = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    study = (data.get("study") if isinstance(data, dict) else None) or {}
    return study.get("capture", "off"), study.get("handlers") or []


def cmd_list(args) -> int:
    _promote, runtime = _scope(args.project)
    cands = okf_inbox.list_candidates(runtime) if runtime else []
    print(json.dumps(cands, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(args) -> int:
    _promote, runtime = _scope(args.project)
    dropped: list[str] = []
    if runtime:
        okf_inbox.record(runtime, args.id, args.status, args.ref)
        dropped = okf_inbox.drop(runtime, [args.id])
    print(
        json.dumps({"id": args.id, "status": args.status, "dropped": dropped}, ensure_ascii=False)
    )
    return 0


def cmd_clear(args) -> int:
    _promote, runtime = _scope(args.project)
    discarded: list[str] = []
    if runtime:
        for cand in okf_inbox.list_candidates(runtime):
            okf_inbox.record(runtime, cand["id"], "discarded")
        discarded = okf_inbox.clear(runtime)
    print(json.dumps({"discarded": discarded}, ensure_ascii=False))
    return 0


def cmd_scan(args) -> int:
    _promote, runtime = _scope(args.project)
    print(json.dumps(scan_memory(args.project, runtime, enqueue=args.enqueue), ensure_ascii=False))
    return 0


def cmd_near(args) -> int:
    # 근사중복 자문(#133) — 재서술 후보를 트리아지에서 표면화한다(자동병합·게이팅 없음).
    _promote, runtime = _scope(args.project)
    pairs: dict[str, list[str]] = {}
    if runtime:
        for cand in okf_inbox.list_candidates(runtime):
            dups = okf_inbox.near_duplicates(runtime, cand["id"], threshold=args.threshold)
            if dups:
                pairs[cand["id"]] = dups
    print(json.dumps(pairs, ensure_ascii=False, indent=2))
    return 0


def cmd_log(args) -> int:
    # 이벤트 저널(capture/promote/discard 이력) — 비-git 스테이징의 순서·로그(#114 U5)
    _promote, runtime = _scope(args.project)
    events = okf_inbox.read_journal(runtime, limit=args.limit) if runtime else []
    print(json.dumps(events, ensure_ascii=False, indent=2))
    return 0


def _import_into(dst: str, cands: list[dict], resolutions: list, moved: dict) -> None:
    """레거시 후보·원장을 dst study.db로 dedup 이관한다(단일 줄 → 단일 줄 블록, 연속성).

    옛 후보 스니펫은 단일 줄이라 id = content_hash(snippet)[:12]가 자식 줄-해시와 같다
    → 재부상 차단(A2′)이 자동으로 이어진다.
    """
    for cand in cands:
        if okf_inbox.is_resolved(dst, cand["id"]):
            continue
        before = len(okf_inbox.list_candidates(dst))
        okf_inbox.append(dst, cand["snippet"], cand["source"], date=cand["date"])
        if len(okf_inbox.list_candidates(dst)) > before:
            moved["candidates"] += 1
    for ident, status, ref in resolutions:
        if not okf_inbox.is_resolved(dst, ident):
            okf_inbox.record(dst, ident, status, ref)
            moved["ledger"] += 1


def cmd_migrate(args) -> int:
    # 레거시 스테이징을 유저 스코프 study.db로 멱등 이관(#114 U4 · #134 U5). 2원천:
    # (a) pre-0.4 홈 <home>/.okf-study, (b) 0.4.x 유저 스코프 markdown. 둘 다 옛 3종 파일.
    import shutil

    dst = str(okf_home.user_scope_runtime())
    home, reason = okf_home.home_state()
    moved = {"candidates": 0, "ledger": 0, "trust": False, "sources": []}

    # (b) 유저 스코프 자체의 옛 markdown → 같은 디렉토리 study.db로 인플레이스 이관 후 소모.
    if study_legacy.has_legacy(dst):
        _import_into(
            dst, study_legacy.read_candidates(dst), study_legacy.read_resolutions(dst), moved
        )
        study_legacy.remove_legacy(dst)
        moved["sources"].append("user-scope-markdown")

    # (a) 홈 <home>/.okf-study → 유저 스코프. markdown·study.db·trust 모두 흡수 후 rmtree.
    if home is not None:
        src = Path(home) / ".okf-study"
        if src.exists():
            _import_into(
                dst, study_legacy.read_candidates(src), study_legacy.read_resolutions(src), moved
            )
            _import_into(
                dst, okf_inbox.list_candidates(src), study_store.list_resolutions(src), moved
            )
            src_trust, dst_trust = src / "trust", Path(dst) / "trust"
            if src_trust.is_file() and not dst_trust.is_file():
                dst_trust.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_trust, dst_trust)
                moved["trust"] = True
            shutil.rmtree(src)  # 홈을 순수 목적지로 되돌린다
            moved["sources"].append("home")

    result = {"migrated": bool(moved["sources"]), "moved": moved}
    if not moved["sources"]:
        result["reason"] = reason or "이관할 레거시 스테이징 없음"
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_dispatch(args) -> int:
    # 설정·핸들러·해시 루트는 승격 대상 repo, trust 파일은 런타임 루트(#114).
    promote, runtime = _scope(args.project)
    repo = promote or str(args.project)
    rt = runtime or str(Path(args.project) / ".okf-study")
    capture, handlers = _load_study(repo)
    if not handlers:
        print(json.dumps({"ran": [], "failed": [], "skipped": [], "note": "핸들러 없음"}))
        return 0
    item = {
        "source": args.source,
        "project": repo,
        "concept": {
            "path": args.concept_path,
            "type": args.concept_type,
            "topic": args.concept_topic,
        },
    }
    check = study_trust.make_trust_check(repo, handlers, capture, rt)
    result = study_dispatch.dispatch(repo, item, handlers, check)
    if any(s.get("reason") == "trust 미승인" for s in result["skipped"]):
        result["note"] = (
            "핸들러 로컬 미승인 — `/study --trust`(study_trust approve)로 승인 후 재실행"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="study", description="study 오케스트레이션")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lst = sub.add_parser("list", help="후보 목록(JSON)")
    lst.add_argument("project", nargs="?", default=".")

    res = sub.add_parser("resolve", help="원장 기록 + inbox 드레인")
    res.add_argument("project", nargs="?", default=".")
    res.add_argument("--id", required=True)
    res.add_argument("--status", required=True, choices=["promoted", "discarded"])
    res.add_argument("--ref")

    clr = sub.add_parser("clear", help="후보 전부 discard")
    clr.add_argument("project", nargs="?", default=".")

    dsp = sub.add_parser("dispatch", help="핸들러 실행(게이트)")
    dsp.add_argument("project", nargs="?", default=".")
    dsp.add_argument("--source", default="manual")
    dsp.add_argument("--concept-path", default="")
    dsp.add_argument("--concept-type", default="")
    dsp.add_argument("--concept-topic", default="")

    scn = sub.add_parser("scan", help="미큐잉 후보 탐지(+--enqueue 재적재)")
    scn.add_argument("project", nargs="?", default=".")
    scn.add_argument("--enqueue", action="store_true")

    lg = sub.add_parser("log", help="이벤트 저널(capture/promote/discard 이력) 출력")
    lg.add_argument("project", nargs="?", default=".")
    lg.add_argument("--limit", type=int, default=None)

    nr = sub.add_parser("near", help="근사중복 자문(SimHash 해밍거리) — 재서술 후보 표면화")
    nr.add_argument("project", nargs="?", default=".")
    nr.add_argument("--threshold", type=int, default=study_simhash.DEFAULT_THRESHOLD)

    mig = sub.add_parser("migrate", help="기존 홈 .okf-study 런타임 → 유저 스코프 멱등 이동")
    mig.add_argument("project", nargs="?", default=".")

    args = ap.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "resolve": cmd_resolve,
        "clear": cmd_clear,
        "dispatch": cmd_dispatch,
        "scan": cmd_scan,
        "log": cmd_log,
        "near": cmd_near,
        "migrate": cmd_migrate,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
