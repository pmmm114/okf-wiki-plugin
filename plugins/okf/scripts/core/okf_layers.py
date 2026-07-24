#!/usr/bin/env python3
"""인식층 정초·출처 접지 린트 (Epic #173 U5) — layer-aware, 플러그인측.

엔진(okf-core)은 taxonomy-neutral이라 layer 어휘·정초 순서를 모른다. 그 판정은
여기(플러그인)가 안다 — 어휘·순서는 하드코딩하지 않고 LAYERS.md의 기계 판독 단일
원천(json 블록)에서 로드한다. 번들 데이터는 엔진 출력에서 소비한다(stdlib 전용이라
frontmatter를 직접 파싱하지 않는다): ``okf graph --edges-from <derived> --json``
(파생 엣지·resource) + ``okf context --group-by <field>``(개념별 층).

검사(전부 warn — 엔진 §9 판정 불변, 스펙 §4.1 관용):
- 정초 순서: derived_from 대상은 출처 개념보다 **엄격히 낮은 층**이어야 한다.
- 접지(상위): 지식·지혜 개념은 근거(derived_from)를 가져야 한다.
- 접지(정보): 정보 개념은 출처(resource)를 가져야 한다.

접지 후보 질의(Epic #189 U2) — 승격 개념이 ``derived_from``으로 접지할 **하위층 기존
개념**을 층별로 제시한다(정초 엄격 하향: 지식→정보, 지혜→지식·정보). 승격의 판정
단계가 소비해, 같은 정보를 다시 만들지 않고 기존 개념에 맵핑하도록 돕는다.

CLI: ``okf_layers.py <bundle> [--strict]``(접지 린트) · ``--candidates-for <layer>
[--json]``(접지 후보). 기본은 자문(발견해도 exit 0), --strict면 발견 시 exit 1.
엔진 실행은 bin/okf 셔틀 경유(stdlib 전용).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_OKF = os.path.join(_HERE, "..", "..", "bin", "okf")
_LAYERS_MD = os.path.join(_HERE, "..", "..", "skills", "okf", "reference", "LAYERS.md")

_JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_CTX_OPEN = "<okf-context>"
_CTX_CLOSE = "</okf-context>"
_UNCLASSIFIED = "(unclassified)"


def load_layers_spec(path: str = _LAYERS_MD) -> dict:
    """LAYERS.md의 기계 판독 json 블록(어휘·정초 순서 단일 원천)을 로드한다."""
    with open(path, encoding="utf-8") as f:
        blocks = _JSON_BLOCK.findall(f.read())
    if not blocks:
        raise ValueError(f"LAYERS 단일 원천 json 블록 없음: {path}")
    return json.loads(blocks[-1])


def parse_layer_map(context_output: str) -> dict:
    """``okf context --group-by <field>`` 출력에서 {개념경로: 층값}을 만든다.

    미분류 섹션·래퍼는 제외한다(층 미기재 개념은 맵에 없다). 각 개념 줄은 엔진
    형식 ``<경로> [<type>] …``이라 첫 ``' ['`` 앞이 경로다.
    """
    layer_map: dict[str, str] = {}
    current: str | None = None
    for line in context_output.split("\n"):
        if line.startswith("## "):
            head = line[3:].strip()
            current = None if head == _UNCLASSIFIED else head
        elif line and line not in (_CTX_OPEN, _CTX_CLOSE) and current is not None:
            path = line.split(" [", 1)[0].strip()
            if path:
                layer_map[path] = current
    return layer_map


def parse_layer_sections(context_output: str) -> dict[str, list[str]]:
    """``okf context --group-by <field>`` 출력을 {층: [개념 줄]}로 파싱한다.

    ``parse_layer_map``과 같은 섹션 스캐너지만, 경로만 뽑지 않고 **개념 줄 전체**
    (``<경로> [<type>] — <핵심>``)를 층별로 보존한다 — 승격 판정에 후보로 제시하기
    위함이다. 미분류 섹션·래퍼는 제외한다.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in context_output.split("\n"):
        if line.startswith("## "):
            head = line[3:].strip()
            current = None if head == _UNCLASSIFIED else head
        elif line and line not in (_CTX_OPEN, _CTX_CLOSE) and current is not None:
            sections.setdefault(current, []).append(line)
    return sections


def lower_layers(target_layer: str, spec: dict) -> list[str]:
    """``target_layer``보다 **엄격히 낮은** 층 목록(order 순). 정초는 엄격 하향이라
    지식→[정보], 지혜→[정보, 지식], 정보→[](뿌리). 미지의 층은 ValueError."""
    order = spec["order"]
    rank = {value: index for index, value in enumerate(order)}
    if target_layer not in rank:
        raise ValueError(f"미지의 층: {target_layer!r} (허용: {order})")
    target_rank = rank[target_layer]
    return [layer for layer in order if rank[layer] < target_rank]


def select_candidates(sections: dict, target_layer: str, spec: dict) -> dict:
    """이미 파싱된 층 섹션(``parse_layer_sections``)에서 target보다 낮은 층만 골라
    반환한다 — 순수 함수(서브프로세스 없음). 상위·동일 층은 접지 후보에서 제외."""
    return {layer: sections.get(layer, []) for layer in lower_layers(target_layer, spec)}


def check(spec: dict, layer_map: dict, graph: dict) -> list[tuple[str, str]]:
    """(경로, 경고문) 목록을 반환한다 — 순수 함수(서브프로세스 없음)."""
    order = spec["order"]
    rank = {value: index for index, value in enumerate(order)}
    dfield = spec["derivation_field"]
    rules = spec.get("rules", {})
    typed = graph.get("typed_edges", [])
    resource = {n["file"]: n.get("resource") for n in graph.get("nodes", [])}
    derivers = {edge["from"] for edge in typed}
    findings: list[tuple[str, str]] = []

    # 1. 정초 순서 — 파생 대상은 엄격히 낮은 층
    if rules.get("derivation_strictly_downward"):
        for edge in typed:
            src, dst = layer_map.get(edge["from"]), layer_map.get(edge["to"])
            if src in rank and dst in rank and rank[dst] >= rank[src]:
                findings.append(
                    (
                        edge["from"],
                        f"정초 순서 위반: `{edge['to']}`({dst})가 "
                        f"`{edge['from']}`({src})보다 낮은 층이 아님",
                    )
                )

    # 2. 접지(상위) — 지식·지혜는 근거(derived_from) 필요
    if rules.get("upper_requires_derived_from"):
        for path, layer in sorted(layer_map.items()):
            if rank.get(layer, 0) >= 1 and path not in derivers:
                findings.append((path, f"미접지: {layer} 개념에 근거(`{dfield}`) 없음"))

    # 3. 접지(정보) — 정보는 출처(resource) 필요
    if rules.get("information_requires_source"):
        base = order[0]
        for path, layer in sorted(layer_map.items()):
            if layer == base and not resource.get(path):
                findings.append((path, f"미접지: {base} 개념에 출처(`resource`) 없음"))

    return findings


def _okf(args: list[str]) -> str:
    proc = subprocess.run([_OKF, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"okf {args[0]} 실패(rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def gather(bundle: str, spec: dict) -> tuple[dict, dict]:
    """엔진 출력에서 (층 맵, 그래프)를 모은다 — bin/okf 셔틀 경유."""
    ctx = _okf(["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)])
    graph = json.loads(_okf(["graph", bundle, "--edges-from", spec["derivation_field"], "--json"]))
    return parse_layer_map(ctx), graph


def grounding_candidates(bundle: str, target_layer: str, spec: dict | None = None) -> dict:
    """``target_layer`` 개념이 ``derived_from``으로 접지할 **하위층 기존 개념**을 층별로
    반환한다(승격 판정용). ``okf context --group-by <field>``를 재사용(bin/okf 셔틀) —
    지식은 정보를, 지혜는 지식·정보를 후보로 본다. 정초 엄격 하향을 인코딩해 상위·동일
    층은 제외한다. 정보(뿌리)면 빈 dict.
    """
    spec = spec or load_layers_spec()
    ctx = _okf(["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)])
    return select_candidates(parse_layer_sections(ctx), target_layer, spec)


def lint(bundle: str) -> list[tuple[str, str]]:
    spec = load_layers_spec()
    layer_map, graph = gather(bundle, spec)
    return check(spec, layer_map, graph)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf_layers", description="인식층 정초·출처 접지 린트")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--strict", action="store_true", help="발견 시 exit 1(기본은 자문 exit 0)")
    ap.add_argument(
        "--candidates-for",
        metavar="LAYER",
        help="이 층 개념이 접지할 하위층 후보를 출력(승격 접지용, 린트 대신)",
    )
    ap.add_argument("--json", action="store_true", help="후보를 JSON으로(--candidates-for와 함께)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.bundle):
        print(f"오류: 번들 디렉터리가 아님: {args.bundle}", file=sys.stderr)
        return 2

    if args.candidates_for is not None:
        try:
            cands = grounding_candidates(args.bundle, args.candidates_for)
        except ValueError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(cands, ensure_ascii=False, indent=2))
        elif not any(cands.values()):
            print(f"({args.candidates_for}보다 낮은 층에 기존 개념 없음 — 접지 후보 없음)")
        else:
            for layer, lines in cands.items():
                print(f"## {layer}")
                for line in lines:
                    print(line)
        return 0

    findings = lint(args.bundle)
    for path, msg in findings:
        print(f"warn {path}  {msg}")
    print(f"접지 린트: warn {len(findings)}건" if findings else "접지 린트: 위반 없음")
    return 1 if (findings and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
