#!/usr/bin/env python3
"""층간 승격 파이프라인 (Epic #197 U3) — snapshot / apply, 결정적 게이트+집행.

모델의 제안 JSON(판정 산물)을 받아 LAYERS §9 금지를 **기계적으로** 게이트하고,
통과분만 집행한다(frontmatter 실체화 → 파일 쓰기 → validate --strict → 접지 린트 →
근거 사슬 감사 → log --kind Promotion → index --write). 판정하지 않는다 — 층 분류·
"새 인식" 평가는 사람+모델의 몫이고, 여기는 판정 산물의 원칙 준수만 검사한다.

탐색 출력은 입력이 아니다(EXPLORE.md 불변식 1) — 재료 무수정 검증의 해시는
``snapshot``이 직접 채집한다. 기존 개념과의 존재 대조도 게이트가 아니다(#189 결정 B,
자문) — 커맨드가 제안 단계에서 **주입된 개념 목록**으로 본다(#391: 지표 자문 없음).
study 모듈 무-import(core⊥study).

제안 계약(Epic #197 §3 · #351 U1 일반화): ``{mode?: "create"|"update"(기본 create),
layer?(별칭 target_layer — 하위호환), path(번들 상대), type, description, body,
derived_from?: [경로], materials: [{path, sha256}], resource?, allow_dangling?:
[경로], rubric?, log_note?}``. 경로는 ``/`` 시작을 허용하며 내부적으로 정규화한다.
``log_note``는 log.md 요약에 덧붙는 자유 문구다(검증 없음) — 소비처 provenance
(예: 캡처 일자)가 apply 위임 후에도 git-추적 이력에 남는 자리(#114 U5 · #351 U2).

모드 규칙(#351 — 명시적 mode, 실존 암묵 판별 금지): create인데 path 실존이면 반려
(§9 금지 2), update인데 부재면 반려(오타가 신설로 둔갑하지 않게). update는 기존
frontmatter를 엔진 query(재료 제공자)로 로드해 제안 필드만 병합하고(미지 축 보존),
갱신 이력은 스킬 §3대로 log.md 엔트리(kind Update)로 남긴다 — supersedes는 필드가
아니라 이 갱신 플로우의 이름이다. update에서 기존 층과 다른 layer는 재라벨이라
반려하고(미분류 개념에 라벨 부여는 허용), rubric·정보층 출처 요구는 create 전용이다
(§3 유지 플로우엔 해당 없음). layer 미기재는 허용 — 층 게이트는 층이 있을 때만
적용된다(판정 요구 필드를 강제하면 채워지지 않는다는 소급 실측, #351 설계).

게이트 → §9 금지 매핑: 어휘·정초 엄격 하향(금지 5) · path 신설(금지 2, 재라벨 차단) ·
재료 해시 재대조(금지 3, 삭제·흡수·수정 차단) · derived_from 실존(금지 1, 근거 날조
차단 — 의도적 dangle은 allow_dangling 명시) · 정보층 출처 필수(§4 접지) · 상위층
비어있지 않은 derived_from(§4 접지).

경로의 **번들 경계**는 §9 금지가 아니라 위 제안 계약("번들 상대")의 전제다(#267) —
`..`가 든 경로는 번들 밖에 쓰이는데 validate·index는 번들 안만 보므로 탐지도 롤백도
되지 않는다. 침묵 실패라 집행 전에 반려한다. 판정은 엔진과 같은 형태를 쓴다.

CLI: ``okf_promote.py snapshot <bundle> --paths <p>…`` ·
``okf_promote.py apply <bundle> --proposals <file|->``. apply는 결과 JSON을 내고
반려가 있으면 exit 1. 반려 사유는 ``{code, detail}``이다(#360) — 소비처는 ``code``로
분기하고(한국어 detail 매칭 금지) 코드 어휘는 ``REJECT_CODES``가 단일원천이다.
엔진 실행은 bin/okf 셔틀 경유(stdlib 전용), 어휘·정초 순서는
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


# 제안 자기검증 필드(#307) — `okf-promote.md`가 정의한 계약. 상위 층 제안에 요구하고
# 개념 파일에 영속시켜, 선별 표의 '새 인식·반증'이 그 자리의 즉흥이 아니라 **남는 기록**이
# 되게 한다.
RUBRIC_KEYS = ("new_insight", "falsification")

_MODES = ("create", "update")

# 반려 코드(#360) — 소비처가 detail(한국어)이 아니라 code로 분기하는 기계 축.
# okf_layers.WARN_CODES(#306)·okf_remote.REFRESH_REASONS와 같은 계약 형태 — 각 코드에
# 실행 가능한 복구 지시가 붙는다. detail은 사람용 표시다(값·경로가 든 진단 문장).
REJECT_CODES: dict[str, str] = {
    "bad_mode": "mode가 어휘 밖이다 — create|update로 명시하라",
    "layer_conflict": "layer와 target_layer(별칭)가 다르다 — 하나만 쓰거나 같은 값으로",
    "bad_layer": "layer가 층 어휘 밖이다 — LAYERS 어휘로 고치거나 미기재로",
    "bad_path": "path 형식 오류 — 번들 상대 .md 경로로",
    "bundle_escape": "경로가 번들 경계를 벗어난다 — 번들 안 경로로",
    "create_exists": "create 대상이 이미 존재한다 — 갱신 의도면 mode: update로",
    "update_missing": "update 대상이 없다 — 오타이거나, 신설 의도면 mode: create로",
    "relabel": "기존 층과 다른 layer — 재라벨 금지, 층간 이동은 적립·신설로",
    "rubric_missing": "상위 층 create에 자기검증이 비었다 — new_insight·falsification을 채워라",
    "missing_field": "필수 텍스트 필드가 비었다 — type·description·body를 채워라",
    "ungrounded_proposal": "상위 층 제안에 derived_from이 없다 — 근거 하위 개념을 이어라",
    "missing_material": "derived_from 대상이 번들에 없다 — 먼저 쓰거나 allow_dangling에 명시하라",
    "unlayered_material": "재료에 layer가 없다 — 층 없는 개념은 정초 재료 불가",
    "bad_material_layer": "재료의 layer가 어휘 밖이다 — 재료 frontmatter를 고쳐라",
    "derivation_inversion": "정초 역전 — 파생 대상을 더 낮은 층으로",
    "snapshot_missing": "재료 스냅샷이 없다 — snapshot 해시를 materials에 담아라",
    "material_changed": "재료가 스냅샷 이후 수정됐다 — snapshot을 다시 떠라",
    "missing_resource": "정보층 create에 출처가 없다 — resource를 채워라",
    "update_unrenderable": "기존 개념을 기계 실체화할 수 없다 — 스킬 §3 수동 편집으로",
    "validate_failed": "validate --strict 실패 — 번들은 롤백/복원됐다, 제안을 고쳐라",
}


def _reason(code: str, detail: str) -> dict:
    """반려 사유 1건 — code는 REJECT_CODES 어휘여야 한다(미등록 코드는 즉시 죽는다)."""
    if code not in REJECT_CODES:
        raise ValueError(f"미등록 반려 코드: {code}")
    return {"code": code, "detail": detail}


def proposal_mode(proposal: dict) -> str | None:
    """명시적 모드(기본 create) — 어휘 밖 값은 None(반려 사유는 게이트가 만든다)."""
    mode = proposal.get("mode") or "create"
    return mode if mode in _MODES else None


def proposal_layer(proposal: dict) -> tuple[str | None, bool]:
    """``layer``(정식)·``target_layer``(하위호환 별칭)를 하나로 — (값, 충돌 여부)."""
    layer = proposal.get("layer")
    legacy = proposal.get("target_layer")
    if layer is not None and legacy is not None and layer != legacy:
        return None, True
    return (layer if layer is not None else legacy), False


def gate_proposal(
    spec: dict,
    layer_map: dict,
    bundle: str,
    proposal: dict,
    batch_promoted: set[str] | None = None,
) -> list[dict]:
    """§9 금지의 기계 게이트(순수 판정 — 번들은 읽기만 한다). ``{code, detail}`` 사유
    목록을 반환하고 빈 리스트면 통과(코드 어휘는 ``REJECT_CODES`` 단일원천, #360).
    판정(층 분류·새 인식)은 하지 않는다 — 원칙 준수 검사만.

    ``batch_promoted``는 같은 배치에서 방금 집행된 개념 집합 — §9 원칙 3("같은
    변경에서 먼저 승격")의 캐스케이드를 위해 그 재료에는 스냅샷 해시를 면제한다
    (파이프라인이 직접 쓴 파일이라 무결성이 자체 보장된다)."""
    reasons: list[dict] = []
    batch_promoted = batch_promoted or set()
    order = spec["order"]
    rank = {value: index for index, value in enumerate(order)}
    rules = spec.get("rules", {})

    mode = proposal_mode(proposal)
    if mode is None:
        reasons.append(
            _reason("bad_mode", f"모드 위반: mode {proposal.get('mode')!r} (허용: {_MODES})")
        )
        mode = "create"  # 나머지 게이트는 보수적으로 create 기준으로 계속 본다

    layer, conflict = proposal_layer(proposal)
    if conflict:
        reasons.append(
            _reason("layer_conflict", "layer/target_layer 불일치 — 별칭은 같은 값이어야 한다")
        )
    if layer is not None and layer not in rank:
        reasons.append(_reason("bad_layer", f"어휘 위반: layer {layer!r} (허용: {order})"))
        layer = None  # 판정 불가 층으로는 층 게이트를 잇지 않는다(반려는 이미 기록)

    target_rel = norm_rel(proposal.get("path") or "")
    existing_layer: str | None = None
    if not target_rel or not target_rel.endswith(".md"):
        reasons.append(
            _reason("bad_path", f"경로 형식 오류: {proposal.get('path')!r} (번들 상대 .md 경로)")
        )
    elif escapes_bundle(bundle, target_rel):
        reasons.append(
            _reason(
                "bundle_escape",
                f"번들 경계 탈출: path {target_rel} — 번들 밖은 validate·index가 못 본다",
            )
        )
    elif mode == "create" and os.path.exists(os.path.join(bundle, target_rel)):
        reasons.append(
            _reason(
                "create_exists",
                f"신설 아님: {target_rel} 이미 존재 — 재라벨·덮어쓰기 금지(§9 금지 2). "
                "갱신 의도면 mode: update로 명시하라(#351)",
            )
        )
    elif mode == "update":
        if not os.path.isfile(os.path.join(bundle, target_rel)):
            reasons.append(
                _reason(
                    "update_missing",
                    f"갱신 대상 없음: {target_rel} — 오타이거나, 신설 의도면 mode: create(#351)",
                )
            )
        else:
            existing_layer = layer_map.get(target_rel)
            if layer is not None and existing_layer is not None and layer != existing_layer:
                reasons.append(
                    _reason(
                        "relabel",
                        f"재라벨 금지: {target_rel} {existing_layer} → {layer} "
                        "(§9 금지 2 — 층간 이동은 적립·신설로)",
                    )
                )
    effective_layer = layer if layer is not None else existing_layer

    # rubric(자기검증) — 상위 층 제안에만 요구한다(#307). 문서가 계약으로 정의해 놓고
    # 코드 어디에서도 읽지도 저장하지도 않아, 선별 표의 '새 인식·반증' 열이 **그 자리에서
    # 지어낸 문장**이 되고 그것이 승인 근거로 쓰였다. 요구하는 것은 **필드의 존재**이지
    # 내용의 진위가 아니다 — 진위는 사람의 몫이고, 여기서 하는 것은 "빈칸으로 통과하지
    # 못하게" 하는 것뿐이다(출처·근거 날조 금지 원칙과 충돌하지 않는다).
    if mode == "create" and effective_layer in rank and rank[effective_layer] >= 1:
        rubric = proposal.get("rubric")
        if not isinstance(rubric, dict):
            reasons.append(
                _reason(
                    "rubric_missing",
                    f"rubric 없음: {effective_layer} 제안은 자기검증이 필요하다 ({RUBRIC_KEYS})",
                )
            )
        else:
            missing = [k for k in RUBRIC_KEYS if not str(rubric.get(k) or "").strip()]
            if missing:
                reasons.append(
                    _reason(
                        "rubric_missing",
                        f"rubric 미기재: {missing} — 빈칸으로는 승인 근거가 되지 않는다",
                    )
                )

    for field in _REQUIRED_TEXT_FIELDS:
        value = proposal.get(field)
        if not isinstance(value, str) or not value.strip():
            reasons.append(_reason("missing_field", f"필수 필드 없음/빈 값: {field}"))

    derived_given = proposal.get("derived_from") is not None
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
                reasons.append(_reason("bundle_escape", f"번들 경계 탈출: {field} {rel}"))

    if (
        rules.get("upper_requires_derived_from")
        and effective_layer in rank
        and rank[effective_layer] >= 1
        and not derived
        and (mode == "create" or derived_given)  # update 미제공은 기존 접지 유지
    ):
        reasons.append(
            _reason(
                "ungrounded_proposal",
                f"미접지 제안: {effective_layer}는 비어있지 않은 derived_from 필요(§4)",
            )
        )

    for dep in derived:
        fs = os.path.join(bundle, dep)
        if not os.path.isfile(fs):
            if dep not in allow_dangling:
                reasons.append(
                    _reason(
                        "missing_material",
                        f"근거 부재: {dep} — 번들에 없음. 미작성 신호로 남기려면 "
                        "allow_dangling에 명시(§9 금지 1: 날조 금지)",
                    )
                )
            continue
        dep_layer = layer_map.get(dep)
        if rules.get("derivation_strictly_downward"):
            if dep_layer is None:
                reasons.append(
                    _reason(
                        "unlayered_material",
                        f"재료 미분류: {dep} — layer 없는 개념은 정초 재료 불가(엄격 하향)",
                    )
                )
            elif dep_layer not in rank:
                # rank 밖 층은 **반려**다. 예전엔 `rank[dep_layer]`가 KeyError로 죽어
                # 배치 중간에서 크래시했는데, 그 exit 1은 "반려 있음"과 같은 코드라
                # 소비처가 구분할 수 없었다. 어휘 밖 값은 정초 관계를 판정할 수 없으므로
                # `is None`(미분류)과 같은 부류로 흡수한다 — 판정 불가는 통과가 아니다.
                reasons.append(
                    _reason(
                        "bad_material_layer",
                        f"재료 층 어휘 밖: {dep}({dep_layer}) — 허용: {order} "
                        "(오타이거나 렌더 파싱 오염일 수 있다)",
                    )
                )
            elif effective_layer in rank and rank[dep_layer] >= rank[effective_layer]:
                reasons.append(
                    _reason(
                        "derivation_inversion",
                        f"정초 역전: {dep}({dep_layer}) ≥ {effective_layer}(§9 금지 5)",
                    )
                )
        if dep in batch_promoted:
            continue  # 같은 배치에서 방금 집행된 재료 — 스냅샷 면제(§9 원칙 3 캐스케이드)
        if dep not in materials:
            reasons.append(
                _reason(
                    "snapshot_missing",
                    f"재료 스냅샷 누락: {dep} — snapshot 해시를 materials에 포함(금지 3 게이트)",
                )
            )
        elif sha256_file(fs) != materials[dep]:
            reasons.append(
                _reason(
                    "material_changed", f"재료 수정됨: {dep} — snapshot 이후 내용 변경(§9 금지 3)"
                )
            )

    if (
        mode == "create"
        and rules.get("information_requires_source")
        and effective_layer == order[0]
        and not (proposal.get("resource") or "").strip()
    ):
        reasons.append(
            _reason(
                "missing_resource", f"정보층 출처 필요: {order[0]} 신설은 resource 필수(§4 접지)"
            )
        )

    return reasons


def render_concept(spec: dict, proposal: dict) -> str:
    """제안 계약에서 개념 파일을 결정적으로 실체화(결정 C — frontmatter는 스크립트,
    body는 모델 저작 원문). derived_from은 LAYERS 권장 절대(번들 상대 ``/``) 표기."""
    lines = ["---"]
    lines.append(f"type: {proposal['type']}")
    lines.append(f"description: {proposal['description']}")
    layer, _conflict = proposal_layer(proposal)
    if layer is not None:  # 미기재 허용(#351) — 층 없는 개념은 층 라인을 쓰지 않는다
        lines.append(f"{spec['field']}: {layer}")
    derived = [norm_rel(p) for p in proposal.get("derived_from") or []]
    if derived:
        lines.append(f"{spec['derivation_field']}:")
        lines.extend(f"  - /{dep}" for dep in derived)
    resource = (proposal.get("resource") or "").strip()
    if resource:
        lines.append(f"resource: {resource}")
    lines.append("---")
    body = proposal["body"].strip("\n")
    rendered = "\n".join(lines) + "\n\n" + body + "\n"
    return rendered + _render_rubric(proposal)


def _render_rubric(proposal: dict) -> str:
    """rubric을 본문 **고정 섹션**으로 영속한다 — 없으면 빈 문자열(하위층 등).

    frontmatter가 아니라 본문에 두는 이유: rubric은 판정 축이 아니라 **읽히는 근거**다.
    엔진의 frontmatter 계약(taxonomy-neutral)을 늘리지 않으면서, 개념을 여는 사람이
    "무엇이 새 인식이고 무엇이면 반증되는가"를 함께 보게 한다.
    """
    rubric = proposal.get("rubric")
    if not isinstance(rubric, dict):
        return ""
    rows = [(key, str(rubric.get(key) or "").strip()) for key in RUBRIC_KEYS]
    if not any(value for _key, value in rows):
        return ""
    labels = {"new_insight": "새 인식", "falsification": "반증 조건"}
    out = ["", "## 자기검증", ""]
    out += [f"- **{labels[key]}**: {value}" for key, value in rows if value]
    return "\n".join(out) + "\n"


def _existing_frontmatter(bundle: str, rel: str, run) -> dict:
    """update 대상의 frontmatter를 엔진 query(재료 제공자)로 로드한다.

    플러그인은 frontmatter를 직접 파싱하지 않는다(파스는 엔진 소관 — stdlib 원칙과
    파서 단일화 둘 다). 개념이 아니면(파스 불가·비개념) ValueError — update 불가.
    질의 우주는 valid 뷰(§9 통과 집합) — concept 테이블이면 규격 미달도 frontmatter가
    차서 통과하는데, 그 문서는 layer_map 밖이라 재라벨 게이트가 조용히 꺼진다(#358).
    """
    escaped = rel.replace("'", "''")
    sql = f"SELECT frontmatter_json FROM valid WHERE path = '{escaped}'"
    rows = json.loads(_staged(run, "query", ["query", bundle, sql, "--json"]))
    if not rows or not rows[0].get("frontmatter_json"):
        raise ValueError(f"개념 아님: {rel} — §9 밖 문서(파스 불가·규격 미달)는 update 불가")
    return json.loads(rows[0]["frontmatter_json"])


def _frontmatter_lines(spec: dict, fields: dict) -> list[str]:
    """병합 frontmatter를 결정적 순서(정본 필드 → 나머지 정렬)로 실체화한다.

    지원 형태는 스칼라(str·bool·수)와 문자열 리스트뿐 — 그 밖(중첩 등)은 ValueError로
    반려한다. 조용한 손상 대신 가시적 반려이고, 그런 개념은 스킬 §3 수동 편집 경로다.
    """
    canonical = ["type", "description", spec["field"], spec["derivation_field"], "resource"]
    ordered = [k for k in canonical if k in fields]
    ordered += sorted(k for k in fields if k not in canonical)
    lines: list[str] = []
    for key in ordered:
        value = fields[key]
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (str, int, float)):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            lines.append(f"{key}:")
            lines.extend(f"  - {v}" for v in value)
        elif isinstance(value, list) and not value:
            continue  # 빈 리스트는 쓰지 않는다(create 렌더의 빈 derived 생략과 동형)
        else:
            raise ValueError(
                f"frontmatter 실체화 불가: {key}({type(value).__name__}) — 스킬 §3 수동 편집으로"
            )
    return lines


def render_updated_concept(spec: dict, proposal: dict, existing: dict) -> str:
    """update 실체화(#351) — 기존 frontmatter에 제안 필드만 병합하고 미지 축은 보존한다.

    병합 규칙: type·description은 제안 값(필수), 층은 기존 우선(제안 기재는 게이트가
    동일성·신규 부여를 이미 판정), derived_from·resource는 **제안이 준 경우만** 대체
    (미제공 = 유지, 빈 리스트 = 명시적 소거 — 상위층 소거는 미접지 게이트가 반려).
    body는 제안이 전체를 새로 저작한다(§3 갱신 = 편집·교체).
    """
    merged = dict(existing)
    merged["type"] = proposal["type"]
    merged["description"] = proposal["description"]
    layer, _conflict = proposal_layer(proposal)
    if layer is not None:
        merged[spec["field"]] = layer
    if proposal.get("derived_from") is not None:
        merged[spec["derivation_field"]] = [norm_rel(p) for p in proposal["derived_from"]]
    if (proposal.get("resource") or "").strip():
        merged["resource"] = str(proposal["resource"]).strip()
    dv = merged.get(spec["derivation_field"])
    if isinstance(dv, str):
        dv = [dv]  # 단일 문자열 표기도 리스트로 승격(엔진 파서 관용과 동형)
    if isinstance(dv, list) and all(isinstance(v, str) for v in dv):
        merged[spec["derivation_field"]] = ["/" + norm_rel(v) for v in dv if norm_rel(v)]
    lines = ["---", *_frontmatter_lines(spec, merged), "---"]
    body = proposal["body"].strip("\n")
    return "\n".join(lines) + "\n\n" + body + "\n" + _render_rubric(proposal)


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
    실패는 ``error: {code, stage, detail}``로 싣고 그때까지의 ``promoted``·``rejected``를
    **둘 다** 보존한다 — 게이트 반려는 사용자가 제안을 고칠 근거이고, 크래시했다고
    사라지면 "3개 중 2개가 왜 반려됐는지"를 다시 알아낼 방법이 없다.
    """
    promoted: list[dict] = []
    rejected: list[dict] = []
    try:
        return _apply_proposals(bundle, proposals, run, promoted, rejected)
    except Exception as exc:  # noqa: BLE001 — 계약 JSON은 어떤 실패에도 나가야 한다
        return {
            "promoted": promoted,
            "rejected": rejected,
            "lint_warns": [],
            "error": {
                "code": "engine_failed" if isinstance(exc, RuntimeError) else "internal_error",
                "stage": getattr(exc, "okf_stage", "unknown"),
                "detail": str(exc),
            },
        }


def _apply_proposals(
    bundle: str, proposals: list[dict], run, promoted: list[dict], rejected: list[dict]
) -> dict:
    """``apply_proposals``의 본체 — 예외 경계 밖에서 실제 집행을 수행한다.

    ``promoted``·``rejected``는 호출자가 넘긴 **같은 리스트**다. 중간에 죽어도 그때까지
    집행된 것과 반려된 것이 래퍼에 그대로 보여야 "무엇이 이미 쓰였고 무엇을 고쳐야
    하는가"를 말할 수 있다.
    """
    spec = okf_layers.load_layers_spec()
    layer_map = _staged(
        run, "context", ["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)]
    )
    layer_map = okf_layers.parse_layer_map(layer_map)
    batch_promoted: set[str] = set()

    for proposal in proposals:
        target_rel = norm_rel(proposal.get("path") or "")
        reasons = gate_proposal(spec, layer_map, bundle, proposal, batch_promoted)
        if reasons:
            rejected.append({"path": target_rel, "reasons": reasons})
            continue

        mode = proposal_mode(proposal) or "create"
        fs = os.path.join(bundle, target_rel)
        original: str | None = None
        if mode == "update":
            with open(fs, encoding="utf-8") as f:
                original = f.read()
            try:
                existing_fm = _existing_frontmatter(bundle, target_rel, run)
                rendered = render_updated_concept(spec, proposal, existing_fm)
            except ValueError as exc:
                rejected.append(
                    {"path": target_rel, "reasons": [_reason("update_unrenderable", str(exc))]}
                )
                continue
        else:
            rendered = render_concept(spec, proposal)
        parent = os.path.dirname(fs)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(fs, "w", encoding="utf-8") as f:
            f.write(rendered)
        try:
            run(["validate", bundle, "--strict"])
        except RuntimeError as exc:
            if original is None:
                os.remove(fs)  # 롤백 — 반려된 제안이 번들을 오염시키지 않는다
            else:
                with open(fs, "w", encoding="utf-8") as f:
                    f.write(original)  # 갱신 반려가 기존 개념을 지우면 안 된다
            rejected.append(
                {
                    "path": target_rel,
                    "reasons": [_reason("validate_failed", f"validate --strict 실패: {exc}")],
                }
            )
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
        layer, _conflict = proposal_layer(proposal)
        effective_layer = layer if layer is not None else layer_map.get(target_rel)
        if mode == "update":
            note = "갱신"
            kind = "Update"  # 스킬 §3 유지 플로우 — supersedes 이력은 log.md 엔트리다
        else:
            material_count = len(proposal.get("derived_from") or [])
            note = f"하위 {material_count}건"
            if effective_layer:
                note = f"layer {effective_layer} ← {note}"
            kind = "Promotion"
        log_note = str(proposal.get("log_note") or "").strip()
        if log_note:
            note = f"{note}, {log_note}"
        summary = f"{proposal['description']} ({note})"
        _staged(run, "log", ["log", "append", concept_dir, "-m", summary, "--kind", kind])
        if effective_layer:
            layer_map[target_rel] = effective_layer  # 같은 배치의 후속 제안이 접지 가능
        batch_promoted.add(target_rel)
        promoted.append(
            {"path": target_rel, "layer": effective_layer, "chain": chain, "mode": mode}
        )

    lint_warns: list[dict] = []
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
        # {path, code, message} — 소비처는 code로 분기한다(한국어 message는 사람용 표시)
        lint_warns = okf_layers.check_findings(spec, fresh_map, fresh_graph)
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
