"""인식층 접지 린트 (Epic #173 U5) — 순수 로직·단일 원천 로드·파서 검증.

엔진 서브프로세스 없이 check()/parse_layer_map()/load_layers_spec()를 직접 친다
(수집 gather()는 bin/okf 셔틀 경유라 별도 스모크로 확인).
"""

from __future__ import annotations

import okf_layers
import pytest

SPEC = {
    "field": "layer",
    "order": ["information", "knowledge", "wisdom"],
    "derivation_field": "derived_from",
    "rules": {
        "derivation_strictly_downward": True,
        "information_requires_source": True,
        "upper_requires_derived_from": True,
    },
}


def test_load_layers_spec_from_single_source():
    # LAYERS.md의 기계 판독 블록이 어휘·순서 단일 원천 — 하드코딩 아님
    spec = okf_layers.load_layers_spec()
    assert spec["field"] == "layer"
    assert spec["order"] == ["information", "knowledge", "wisdom"]
    assert spec["derivation_field"] == "derived_from"
    assert spec["rules"]["derivation_strictly_downward"] is True


def test_parse_layer_map_from_grouped_context():
    ctx = (
        "<okf-context>\n"
        "## information\ninfo.md [Fact] — 사실\n"
        "## wisdom\nwise.md [Convention] — 판단\n"
        "## (unclassified)\nplain.md [Note]\n"
        "</okf-context>"
    )
    assert okf_layers.parse_layer_map(ctx) == {"info.md": "information", "wise.md": "wisdom"}


def test_check_clean_chain_no_findings():
    layer_map = {"info.md": "information", "know.md": "knowledge", "wise.md": "wisdom"}
    graph = {
        "nodes": [
            {"file": "info.md", "type": "Fact", "resource": "https://ex.org/t"},
            {"file": "know.md", "type": "Model", "resource": None},
            {"file": "wise.md", "type": "Convention", "resource": None},
        ],
        "edges": [],
        "typed_edges": [
            {"from": "know.md", "to": "info.md", "via": "derived_from"},
            {"from": "wise.md", "to": "know.md", "via": "derived_from"},
            {"from": "wise.md", "to": "info.md", "via": "derived_from"},
        ],
    }
    assert okf_layers.check(SPEC, layer_map, graph) == []


def test_check_detects_ordering_violation():
    # info가 wisdom에서 파생 — 역방향(엄격 하향 위반)
    layer_map = {"info.md": "information", "wise.md": "wisdom"}
    graph = {
        "nodes": [
            {"file": "info.md", "type": "Fact", "resource": "https://ex.org/t"},
            {"file": "wise.md", "type": "Convention", "resource": None},
        ],
        "edges": [],
        "typed_edges": [{"from": "info.md", "to": "wise.md", "via": "derived_from"}],
    }
    findings = okf_layers.check(SPEC, layer_map, graph)
    assert any("정초 순서 위반" in msg for _, msg in findings)
    assert any(path == "info.md" for path, _ in findings)


def test_check_detects_missing_grounding():
    # wisdom인데 derived_from 없음 + information인데 resource 없음
    layer_map = {"lonely.md": "wisdom", "fact.md": "information"}
    graph = {
        "nodes": [
            {"file": "lonely.md", "type": "Convention", "resource": None},
            {"file": "fact.md", "type": "Fact", "resource": None},
        ],
        "edges": [],
        "typed_edges": [],
    }
    by_path = dict(okf_layers.check(SPEC, layer_map, graph))
    assert "근거" in by_path["lonely.md"]  # 상위 층 미접지
    assert "출처" in by_path["fact.md"]  # 정보 층 미접지


def test_check_respects_rules_toggle():
    # 규칙을 끄면 해당 검사는 발화하지 않는다(어휘·순서·규칙 전부 데이터 주도)
    spec = {**SPEC, "rules": {"derivation_strictly_downward": True}}
    layer_map = {"lonely.md": "wisdom", "fact.md": "information"}
    graph = {"nodes": [], "edges": [], "typed_edges": []}
    assert okf_layers.check(spec, layer_map, graph) == []  # 접지 규칙 off → 무발화


# --- 접지 후보 질의 (Epic #189 U2) -----------------------------------------


def test_parse_layer_sections_preserves_full_lines():
    # parse_layer_map은 경로만, sections는 개념 줄 전체를 층별로 보존
    ctx = (
        "<okf-context>\n"
        "## information\ninfo.md [Fact] — 사실\n"
        "## knowledge\nknow.md [Model] — 이해\n"
        "## (unclassified)\nplain.md [Note]\n"
        "</okf-context>"
    )
    assert okf_layers.parse_layer_sections(ctx) == {
        "information": ["info.md [Fact] — 사실"],
        "knowledge": ["know.md [Model] — 이해"],
    }


def test_lower_layers_strictly_downward():
    # 정초 엄격 하향 — 지식은 정보만, 지혜는 정보·지식, 정보는 뿌리(후보 없음)
    assert okf_layers.lower_layers("knowledge", SPEC) == ["information"]
    assert okf_layers.lower_layers("wisdom", SPEC) == ["information", "knowledge"]
    assert okf_layers.lower_layers("information", SPEC) == []


def test_lower_layers_rejects_unknown():
    with pytest.raises(ValueError):
        okf_layers.lower_layers("데이터", SPEC)


def test_select_candidates_only_lower_layers():
    sections = {
        "information": ["info.md [Fact] — 사실"],
        "knowledge": ["know.md [Model] — 이해"],
        "wisdom": ["wise.md [Convention] — 판단"],
    }
    # 지식 승격 → 정보만(같은·상위 층 제외)
    assert okf_layers.select_candidates(sections, "knowledge", SPEC) == {
        "information": ["info.md [Fact] — 사실"],
    }
    # 지혜 승격 → 정보·지식
    assert okf_layers.select_candidates(sections, "wisdom", SPEC) == {
        "information": ["info.md [Fact] — 사실"],
        "knowledge": ["know.md [Model] — 이해"],
    }
    # 정보 승격 → 뿌리라 후보 없음
    assert okf_layers.select_candidates(sections, "information", SPEC) == {}
