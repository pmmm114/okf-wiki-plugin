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


def test_layers_md_has_single_json_block():
    # 로더는 파일의 마지막 json 블록을 정본으로 읽는다(blocks[-1]) — 산문 절(§8·§9 등)에
    # json 펜스가 추가되면 spec이 오염되므로 LAYERS.md의 json 블록은 정확히 1개여야 한다
    with open(okf_layers._LAYERS_MD, encoding="utf-8") as f:
        blocks = okf_layers._JSON_BLOCK.findall(f.read())
    assert len(blocks) == 1


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


def test_parse_context_meta_keeps_unclassified():
    # 신호·맵은 전체 개념 우주가 필요하다 — 미분류 섹션도 layer=None으로 보존
    ctx = (
        "<okf-context>\n"
        "## information\ninfo.md [Fact] — 사실 요약\n"
        "## (unclassified)\nplain.md [Note]\nsub/other.md [] — 타입 없음\n"
        "</okf-context>"
    )
    assert okf_layers.parse_context_meta(ctx) == [
        {"path": "info.md", "type": "Fact", "description": "사실 요약", "layer": "information"},
        {"path": "plain.md", "type": "Note", "description": None, "layer": None},
        {"path": "sub/other.md", "type": None, "description": "타입 없음", "layer": None},
    ]


def test_topic_of_directory_prefix():
    assert okf_layers.topic_of("root.md") == "."
    assert okf_layers.topic_of("produce/tomato.md") == "produce"
    assert okf_layers.topic_of("a/b/deep.md") == "a/b"


def _signals_fixture():
    meta = [
        {"path": "produce/f1.md", "type": "Fact", "description": "사실1", "layer": "information"},
        {"path": "produce/f2.md", "type": "Fact", "description": "사실2", "layer": "information"},
        {"path": "produce/note.md", "type": "Note", "description": None, "layer": None},
        {"path": "wise.md", "type": "Convention", "description": "판단", "layer": "wisdom"},
    ]
    graph = {
        "nodes": [],
        "edges": [],
        "typed_edges": [
            {"from": "wise.md", "to": "produce/f1.md", "via": "derived_from"},
            {"from": "produce/f2.md", "to": "produce/f1.md", "via": "derived_from"},
        ],
    }
    return meta, graph


def test_build_signals_counts_include_unclassified():
    meta, graph = _signals_fixture()
    payload = okf_layers.build_signals(SPEC, meta, graph)
    by_topic = {entry["topic"]: entry for entry in payload["topics"]}
    assert by_topic["produce"]["counts"] == {"information": 2, "(unclassified)": 1}
    assert by_topic["."]["counts"] == {"wisdom": 1}


def test_build_signals_focus_descending_by_refs():
    meta, graph = _signals_fixture()
    payload = okf_layers.build_signals(SPEC, meta, graph)
    by_topic = {entry["topic"]: entry for entry in payload["topics"]}
    # f1은 파생 유입 2건(참조 집중), 그 외 유입 없음
    assert by_topic["produce"]["focus"] == [{"path": "produce/f1.md", "refs": 2}]
    assert by_topic["."]["focus"] == []


def test_build_signals_surfaces_ungrounded_upper():
    meta = [
        {"path": "float.md", "type": "Model", "description": "떠 있는 이해", "layer": "knowledge"},
    ]
    graph = {"nodes": [], "edges": [], "typed_edges": []}
    payload = okf_layers.build_signals(SPEC, meta, graph)
    assert payload["topics"][0]["ungrounded"] == ["float.md"]


def test_ungrounded_paths_matches_check_rule():
    # 신호와 린트 규칙 2는 같은 판정을 공유한다 — 단일 원천 확인
    layer_map = {"float.md": "wisdom", "info.md": "information"}
    graph = {
        "nodes": [{"file": "info.md", "type": "Fact", "resource": "https://ex.org"}],
        "edges": [],
        "typed_edges": [],
    }
    assert okf_layers.ungrounded_paths(SPEC, layer_map, graph) == ["float.md"]
    lint_paths = [
        p
        for p, msg in okf_layers.check(SPEC, layer_map, graph)
        if "미접지" in msg and "근거" in msg
    ]
    assert lint_paths == ["float.md"]


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
