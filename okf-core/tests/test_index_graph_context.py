"""T-P2-4 index·graph·context — 완료 기준 매핑: index 재생성→재파싱→§9 통과 /
graph의 orders↔customers↔sales 에지 검출 / context 출력이 max-chars 이내."""

import json
import shutil
from pathlib import Path

from okf_core.context import build_context
from okf_core.graph import build_graph, linked_to
from okf_core.index import write_indexes
from okf_core.parser import parse
from okf_core.validate import validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"


def test_index_regenerate_reparse_conformant(tmp_path):
    bundle = tmp_path / "bundle"
    shutil.copytree(APPENDIX_A, bundle)
    written = write_indexes(bundle)
    assert written == ["datasets/index.md", "index.md", "tables/index.md"]

    # 자기 출력이 컨포먼트 — error는 물론 warn도 없어야 함
    assert validate_bundle(bundle) == []

    root = parse(bundle / "index.md")
    assert root.frontmatter == {"okf_version": "0.1"}  # 기존 선언 보존(§11)
    sub = parse(bundle / "tables" / "index.md")
    assert sub.frontmatter is None and sub.fm_error is None  # 비루트는 frontmatter 없음
    # §6 형식: 섹션 헤딩 + `* [Title](url) - description`
    assert "# Contents" in sub.body
    assert "* [Orders](orders.md) - One row per completed customer order." in sub.body
    assert "* [datasets](datasets/)" in (bundle / "index.md").read_text(encoding="utf-8")


def test_graph_detects_triangle_edges():
    graph = build_graph(APPENDIX_A)
    edges = {(e["from"], e["to"]) for e in graph["edges"]}
    triangle = {
        ("datasets/sales.md", "tables/orders.md"),
        ("datasets/sales.md", "tables/customers.md"),
        ("tables/orders.md", "tables/customers.md"),
        ("tables/orders.md", "datasets/sales.md"),
        ("tables/customers.md", "tables/orders.md"),
        ("tables/customers.md", "datasets/sales.md"),
    }
    assert triangle <= edges
    files = {n["file"] for n in graph["nodes"]}
    assert "index.md" in files and "datasets/sales.md" in files


def test_graph_linked_to_by_path_and_resource():
    graph = build_graph(APPENDIX_A)
    expected = ["datasets/sales.md", "tables/index.md", "tables/orders.md"]
    assert linked_to(graph, "customers.md") == expected  # 경로 부분일치
    assert linked_to(graph, "t=customers") == expected  # resource URI 부분일치
    assert linked_to(graph, "no-such-thing") == []  # 무매칭이면 무출력


def test_graph_json_shape():
    graph = build_graph(APPENDIX_A)
    payload = json.loads(json.dumps(graph))  # JSON 직렬화 가능 형태 고정
    assert set(payload) == {"nodes", "edges"}
    assert all(set(n) == {"file", "type", "resource"} for n in payload["nodes"])
    assert all(set(e) == {"from", "to"} for e in payload["edges"])


def test_context_within_budget_and_line_format():
    out = build_context(APPENDIX_A)
    assert out.startswith("<okf-context>\n") and out.endswith("\n</okf-context>")
    assert len(out) <= 8000
    lines = out.split("\n")[1:-1]
    assert (
        "datasets/sales.md [BigQuery Dataset] — All sales-related tables for the retail business."
        in lines
    )
    assert not any(line.startswith(("index.md", "tables/index.md")) for line in lines)

    for budget in (120, 300):
        assert len(build_context(APPENDIX_A, max_chars=budget)) <= budget
