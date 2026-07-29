#!/usr/bin/env python3
"""층간 승격 파이프라인 (Epic #197 U3) — snapshot / apply, 결정적 게이트+집행.

모델의 제안 JSON(판정 산물)을 받아 LAYERS §9 금지를 **기계적으로** 게이트하고,
통과분만 집행한다(frontmatter 실체화 → 파일 쓰기 → validate --strict → 접지 린트 →
근거 사슬 감사 → log --kind Promotion → index --write). 판정하지 않는다 — 층 분류·
"새 인식" 평가는 사람+모델의 몫이고, 여기는 판정 산물의 원칙 준수만 검사한다.

탐색 출력은 입력이 아니다(EXPLORE.md 불변식 1) — 재료 무수정 검증의 해시는
``snapshot``이 직접 채집한다. 근사중복(near-bundle)도 게이트가 아니다(#189 결정 B,
자문) — 커맨드가 제안 단계에서 별도로 자문한다. study 모듈 무-import(core⊥study).

제안 계약(Epic #197 §3): ``{target_layer, path(신설, 번들 상대), type, description,
body, derived_from: [경로], materials: [{path, sha256}], resource?, allow_dangling?:
[경로], rubric?}``. 경로는 ``/`` 시작을 허용하며 내부적으로 정규화한다.

게이트 → §9 금지 매핑: 어휘·정초 엄격 하향(금지 5) · path 신설(금지 2, 재라벨 차단) ·
재료 해시 재대조(금지 3, 삭제·흡수·수정 차단) · derived_from 실존(금지 1, 근거 날조
차단 — 의도적 dangle은 allow_dangling 명시) · 정보층 출처 필수(§4 접지) · 상위층
비어있지 않은 derived_from(§4 접지).

경로의 **번들 경계**는 §9 금지가 아니라 위 제안 계약("번들 상대")의 전제다(#267) —
`..`가 든 경로는 번들 밖에 쓰이는데 validate·index는 번들 안만 보므로 탐지도 롤백도
되지 않는다. 침묵 실패라 집행 전에 반려한다. 판정은 엔진과 같은 형태를 쓴다.

CLI: ``okf_promote.py snapshot <bundle> --paths <p>…`` ·
``okf_promote.py apply <bundle> --proposals <file|->``. apply는 결과 JSON을 내고
반려가 있으면 exit 1. 엔진 실행은 bin/okf 셔틀 경유(stdlib 전용), 어휘·정초 순서는
okf_layers(LAYERS.md 기계 판독 블록)에서 로드한다(하드코딩 0).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import okf_layers

_HERE = os.path.dirname(os.path.abspath(__file__))
_OKF = os.path.join(_HERE, "..", "..", "bin", "okf")

_REQUIRED_TEXT_FIELDS = ("type", "description", "body")


def _okf_run(args: list[str]) -> str:
    proc = subprocess.run([_OKF, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"okf {args[0]} 실패(rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def norm_rel(path: str) -> str:
    """번들 상대 경로 정규화 — LAYERS 권장 절대(`/x.md`) 표기와 엔진 출력 표기
    (`x.md`)를 같은 키로 다룬다."""
    return path.strip().lstrip("/")


def escapes_bundle(bundle: str, rel: str) -> bool:
    """쓰기 대상이 번들 루트를 벗어나는가(#267) — 루트 기준 **실체** 판정.

    문자열 정규화(`..` 선두 검사)로는 부족하다. 심링크를 타고 나가는 경로는 문자열에
    `..`가 없지만 실제 쓰기는 밖이고, 엔진의 번들 순회는 심링크 디렉터리로 내려가지
    않아 그렇게 쓰인 파일을 **영영 보지 못한다** — `..` 탈출과 같은 등급의 침묵 실패다.

    엔진의 크로스링크 검사도 `..`를 보지만 그쪽은 *문서 안 링크 대상*이라 파일시스템을
    건드리지 않는다. 여기는 *실제 쓰기 위치*라 도메인이 다르다 — 같은 도메인인
    ``study_dispatch.resolve_command``(핸들러 실행 경로)와 같은 형태를 쓴다.
    루트도 함께 해소하므로 번들 자체가 심링크 아래 있어도 오탐이 없다.
    """
    root = Path(bundle).resolve()
    try:
        (root / rel).resolve().relative_to(root)
    except ValueError:
        return True
    return False


def sha256_file(fs_path: str) -> str:
    with open(fs_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def snapshot_materials(bundle: str, paths: list[str]) -> dict:
    """재료 내용 해시 채집(결정 F v2) — 제안 계약의 ``materials``가 된다.

    없는 경로는 오류로 표면화한다(조용한 누락 방지 — 없는 재료는 스냅샷이 아니라
    ``allow_dangling``의 몫이다).
    """
    materials = []
    missing = []
    for path in paths:
        rel = norm_rel(path)
        fs = os.path.join(bundle, rel)
        if not os.path.isfile(fs):
            missing.append(rel)
        else:
            materials.append({"path": rel, "sha256": sha256_file(fs)})
    return {"materials": materials, "missing": missing}


def gate_proposal(
    spec: dict,
    layer_map: dict,
    bundle: str,
    proposal: dict,
    batch_promoted: set[str] | None = None,
) -> list[str]:
    """§9 금지의 기계 게이트(순수 판정 — 번들은 읽기만 한다). 사유 목록을 반환하고
    빈 리스트면 통과. 판정(층 분류·새 인식)은 하지 않는다 — 원칙 준수 검사만.

    ``batch_promoted``는 같은 배치에서 방금 집행된 개념 집합 — §9 원칙 3("같은
    변경에서 먼저 승격")의 캐스케이드를 위해 그 재료에는 스냅샷 해시를 면제한다
    (파이프라인이 직접 쓴 파일이라 무결성이 자체 보장된다)."""
    reasons: list[str] = []
    batch_promoted = batch_promoted or set()
    order = spec["order"]
    rank = {value: index for index, value in enumerate(order)}
    rules = spec.get("rules", {})

    target_layer = proposal.get("target_layer")
    if target_layer not in rank:
        reasons.append(f"어휘 위반: target_layer {target_layer!r} (허용: {order})")

    for field in _REQUIRED_TEXT_FIELDS:
        value = proposal.get(field)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"필수 필드 없음/빈 값: {field}")

    target_rel = norm_rel(proposal.get("path") or "")
    if not target_rel or not target_rel.endswith(".md"):
        reasons.append(f"경로 형식 오류: {proposal.get('path')!r} (번들 상대 .md 경로)")
    elif escapes_bundle(bundle, target_rel):
        reasons.append(f"번들 경계 탈출: path {target_rel} — 번들 밖은 validate·index가 못 본다")
    elif os.path.exists(os.path.join(bundle, target_rel)):
        reasons.append(f"신설 아님: {target_rel} 이미 존재 — 재라벨·덮어쓰기 금지(§9 금지 2)")

    derived = [norm_rel(p) for p in proposal.get("derived_from") or []]
    allow_dangling = {norm_rel(p) for p in proposal.get("allow_dangling") or []}
    materials = {
        norm_rel(m.get("path", "")): m.get("sha256")
        for m in proposal.get("materials") or []
        if isinstance(m, dict)
    }

    # 재료 경로도 같은 경계 — 밖의 파일을 근거로 인정하면 사슬이 트리를 벗어나고,
    # 번들 밖은 layer_map에 없어 "미분류"로 우연히 걸리거나(강도 임의) 그냥 통과한다.
    for field, rels in (
        ("derived_from", derived),
        ("materials", list(materials)),
        ("allow_dangling", sorted(allow_dangling)),
    ):
        for rel in rels:
            if rel and escapes_bundle(bundle, rel):
                reasons.append(f"번들 경계 탈출: {field} {rel}")

    if (
        rules.get("upper_requires_derived_from")
        and target_layer in rank
        and rank[target_layer] >= 1
        and not derived
    ):
        reasons.append(f"미접지 제안: {target_layer}는 비어있지 않은 derived_from 필요(§4)")

    for dep in derived:
        fs = os.path.join(bundle, dep)
        if not os.path.isfile(fs):
            if dep not in allow_dangling:
                reasons.append(
                    f"근거 부재: {dep} — 번들에 없음. 미작성 신호로 남기려면 "
                    f"allow_dangling에 명시(§9 금지 1: 날조 금지)"
                )
            continue
        dep_layer = layer_map.get(dep)
        if rules.get("derivation_strictly_downward"):
            if dep_layer is None:
                reasons.append(f"재료 미분류: {dep} — layer 없는 개념은 정초 재료 불가(엄격 하향)")
            elif dep_layer not in rank:
                # rank 밖 층은 **반려**다. 예전엔 `rank[dep_layer]`가 KeyError로 죽어
                # 배치 중간에서 크래시했는데, 그 exit 1은 "반려 있음"과 같은 코드라
                # 소비처가 구분할 수 없었다. 어휘 밖 값은 정초 관계를 판정할 수 없으므로
                # `is None`(미분류)과 같은 부류로 흡수한다 — 판정 불가는 통과가 아니다.
                reasons.append(
                    f"재료 층 어휘 밖: {dep}({dep_layer}) — 허용: {order} "
                    f"(오타이거나 렌더 파싱 오염일 수 있다)"
                )
            elif target_layer in rank and rank[dep_layer] >= rank[target_layer]:
                reasons.append(f"정초 역전: {dep}({dep_layer}) ≥ {target_layer}(§9 금지 5)")
        if dep in batch_promoted:
            continue  # 같은 배치에서 방금 집행된 재료 — 스냅샷 면제(§9 원칙 3 캐스케이드)
        if dep not in materials:
            reasons.append(
                f"재료 스냅샷 누락: {dep} — snapshot 해시를 materials에 포함(금지 3 게이트)"
            )
        elif sha256_file(fs) != materials[dep]:
            reasons.append(f"재료 수정됨: {dep} — snapshot 이후 내용 변경(§9 금지 3)")

    if (
        rules.get("information_requires_source")
        and target_layer == order[0]
        and not (proposal.get("resource") or "").strip()
    ):
        reasons.append(f"정보층 출처 필요: {order[0]} 신설은 resource 필수(§4 접지)")

    return reasons


def render_concept(spec: dict, proposal: dict) -> str:
    """제안 계약에서 개념 파일을 결정적으로 실체화(결정 C — frontmatter는 스크립트,
    body는 모델 저작 원문). derived_from은 LAYERS 권장 절대(번들 상대 ``/``) 표기."""
    lines = ["---"]
    lines.append(f"type: {proposal['type']}")
    lines.append(f"description: {proposal['description']}")
    lines.append(f"{spec['field']}: {proposal['target_layer']}")
    derived = [norm_rel(p) for p in proposal.get("derived_from") or []]
    if derived:
        lines.append(f"{spec['derivation_field']}:")
        lines.extend(f"  - /{dep}" for dep in derived)
    resource = (proposal.get("resource") or "").strip()
    if resource:
        lines.append(f"resource: {resource}")
    lines.append("---")
    body = proposal["body"].strip("\n")
    return "\n".join(lines) + "\n\n" + body + "\n"


def _staged(run, stage: str, argv: list[str]) -> str:
    """엔진 호출에 **어느 단계에서 죽었는지**를 붙인다 — 사용자가 다음 행동을 고를 근거."""
    try:
        return run(argv)
    except Exception as exc:
        exc.okf_stage = stage  # type: ignore[attr-defined]
        raise


def apply_proposals(bundle: str, proposals: list[dict], run=_okf_run) -> dict:
    """게이트 통과분만 집행한다 — 쓰기 → validate --strict(실패 시 롤백·반려) →
    근거 사슬 감사 → log --kind Promotion → (통과분 있으면) index --write →
    접지 린트(자문 warn 동봉). ``run``은 엔진 러너(테스트 주입점).

    **어떤 경우에도 계약 JSON을 반환한다.** 예전에는 ``validate``만 감싸여 있어
    ``context``·``graph``·``log``·``index`` 실패나 예기치 못한 예외가 traceback으로
    빠져나갔다 — 그때 stdout은 0바이트인데, 커맨드 문서는 ``promoted``/``rejected``/
    ``lint_warns``를 파싱하라고 지시하므로 소비처 계약이 파기된다. 게다가 배치 앞부분은
    이미 파일로 쓰이고 log에도 남은 상태라, 사용자에게 그 사실을 알릴 자리가 사라진다.
    실패는 ``error: {code, stage, detail}``로 싣고 그때까지의 ``promoted``를 보존한다.
    """
    promoted: list[dict] = []
    try:
        return _apply_proposals(bundle, proposals, run, promoted)
    except Exception as exc:  # noqa: BLE001 — 계약 JSON은 어떤 실패에도 나가야 한다
        return {
            "promoted": promoted,
            "rejected": [],
            "lint_warns": [],
            "error": {
                "code": "engine_failed" if isinstance(exc, RuntimeError) else "internal_error",
                "stage": getattr(exc, "okf_stage", "unknown"),
                "detail": str(exc),
            },
        }


def _apply_proposals(bundle: str, proposals: list[dict], run, promoted: list[dict]) -> dict:
    """``apply_proposals``의 본체 — 예외 경계 밖에서 실제 집행을 수행한다.

    ``promoted``는 호출자가 넘긴 **같은 리스트**다. 중간에 죽어도 그때까지 집행된
    것이 래퍼에 그대로 보여야 "무엇이 이미 쓰였는가"를 말할 수 있다.
    """
    spec = okf_layers.load_layers_spec()
    layer_map = _staged(
        run, "context", ["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)]
    )
    layer_map = okf_layers.parse_layer_map(layer_map)
    rejected: list[dict] = []
    batch_promoted: set[str] = set()

    for proposal in proposals:
        target_rel = norm_rel(proposal.get("path") or "")
        reasons = gate_proposal(spec, layer_map, bundle, proposal, batch_promoted)
        if reasons:
            rejected.append({"path": target_rel, "reasons": reasons})
            continue

        fs = os.path.join(bundle, target_rel)
        parent = os.path.dirname(fs)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(fs, "w", encoding="utf-8") as f:
            f.write(render_concept(spec, proposal))
        try:
            run(["validate", bundle, "--strict"])
        except RuntimeError as exc:
            os.remove(fs)  # 롤백 — 반려된 제안이 번들을 오염시키지 않는다
            rejected.append({"path": target_rel, "reasons": [f"validate --strict 실패: {exc}"]})
            continue

        chain = [
            line
            for line in _staged(
                run,
                "graph",
                ["graph", bundle, "--edges-from", spec["derivation_field"], "--chain", target_rel],
            ).splitlines()
            if line.strip()
        ]
        concept_dir = os.path.join(bundle, os.path.dirname(target_rel))
        material_count = len(proposal.get("derived_from") or [])
        promoted_layer = proposal["target_layer"]
        summary = f"{proposal['description']} (layer {promoted_layer} ← 하위 {material_count}건)"
        _staged(run, "log", ["log", "append", concept_dir, "-m", summary, "--kind", "Promotion"])
        layer_map[target_rel] = proposal["target_layer"]  # 같은 배치의 후속 제안이 접지 가능
        batch_promoted.add(target_rel)
        promoted.append({"path": target_rel, "layer": proposal["target_layer"], "chain": chain})

    lint_warns: list[str] = []
    if promoted:
        _staged(run, "index", ["index", bundle, "--write"])
        fresh_map = okf_layers.parse_layer_map(
            _staged(
                run,
                "context",
                ["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)],
            )
        )
        fresh_graph = json.loads(
            _staged(
                run, "graph", ["graph", bundle, "--edges-from", spec["derivation_field"], "--json"]
            )
        )
        lint_warns = [
            f"{path}  {message}" for path, message in okf_layers.check(spec, fresh_map, fresh_graph)
        ]
    return {"promoted": promoted, "rejected": rejected, "lint_warns": lint_warns}


def _load_proposals(source: str) -> list[dict]:
    raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("proposals", data)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("제안은 객체 또는 리스트여야 한다")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="okf_promote", description="층간 승격 파이프라인(게이트+집행)"
    )
    sub = ap.add_subparsers(dest="op", required=True)
    snap = sub.add_parser("snapshot", help="재료 내용 해시 채집(제안 계약 materials)")
    snap.add_argument("bundle", help="번들 디렉터리 경로")
    snap.add_argument("--paths", nargs="+", required=True, help="재료 경로(번들 상대)")
    app = sub.add_parser("apply", help="제안 게이트+집행 — 결과 JSON, 반려 있으면 exit 1")
    app.add_argument("bundle", help="번들 디렉터리 경로")
    app.add_argument("--proposals", required=True, help="제안 JSON 파일 경로 또는 -(stdin)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.bundle):
        print(f"오류: 번들 디렉터리가 아님: {args.bundle}", file=sys.stderr)
        return 2

    if args.op == "snapshot":
        result = snapshot_materials(args.bundle, args.paths)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["missing"] else 0

    try:
        proposals = _load_proposals(args.proposals)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: 제안 로드 실패: {exc}", file=sys.stderr)
        return 2
    report = apply_proposals(args.bundle, proposals)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 종료코드 3분기: 0 전량 승격 / 1 반려 있음 / 3 크래시. 예전엔 크래시가 반려와 같은
    # 1이라, 커맨드 문서가 지시한 promoted·rejected 파싱이 무출력에 대해 실패했다.
    if report.get("error"):
        return 3
    return 1 if report["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
