"""링크 그래프 (T-P2-4).

- ``build_graph``: nodes(파일·type·resource) + edges(번들 내부 .md 링크, 대상
  존재 시만) — ``--json`` 출력 형식 그대로의 dict. ``edges_from=KEY``를 주면
  그 frontmatter **리스트 키를 타입 엣지**(``typed_edges``: from·to·via)로 함께
  수집한다 — KEY는 엔진이 해석하지 않는 임의 축이다(taxonomy-neutral). 무지정
  기본 출력은 불변(``typed_edges`` 키 없음).
- ``linked_to``: 역링크 조회. 매칭은 노드의 상대경로 또는 frontmatter
  ``resource`` URI에 대한 부분일치 휴리스틱, 무매칭이면 빈 결과(무출력).
- ``chain``: ``typed_edges``를 하향 전이 순회해 근거 사슬을 반환(판단→근거).
"""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
from collections import deque
from pathlib import Path

from okf_core.parser import FORM_EXTERNAL, walk_bundle


def _resolve(rel: str, target: str) -> str | None:
    """번들 내부 .md 링크 대상을 번들 상대경로로 정규화. 대상 아님 → None."""
    t = target.split("#", 1)[0]
    if not t or t.endswith("/") or not t.endswith(".md"):
        return None
    if t.startswith("/"):
        return t.lstrip("/")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), t))
    return None if resolved.startswith("..") else resolved


def build_graph(root: str | Path, edges_from: str | None = None) -> dict:
    """번들 링크 그래프. edges_from(frontmatter 리스트 키)를 주면 그 키의 대상들을
    타입 엣지(typed_edges)로 함께 수집한다 — 본문 링크 edges와 나란히, 실재 대상만.
    무지정이면 typed_edges 키를 넣지 않는다(기본 계약 불변)."""
    parsed = walk_bundle(root)
    existing = {rel for rel, _ in parsed}

    nodes = []
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    typed: list[dict[str, str]] = []
    typed_seen: set[tuple[str, str]] = set()
    for rel, doc in parsed:
        fm = doc.frontmatter or {}
        type_val = fm.get("type")
        resource = fm.get("resource")
        nodes.append(
            {
                "file": rel,
                "type": type_val if isinstance(type_val, str) else None,
                "resource": resource if isinstance(resource, str) else None,
            }
        )
        for link in doc.links:
            if link.form == FORM_EXTERNAL:
                continue
            resolved = _resolve(rel, link.target)
            if resolved is None or resolved not in existing:
                continue
            if (rel, resolved) not in seen:
                seen.add((rel, resolved))
                edges.append({"from": rel, "to": resolved})
        if edges_from:
            raw = fm.get(edges_from)
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, str):
                    continue
                resolved = _resolve(rel, item)
                if resolved is None or resolved not in existing:
                    continue  # dangling·비.md 대상은 엣지에서 제외(본문 링크와 동일)
                if (rel, resolved) not in typed_seen:
                    typed_seen.add((rel, resolved))
                    typed.append({"from": rel, "to": resolved, "via": edges_from})
    graph: dict = {"nodes": nodes, "edges": edges}
    if edges_from:
        graph["typed_edges"] = typed
    return graph


def linked_to(graph: dict, query: str) -> list[str]:
    """query가 경로 또는 resource URI에 부분일치하는 노드로 들어오는 역링크 파일 목록."""
    matched = {
        n["file"]
        for n in graph["nodes"]
        if query in n["file"] or (n["resource"] and query in n["resource"])
    }
    return sorted({e["from"] for e in graph["edges"] if e["to"] in matched})


def chain(graph: dict, start: str, via: str | None = None) -> list[str]:
    """start에서 typed_edges(via 일치분)를 하향 전이 순회해 도달하는 개념 경로 목록.

    BFS + 정렬로 결정론적이며 start 자신은 제외한다. 근거 사슬 조회 —
    판단(예: wisdom) 개념에서 그 근거(knowledge→information→…)를 따라 내려간다.
    무매칭·뿌리 개념은 빈 목록.
    """
    adj: dict[str, list[str]] = {}
    for e in graph.get("typed_edges", []):
        if via is None or e["via"] == via:
            adj.setdefault(e["from"], []).append(e["to"])
    order: list[str] = []
    seen = {start}
    queue = deque(sorted(adj.get(start, [])))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        order.append(node)
        for nxt in sorted(adj.get(node, [])):
            if nxt not in seen:
                queue.append(nxt)
    return order


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf graph", description="번들 링크 그래프")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--json", action="store_true", help="nodes/edges JSON 출력")
    ap.add_argument("--linked-to", metavar="P", help="경로·resource 부분일치 역링크 조회")
    ap.add_argument("--edges-from", metavar="KEY", help="frontmatter 리스트 키를 타입 엣지로")
    ap.add_argument("--chain", metavar="C", help="개념 C의 근거 사슬 하향 순회(--edges-from 필요)")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}")
        return 2

    if args.chain is not None:
        if not args.edges_from:
            print("오류: --chain은 --edges-from KEY가 필요함", file=sys.stderr)
            return 2
        graph = build_graph(bundle, edges_from=args.edges_from)
        for rel in chain(graph, args.chain, via=args.edges_from):  # 무매칭이면 무출력
            print(rel)
        return 0

    graph = build_graph(bundle, edges_from=args.edges_from)
    if args.linked_to is not None:
        for rel in linked_to(graph, args.linked_to):  # 무매칭이면 무출력
            print(rel)
    elif args.json:
        print(json.dumps(graph, ensure_ascii=False, indent=2))
    else:
        summary = f"nodes {len(graph['nodes'])}건, edges {len(graph['edges'])}건"
        if args.edges_from:
            summary += f", typed_edges {len(graph['typed_edges'])}건"
        print(f"{summary} (--json으로 상세)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
