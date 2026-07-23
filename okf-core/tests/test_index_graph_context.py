"""T-P2-4 index·graph·context — 완료 기준 매핑: index 재생성→재파싱→§9 통과 /
graph의 orders↔customers↔sales 에지 검출 / context 출력이 max-chars 이내."""

import json
import shutil
from pathlib import Path

from okf_core.context import build_context
from okf_core.graph import build_graph, chain, linked_to
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
    # 하위 디렉터리 링크는 베어 `datasets/`(디렉터리 링크)가 아니라 그 index.md를
    # 가리켜야 원격 repo 정적 웹뷰에서도 해소된다(문서간 링크 이식성).
    root_text = (bundle / "index.md").read_text(encoding="utf-8")
    assert "* [datasets](datasets/index.md)" in root_text
    assert "* [datasets](datasets/)" not in root_text  # 베어 디렉터리 링크 회귀 금지
    assert (bundle / "datasets" / "index.md").is_file()  # 링크 대상 실재


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


def _concept(dirpath, name, type_val, description, layer=None):
    fm = [f"type: {type_val}", f"description: {description}"]
    if layer is not None:
        fm.append(f"layer: {layer}")
    body = "---\n" + "\n".join(fm) + "\n---\n\n# " + name + "\n"
    (dirpath / name).write_text(body, encoding="utf-8")


def _axis_bundle(tmp_path):
    bundle = tmp_path / "axis"
    bundle.mkdir()
    _concept(bundle, "info.md", "Fact", "토마토는 식물학적으로 과일이다.", layer="information")
    _concept(bundle, "know.md", "Model", "식물학적 분류와 요리적 분류는 다르다.", layer="knowledge")
    _concept(bundle, "wise.md", "Convention", "토마토는 과일 샐러드에 넣지 않는다.", layer="wisdom")
    _concept(bundle, "plain.md", "Note", "층 미표시 개념.")
    return bundle


def test_context_group_by_axis(tmp_path):
    bundle = _axis_bundle(tmp_path)
    out = build_context(bundle, group_by="layer")
    lines = out.split("\n")[1:-1]  # 래퍼 제외
    # 값 알파벳순 섹션 + 미기재(None)는 맨 뒤
    assert [ln for ln in lines if ln.startswith("## ")] == [
        "## information",
        "## knowledge",
        "## wisdom",
        "## (unclassified)",
    ]
    # 각 개념이 자기 섹션 바로 아래에
    assert lines[lines.index("## information") + 1].startswith("info.md ")
    assert lines[lines.index("## knowledge") + 1].startswith("know.md ")
    assert lines[lines.index("## wisdom") + 1].startswith("wise.md ")
    assert lines[lines.index("## (unclassified)") + 1].startswith("plain.md ")
    # 예산 준수(그룹 헤딩 포함)
    assert len(build_context(bundle, group_by="layer", max_chars=200)) <= 200


def test_context_filter_axis(tmp_path):
    out = build_context(_axis_bundle(tmp_path), filter_key="layer", filter_value="wisdom")
    lines = out.split("\n")[1:-1]
    assert lines == ["wise.md [Convention] — 토마토는 과일 샐러드에 넣지 않는다."]
    assert not any(ln.startswith("## ") for ln in lines)  # 필터만 하면 섹션 없음


def test_context_axis_is_section9_neutral_and_default_unchanged(tmp_path):
    bundle = _axis_bundle(tmp_path)
    # §9 중립성: layer는 미지 optional 키라 판정에 영향 없음(전부 컨포먼트, warn 0)
    assert validate_bundle(bundle) == []
    # 무플래그 출력은 축 도입 전과 동일(그룹 헤딩 없음, walk 정렬 순서)
    default = build_context(bundle).split("\n")[1:-1]
    assert not any(ln.startswith("## ") for ln in default)
    assert default[0].startswith("info.md ")


def _chain_bundle(tmp_path):
    """토마토 정초 사슬: wisdom → knowledge → information (+ dangling 대상 1)."""
    b = tmp_path / "chain"
    b.mkdir()
    (b / "info.md").write_text(
        "---\ntype: Fact\nlayer: information\nresource: https://example.org/tomato\n"
        "description: 토마토는 과일이다.\n---\n\n# info\n",
        encoding="utf-8",
    )
    (b / "know.md").write_text(
        "---\ntype: Model\nlayer: knowledge\ndescription: 분류 기준 차이.\n"
        "derived_from:\n  - /info.md\n---\n\n# know\n",
        encoding="utf-8",
    )
    (b / "wise.md").write_text(
        "---\ntype: Convention\nlayer: wisdom\ndescription: 샐러드 지침.\n"
        "derived_from:\n  - /know.md\n  - /info.md\n  - /missing.md\n---\n\n# wise\n",
        encoding="utf-8",
    )
    return b


def test_graph_default_shape_unchanged_without_edges_from(tmp_path):
    # edges_from 무지정 시 typed_edges 키 없음 — 기본 계약 불변
    assert "typed_edges" not in build_graph(_chain_bundle(tmp_path))


def test_graph_typed_edges_from_frontmatter(tmp_path):
    g = build_graph(_chain_bundle(tmp_path), edges_from="derived_from")
    assert {(e["from"], e["to"], e["via"]) for e in g["typed_edges"]} == {
        ("know.md", "info.md", "derived_from"),
        ("wise.md", "know.md", "derived_from"),
        ("wise.md", "info.md", "derived_from"),
    }  # 실재 대상만 — /missing.md(dangling)는 엣지에서 제외
    assert all(set(e) == {"from", "to", "via"} for e in g["typed_edges"])
    assert g["edges"] == []  # 본문 링크 없음 — 타입 엣지와 본문 엣지는 별개
    assert {n["file"] for n in g["nodes"]} == {"info.md", "know.md", "wise.md"}


def test_graph_chain_traverses_provenance_downward(tmp_path):
    g = build_graph(_chain_bundle(tmp_path), edges_from="derived_from")
    # 판단(wisdom)에서 근거 사슬을 하향 전이 순회(BFS 정렬 결정론)
    assert chain(g, "wise.md", via="derived_from") == ["info.md", "know.md"]
    assert chain(g, "know.md", via="derived_from") == ["info.md"]
    assert chain(g, "info.md", via="derived_from") == []  # 정보는 사슬의 뿌리
