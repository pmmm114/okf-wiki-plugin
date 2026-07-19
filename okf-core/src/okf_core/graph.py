"""링크 그래프 (T-P2-4).

- ``build_graph``: nodes(파일·type·resource) + edges(번들 내부 .md 링크, 대상
  존재 시만) — ``--json`` 출력 형식 그대로의 dict.
- ``linked_to``: 역링크 조회. 매칭은 노드의 상대경로 또는 frontmatter
  ``resource`` URI에 대한 부분일치 휴리스틱, 무매칭이면 빈 결과(무출력).
"""

from __future__ import annotations

import argparse
import json
import posixpath
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


def build_graph(root: str | Path) -> dict:
    parsed = walk_bundle(root)
    existing = {rel for rel, _ in parsed}

    nodes = []
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
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
    return {"nodes": nodes, "edges": edges}


def linked_to(graph: dict, query: str) -> list[str]:
    """query가 경로 또는 resource URI에 부분일치하는 노드로 들어오는 역링크 파일 목록."""
    matched = {
        n["file"]
        for n in graph["nodes"]
        if query in n["file"] or (n["resource"] and query in n["resource"])
    }
    return sorted({e["from"] for e in graph["edges"] if e["to"] in matched})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf graph", description="번들 링크 그래프")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--json", action="store_true", help="nodes/edges JSON 출력")
    ap.add_argument("--linked-to", metavar="P", help="경로·resource 부분일치 역링크 조회")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}")
        return 2
    graph = build_graph(bundle)
    if args.linked_to is not None:
        for rel in linked_to(graph, args.linked_to):  # 무매칭이면 무출력
            print(rel)
    elif args.json:
        print(json.dumps(graph, ensure_ascii=False, indent=2))
    else:
        print(f"nodes {len(graph['nodes'])}건, edges {len(graph['edges'])}건 (--json으로 상세)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
