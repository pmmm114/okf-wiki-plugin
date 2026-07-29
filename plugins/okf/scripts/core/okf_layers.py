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

탐색 제공자(Epic #197 U1·U2) — 이 스크립트는 EXPLORE.md 탐색 계약의 **내장 기본
제공자**이기도 하다(같은 계약의 첫 구현체일 뿐 특권 없음): ``signals <bundle>``이
승격 신호(하위층 밀집·참조 집중·미접지·미분류 규모)를, ``map <bundle> [--topic]``이
주제 층 맵을 계약 응답으로 낸다. 둘 다 자문 데이터 제시다 — 임계값·판정 없음
(LAYERS §9 금지 4). 게이트(okf_promote)는 이 출력을 소비하지 않는다(계약 불변식 1).

CLI: ``okf_layers.py <bundle> [--strict]``(접지 린트) · ``--candidates-for <layer>
[--json]``(접지 후보) · ``okf_layers.py signals <bundle> [--json]`` ·
``okf_layers.py map <bundle> [--topic T] [--layer L] [--json]``(탐색 계약).
기본은 자문(발견해도 exit 0), --strict면 발견 시 exit 1.
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


def _section_head(line: str, known: frozenset[str]) -> tuple[bool, str | None] | None:
    """섹션 전환 판정 — ``(전환함, 현재 층|None)`` 또는 전환이 아니면 ``None``.

    ``## `` 접두만 보면 **개념 줄에 섞여 들어온 마크다운 헤딩**이 섹션을 전환시킨다.
    엔진이 렌더를 1줄/개념으로 지키더라도(#294에서 함께 고쳤다) 파서 쪽에서 한 번 더
    막는다 — 판정 입력의 어휘는 LAYERS 단일원천이 정하는 닫힌 집합이고, 그 밖의
    ``## X``는 섹션이 아니라 내용이다. 두 층 어디가 뚫려도 다른 한쪽이 남는다.
    """
    if not line.startswith("## "):
        return None
    head = line[3:].strip()
    if head == _UNCLASSIFIED:
        return True, None
    if head in known:
        return True, head
    return None  # 어휘 밖 헤딩 = 섹션이 아니다(내용으로 흘려보낸다)


def _known_layers() -> frozenset[str]:
    """LAYERS 단일원천의 층 어휘 — 섹션으로 인정할 헤딩 집합."""
    return frozenset(load_layers_spec()["values"])


def parse_layer_map(context_output: str) -> dict:
    """``okf context --group-by <field>`` 출력에서 {개념경로: 층값}을 만든다.

    미분류 섹션·래퍼는 제외한다(층 미기재 개념은 맵에 없다). 각 개념 줄은 엔진
    형식 ``<경로> [<type>] …``이라 첫 ``' ['`` 앞이 경로다.
    """
    known = _known_layers()
    layer_map: dict[str, str] = {}
    current: str | None = None
    for line in context_output.split("\n"):
        switched = _section_head(line, known)
        if switched is not None:
            current = switched[1]
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
    known = _known_layers()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in context_output.split("\n"):
        switched = _section_head(line, known)
        if switched is not None:
            current = switched[1]
        elif line and line not in (_CTX_OPEN, _CTX_CLOSE) and current is not None:
            sections.setdefault(current, []).append(line)
    return sections


def parse_context_meta(context_output: str) -> list[dict]:
    """``okf context --group-by <field>`` 출력을 개념 메타 리스트로 파싱한다.

    ``parse_layer_map``·``parse_layer_sections``와 같은 섹션 스캐너지만 **미분류
    섹션을 버리지 않는다** — 신호(미분류 규모)·층 맵은 전체 개념 우주가 필요하다.
    항목은 ``{path, type, description, layer}``이고 미분류는 ``layer=None``.
    개념 줄은 엔진 형식 ``<경로> [<type>] — <핵심>``이라 각 조각을 방어적으로 뽑는다.
    """
    known = _known_layers()
    meta: list[dict] = []
    current: str | None = None
    for line in context_output.split("\n"):
        switched = _section_head(line, known)
        if switched is not None:
            current = switched[1]
        elif line and line not in (_CTX_OPEN, _CTX_CLOSE):
            path = line.split(" [", 1)[0].strip()
            if not path:
                continue
            typ: str | None = None
            desc: str | None = None
            if " [" in line:
                rest = line.split(" [", 1)[1]
                typ = rest.split("]", 1)[0].strip() or None
                if "— " in rest:
                    desc = rest.split("— ", 1)[1].strip() or None
            meta.append({"path": path, "type": typ, "description": desc, "layer": current})
    return meta


def topic_of(path: str) -> str:
    """개념 경로의 주제(디렉토리 프리픽스). 루트 직속은 ``"."``."""
    head = path.rsplit("/", 1)[0] if "/" in path else ""
    return head or "."


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


def build_signals(spec: dict, meta: list[dict], graph: dict) -> dict:
    """승격 신호 리포트(Epic #197 U1) — EXPLORE 계약 signals 응답(순수 함수).

    주제(디렉토리)별로 ① 층 계수(``counts``, 미분류 포함 — 하위층 밀집·미분류 규모),
    ② 미접지 상위 개념(``ungrounded``), ③ 파생 유입 집계(``focus``, 참조 집중 내림차순)를
    모은다. 자문 데이터 제시가 전부다 — 임계값·판정 없음(LAYERS §9 금지 4).
    """
    layer_map = {m["path"]: m["layer"] for m in meta if m["layer"]}
    refs: dict[str, int] = {}
    for edge in graph.get("typed_edges", []):
        refs[edge["to"]] = refs.get(edge["to"], 0) + 1

    topics: dict[str, dict] = {}

    def bucket(topic: str) -> dict:
        return topics.setdefault(topic, {"counts": {}, "ungrounded": [], "focus": []})

    for m in meta:
        counts = bucket(topic_of(m["path"]))["counts"]
        key = m["layer"] or _UNCLASSIFIED
        counts[key] = counts.get(key, 0) + 1
    for path in ungrounded_paths(spec, layer_map, graph):
        bucket(topic_of(path))["ungrounded"].append(path)
    for path, count in sorted(refs.items(), key=lambda kv: (-kv[1], kv[0])):
        bucket(topic_of(path))["focus"].append({"path": path, "refs": count})
    return {"topics": [{"topic": topic, **data} for topic, data in sorted(topics.items())]}


def build_map(
    spec: dict, meta: list[dict], graph: dict, topic: str = ".", layer: str | None = None
) -> dict:
    """주제 층 맵(Epic #197 U2) — EXPLORE 계약 map 응답(순수 함수).

    주제(디렉토리 프리픽스, ``.``=전체)의 인식 지형을 한 뷰로: 개념별
    경로·층(미분류 ``None``)·type·description·정초 재료(``derived_from``)·역링크
    수(``refs``, 본문 링크 유입) + 주제 밖으로 나가는 정초 엣지(``edges_out``).
    미지의 층 필터는 ValueError — 계약 소비자가 오타를 조용히 빈 맵으로 받지 않게.
    """
    if layer is not None and layer not in spec["order"]:
        raise ValueError(f"미지의 층: {layer!r} (허용: {spec['order']})")
    topic = (topic or ".").strip("/") or "."
    derived: dict[str, list[str]] = {}
    for edge in graph.get("typed_edges", []):
        derived.setdefault(edge["from"], []).append(edge["to"])
    backrefs: dict[str, int] = {}
    for edge in graph.get("edges", []):
        backrefs[edge["to"]] = backrefs.get(edge["to"], 0) + 1

    def in_topic(path: str) -> bool:
        if topic == ".":
            return True
        head = topic_of(path)
        return head == topic or head.startswith(topic + "/")

    concepts = []
    edges_out = []
    for m in sorted(meta, key=lambda entry: entry["path"]):
        if not in_topic(m["path"]) or (layer is not None and m["layer"] != layer):
            continue
        materials = derived.get(m["path"], [])
        concepts.append(
            {
                "path": m["path"],
                "layer": m["layer"],
                "type": m["type"],
                "description": m["description"],
                "derived_from": materials,
                "refs": backrefs.get(m["path"]),
            }
        )
        edges_out.extend(
            {"from": m["path"], "to": target} for target in materials if not in_topic(target)
        )
    return {"topic": topic, "concepts": concepts, "edges_out": edges_out}


def _field_errors(item: dict, where: str, types: dict) -> list[str]:
    """선택 필드의 존재-시-타입 검사 — 검증기 공용(미지 필드는 무시)."""
    errors = []
    for key, expected in types.items():
        if key in item and item[key] is not None and not isinstance(item[key], expected):
            errors.append(f"{where}.{key} 형식 오류")
    return errors


def validate_signals_payload(payload) -> list[str]:
    """signals 응답의 계약 검증(소비 측 소유) — 오류 목록, 빈 리스트면 통과.

    필수는 ``topics[].topic``뿐이고 미지 필드는 무시한다(계약 §2 관용).
    """
    if not isinstance(payload, dict):
        return ["payload가 객체가 아님"]
    topics = payload.get("topics")
    if not isinstance(topics, list):
        return ["필수 필드 없음/형식 오류: topics(list)"]
    errors: list[str] = []
    for index, entry in enumerate(topics):
        where = f"topics[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("topic"), str):
            errors.append(f"{where}.topic(str) 필수")
            continue
        errors.extend(
            _field_errors(entry, where, {"counts": dict, "ungrounded": list, "focus": list})
        )
    return errors


def validate_map_payload(payload) -> list[str]:
    """map 응답의 계약 검증(소비 측 소유) — 오류 목록, 빈 리스트면 통과.

    필수는 ``concepts[].path``뿐이고 미지 필드는 무시한다(계약 §2 관용).
    """
    if not isinstance(payload, dict):
        return ["payload가 객체가 아님"]
    concepts = payload.get("concepts")
    if not isinstance(concepts, list):
        return ["필수 필드 없음/형식 오류: concepts(list)"]
    errors: list[str] = []
    for index, entry in enumerate(concepts):
        where = f"concepts[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append(f"{where}.path(str) 필수")
            continue
        errors.extend(
            _field_errors(
                entry,
                where,
                {"layer": str, "type": str, "description": str, "derived_from": list, "refs": int},
            )
        )
    errors.extend(_field_errors(payload, "payload", {"edges_out": list}))
    return errors


def ungrounded_paths(spec: dict, layer_map: dict, graph: dict) -> list[str]:
    """근거(``derived_from``) 없는 상위 층 개념 경로 — check() 규칙 2와 신호 리포트가
    공유하는 단일 판정(순수 함수). 규칙이 꺼져 있으면 빈 리스트."""
    if not spec.get("rules", {}).get("upper_requires_derived_from"):
        return []
    rank = {value: index for index, value in enumerate(spec["order"])}
    derivers = {edge["from"] for edge in graph.get("typed_edges", [])}
    return [
        path
        for path, layer in sorted(layer_map.items())
        if rank.get(layer, 0) >= 1 and path not in derivers
    ]


def check(spec: dict, layer_map: dict, graph: dict) -> list[tuple[str, str]]:
    """(경로, 경고문) 목록을 반환한다 — 순수 함수(서브프로세스 없음)."""
    order = spec["order"]
    rank = {value: index for index, value in enumerate(order)}
    dfield = spec["derivation_field"]
    rules = spec.get("rules", {})
    typed = graph.get("typed_edges", [])
    resource = {n["file"]: n.get("resource") for n in graph.get("nodes", [])}
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
    for path in ungrounded_paths(spec, layer_map, graph):
        findings.append((path, f"미접지: {layer_map[path]} 개념에 근거(`{dfield}`) 없음"))

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


def _grouped_context(bundle: str, spec: dict) -> str:
    """층 축으로 묶은 컨텍스트 출력 — 린트·후보·신호·맵이 공유하는 엔진 호출 한 곳."""
    return _okf(["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)])


def _typed_graph(bundle: str, spec: dict) -> dict:
    """파생 타입 엣지를 포함한 그래프 JSON — bin/okf 셔틀 경유."""
    return json.loads(_okf(["graph", bundle, "--edges-from", spec["derivation_field"], "--json"]))


def gather(bundle: str, spec: dict) -> tuple[dict, dict]:
    """엔진 출력에서 (층 맵, 그래프)를 모은다 — bin/okf 셔틀 경유."""
    return parse_layer_map(_grouped_context(bundle, spec)), _typed_graph(bundle, spec)


def bundle_layer_sections(bundle: str, spec: dict | None = None) -> dict:
    """번들 개념을 {층: [개념 줄]}로 반환한다 — ``okf context --group-by <field>``를
    재사용(bin/okf 셔틀). 접지 후보(하위층, U2)·근사중복 대조(같은 층, U3)가 공유하는
    번들 층 원천이다.
    """
    spec = spec or load_layers_spec()
    return parse_layer_sections(_grouped_context(bundle, spec))


def grounding_candidates(bundle: str, target_layer: str, spec: dict | None = None) -> dict:
    """``target_layer`` 개념이 ``derived_from``으로 접지할 **하위층 기존 개념**을 층별로
    반환한다(승격 판정용). ``bundle_layer_sections``에서 정초 엄격 하향으로 걸러 —
    지식은 정보를, 지혜는 지식·정보를 후보로 본다. 정보(뿌리)면 빈 dict.
    """
    spec = spec or load_layers_spec()
    return select_candidates(bundle_layer_sections(bundle, spec), target_layer, spec)


def lint(bundle: str) -> list[tuple[str, str]]:
    spec = load_layers_spec()
    layer_map, graph = gather(bundle, spec)
    return check(spec, layer_map, graph)


def _print_signals(payload: dict) -> None:
    """signals 응답의 사람 가독 렌더 — 계약 필드만 소비한다."""
    for entry in payload.get("topics", []):
        print(f"## {entry['topic']}")
        counts = entry.get("counts", {})
        if counts:
            print("  층: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        for path in entry.get("ungrounded", []):
            print(f"  미접지: {path}")
        for item in entry.get("focus", []):
            print(f"  참조 집중: {item['path']} ({item['refs']})")


def _print_map(payload: dict) -> None:
    """map 응답의 사람 가독 렌더 — 계약 필드만 소비한다."""
    print(f"## {payload.get('topic', '.')}")
    for concept in payload.get("concepts", []):
        layer = concept.get("layer") or _UNCLASSIFIED
        typ = concept.get("type") or ""
        desc = concept.get("description") or ""
        refs = concept.get("refs")
        parts = [f"  {concept['path']} [{typ}] ({layer}"]
        if refs is not None:
            parts.append(f", refs {refs}")
        parts.append(")")
        if desc:
            parts.append(f" — {desc}")
        print("".join(parts))
        for target in concept.get("derived_from", []):
            print(f"    ⤷ {target}")
    for edge in payload.get("edges_out", []):
        print(f"  주제 밖 정초: {edge['from']} → {edge['to']}")


def contract_main(argv: list[str]) -> int:
    """탐색 계약(EXPLORE.md) 호출 규약 — ``signals <bundle>`` · ``map <bundle>``.

    내장 기본 제공자의 진입점이다. 응답은 stdout JSON(``--json``) 또는 같은 데이터의
    사람 가독 렌더 — 어느 쪽이든 계약 스키마의 필드만 담는다.
    """
    ap = argparse.ArgumentParser(prog="okf_layers", description="탐색 계약 내장 제공자")
    sub = ap.add_subparsers(dest="op", required=True)
    sig = sub.add_parser("signals", help="승격 신호 리포트(자문)")
    sig.add_argument("bundle", help="번들 디렉터리 경로")
    sig.add_argument("--json", action="store_true", help="계약 JSON으로 출력")
    mp = sub.add_parser("map", help="주제 층 맵(자문)")
    mp.add_argument("bundle", help="번들 디렉터리 경로")
    mp.add_argument("--topic", default=".", help="주제(디렉토리 프리픽스, 기본 전체)")
    mp.add_argument("--layer", help="이 층의 개념만(선택)")
    mp.add_argument("--json", action="store_true", help="계약 JSON으로 출력")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.bundle):
        print(f"오류: 번들 디렉터리가 아님: {args.bundle}", file=sys.stderr)
        return 2

    spec = load_layers_spec()
    meta = parse_context_meta(_grouped_context(args.bundle, spec))
    graph = _typed_graph(args.bundle, spec)
    if args.op == "signals":
        payload = build_signals(spec, meta, graph)
        renderer = _print_signals
    else:
        try:
            payload = build_map(spec, meta, graph, topic=args.topic, layer=args.layer)
        except ValueError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 2
        renderer = _print_map
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        renderer(payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("signals", "map"):
        return contract_main(argv)
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
