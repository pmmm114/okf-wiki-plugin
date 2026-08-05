"""엔진 렌더 ↔ 플러그인 파서 왕복 대조 게이트 (#279 B1).

플러그인은 엔진의 **사람용 렌더 텍스트를 파싱**해 판정 입력으로 쓴다 —
``okf context``에 기계 출력(``--json``)이 없어서 그것밖에 없다. 그래서 엔진이
표시 형식을 다듬으면 파서가 빈 값을 돌려주고, 그 빈 값이 **정상 재료를 반려로
바꾸면서도 exit 0**으로 끝난다. 예외도 비0 종료도 없으니 사용자는 제안을 고치러
가고, 원인은 엔진 렌더 변경이다.

기존 ``test_okf_layers.py``는 파서에 **손으로 쓴 형식 문자열**을 먹인다 — 엔진과
플러그인이 같이 틀려도 초록이라 이 계열을 구조적으로 못 잡는다. 그 파일과
``test_okf_promote.py``가 docstring에서 "실번들 E2E는 별도 스모크"로 미룬 그
스모크가 바로 여기다(둘 다 미작성 상태였다).

**이 파일의 규칙: 형식 문자열을 손으로 쓰지 않는다.** 기대값은 전부 번들
frontmatter에서 나온 의미 값(경로·type·층·핵심)이고, 그 사이를 잇는 표기
(``## `` 헤딩 · ``[type]`` · ``—`` 구분자)는 **엔진이 실제로 낸 것만** 쓴다.
표기를 여기에 적어 두면 이 게이트가 잠그려는 바로 그 결합이 테스트로 복제된다.

셔틀 경유는 플러그인 테스트 계층의 ``--no-project`` 전제와 어긋나지 않는다 —
``bin/okf``는 자기 환경(``uv run --project``)을 직접 세우므로 플러그인 파이썬이
``okf_core``를 import하지 않는다. 프로덕션 경로(``okf_layers._okf``)와 같은 모양이다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import okf_explore
import okf_layers
import okf_promote
import pytest

PLUGIN = Path(__file__).resolve().parent.parent
OKF = PLUGIN / "bin" / "okf"

# 번들 재료 — (파일명, type, 층, 핵심 한 줄). 층이 None이면 미분류 섹션으로 간다.
# 정초 규칙(하향 파생·정보는 출처)을 만족시켜 린트 경고 없이 통과하게 짠다.
_INFO = ("info.md", "fact", "information", "정보층 개념의 핵심 한 줄이다.")
_KNOW = ("know.md", "insight", "knowledge", "지식층 개념의 핵심 한 줄이다.")
_WISE = ("wise.md", "principle", "wisdom", "지혜층 개념의 핵심 한 줄이다.")
_PLAIN = ("plain.md", "fact", None, "층 미기재 개념의 핵심 한 줄이다.")
_MATERIALS = (_INFO, _KNOW, _WISE, _PLAIN)


def _write(bundle: Path, name: str, type_: str, layer: str | None, gist: str, **extra) -> None:
    fm = {"type": type_}
    if layer is not None:
        fm[okf_layers.load_layers_spec()["field"]] = layer
    fm.update(extra)
    lines = ["---"]
    for key, value in fm.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines += ["---", "", f"# {name[:-3]}", "", gist, ""]
    (bundle / name).write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> str:
    """실물 번들 — 세 층 + 미분류가 모두 있어야 섹션 스캐너가 전부 운동한다."""
    root = tmp_path_factory.mktemp("roundtrip")
    spec = okf_layers.load_layers_spec()
    derive = spec["derivation_field"]
    _write(root, *_INFO, resource="https://example.invalid/src")
    _write(root, *_KNOW, **{derive: [_INFO[0]]})
    _write(root, *_WISE, **{derive: [_KNOW[0]]})
    _write(root, *_PLAIN, resource="https://example.invalid/src")
    return str(root)


@pytest.fixture(scope="module")
def rendered(bundle: str) -> str:
    """엔진이 **실제로** 낸 렌더 — 이 문자열이 이 파일의 유일한 형식 원천이다."""
    spec = okf_layers.load_layers_spec()
    proc = subprocess.run(
        [str(OKF), "context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"엔진 호출 실패(rc={proc.returncode}): {proc.stderr.strip()}"
    return proc.stdout


# --- 파서 3종 왕복 (엔진 실물 → 파서) ----------------------------------------


def test_parse_layer_map_roundtrips_real_render(rendered: str):
    """``parse_layer_map``이 실물 렌더에서 {경로: 층}을 낸다 — 미분류는 빠진다.

    이게 비면 승격 게이트의 층 판정 재료가 통째로 사라진다(조용한 반려).
    """
    assert okf_layers.parse_layer_map(rendered) == {
        name: layer for name, _type, layer, _gist in _MATERIALS if layer is not None
    }


def test_parse_layer_sections_roundtrips_real_render(rendered: str):
    """``parse_layer_sections``가 층별로 **개념 줄 전체**를 보존한다.

    줄의 표기는 단언하지 않는다 — 층 배정과 줄 수, 그리고 각 줄이 그 개념의
    경로·type·핵심을 담고 있다는 것만 본다.
    """
    sections = okf_layers.parse_layer_sections(rendered)
    assert set(sections) == {layer for _name, _type, layer, _gist in _MATERIALS if layer}
    for name, type_, layer, gist in _MATERIALS:
        if layer is None:
            assert all(name not in line for lines in sections.values() for line in lines), (
                f"미분류 {name}이 층 섹션에 들어갔다"
            )
            continue
        (line,) = [ln for ln in sections[layer] if ln.startswith(name)]
        assert type_ in line and gist in line, f"{name} 줄이 type·핵심을 잃었다: {line!r}"


def test_parse_context_meta_roundtrips_real_render(rendered: str):
    """``parse_context_meta``가 경로·type·핵심·층을 정확히 뽑는다 — 미분류 포함.

    여기가 빈 값을 내면 탐색 신호·맵의 재료 우주가 사라진다(자문이 조용히 0건).
    """
    assert okf_layers.parse_context_meta(rendered) == [
        {"path": name, "type": type_, "description": gist, "layer": layer}
        for name, type_, layer, gist in _MATERIALS
    ]


def test_query_anchor_is_rendered_but_not_parsed_as_concept(rendered: str):
    """조회 앵커가 렌더에는 있고 파서 결과에는 없다 — 엔진↔파서 교차 계약(#402).

    앵커는 엔진이 붙이고 걸러내기는 플러그인 파서가 한다. 둘은 서로를 모르므로,
    엔진이 앵커를 개념 줄 형식(``<경로> [<type>]``)으로 바꾸거나 파서 가드가 빠지면
    앵커가 유령 개념이 된다 — 위 왕복 단언들이 그때 붉어진다. 다만 **앵커가 렌더에
    있다**는 반대 방향은 거기서 안 잡힌다(엔진이 앵커를 아예 빼도 왕복은 통과한다).
    그 감도 공백을 여기서 닫는다.
    """
    assert "okf query" in rendered, "주입 말미의 조회 입구가 사라졌다 — pull 트리거 소실"
    paths = [row["path"] for row in okf_layers.parse_context_meta(rendered)]
    assert not any("okf query" in path for path in paths), paths


# --- 생산 경로 왕복 (셔틀을 직접 도는 소비자) --------------------------------


def test_gather_feeds_lint_from_real_engine(bundle: str):
    """린트 수집 경로(``gather``)가 셔틀 실물에서 층 맵·그래프를 채운다."""
    spec = okf_layers.load_layers_spec()
    layer_map, graph = okf_layers.gather(bundle, spec)
    assert layer_map, "gather가 빈 층 맵을 냈다 — 접지 린트가 조용히 무판정이 된다"
    assert layer_map == okf_layers.parse_layer_map(okf_layers._grouped_context(bundle, spec))
    assert graph.get("nodes"), "그래프가 비었다"
    # 정초 규칙을 만족하게 짠 번들이므로 경고가 없어야 한다 — 파서가 재료를
    # 흘리면 여기서 '근거 없음' 같은 유령 경고가 뜬다.
    assert okf_layers.check(spec, layer_map, graph) == []


def test_grounding_candidates_come_from_real_engine(bundle: str):
    """접지 후보(하위층 질의)가 실물 번들에서 후보를 낸다 — 빈 dict면 승격이 막힌다."""
    candidates = okf_layers.grounding_candidates(bundle, "wisdom")
    assert set(candidates) == {"information", "knowledge"}
    assert any(_KNOW[0] in line for line in candidates["knowledge"])


def test_promote_gate_gets_layer_material_from_real_engine(bundle: str):
    """#279의 조용한 고장 지점 — 승격 게이트가 쓰는 층 맵이 셔틀 실물에서 찬다.

    ``apply_proposals``는 이 맵으로 재료의 층을 읽는다. 맵이 비면 정상 재료가
    '재료 미분류'로 반려되는데 그건 정상 동작이라 **exit 0**이다. 그래서 프로덕션
    러너(``_okf_run``)를 그대로 태워 재료가 실제로 분류돼 보이는지 잠근다.
    """
    spec = okf_layers.load_layers_spec()
    layer_map = okf_layers.parse_layer_map(
        okf_promote._okf_run(
            ["context", bundle, "--group-by", spec["field"], "--max-chars", str(10**9)]
        )
    )
    assert layer_map.get(_INFO[0]) == "information"
    assert layer_map.get(_KNOW[0]) == "knowledge"


def test_update_loader_universe_is_valid_view_on_real_engine(tmp_path: Path):
    """update 로더(#351 U1)의 질의 우주가 §9 통과 집합(valid 뷰)임을 실엔진으로 잠근다.

    concept 테이블을 질의하면 규격 미달(frontmatter는 파스되지만 type이 빈 문서)도
    frontmatter가 차서 로더를 통과하고, 그 문서는 layer_map 밖이라 재라벨 게이트가
    조용히 꺼진다 — update가 §9 밖 문서를 개념으로 수선하며 착지한다(#358 리뷰 실측).
    FakeEngine은 query 응답을 고정 반환해 이 구분을 가리므로 실엔진으로만 잠긴다.
    """
    _write(tmp_path, *_INFO, resource="https://example.invalid/src")
    (tmp_path / "bad.md").write_text(
        '---\ntype: ""\ndescription: 규격 미달.\nlayer: wisdom\n---\n\n# 몸\n', encoding="utf-8"
    )
    fm = okf_promote._existing_frontmatter(str(tmp_path), _INFO[0], okf_promote._okf_run)
    assert fm["type"] == _INFO[1]
    with pytest.raises(ValueError, match="개념 아님"):
        okf_promote._existing_frontmatter(str(tmp_path), "bad.md", okf_promote._okf_run)


def test_explore_builtin_payload_comes_from_real_engine(bundle: str):
    """내장 탐색 제공자가 실물 렌더에서 신호·맵을 만든다(계약 응답까지)."""
    signals = okf_explore._builtin_payload(bundle, "signals", topic="", layer=None)
    assert okf_layers.validate_signals_payload(signals) == [], signals
    assert signals.get("topics"), "신호가 주제를 못 냈다 — 메타 파싱이 비었다"

    topic = okf_layers.topic_of(_INFO[0])
    mapped = okf_explore._builtin_payload(bundle, "map", topic=topic, layer=None)
    assert okf_layers.validate_map_payload(mapped) == [], mapped
    assert _INFO[0] in json.dumps(mapped, ensure_ascii=False), f"맵이 재료를 잃었다: {mapped}"


# --- 렌더 주입 면역 (#294) ------------------------------------------------------
#
# description은 스펙상 자유 텍스트라 다중행이 될 수 있고, 엔진 `validate --strict`는
# 그것을 error 0 / warn 0으로 통과시킨다. 그런데 파서 3종이 `## ` 접두만 보고 섹션을
# 전환하므로, description 안의 마크다운 헤딩 한 줄이 **가짜 층 섹션**을 만들어
# 뒤따르는 정상 개념의 층을 뒤바꾼다. 특수 조작이 아니라 평범한 `description: |`이다.

# 결함은 **description frontmatter** 경로다 — 본문 폴백은 이미 첫 문장·길이 절단으로
# 보호돼 있다. `gist`가 description만 무가공 반환하는 것이 주입 통로다.
_INJECTED_DESC = "이 개념은 문서 구조를 다룬다.\n## 배경\n섹션 제목 예시를 포함한다."
_INJECT = ("inject.md", "fact", "information", "본문은 평범하다.")
_AFTER = ("zz-after.md", "fact", "information", "주입 뒤에 오는 정상 개념이다.")


@pytest.fixture(scope="module")
def injected_render(tmp_path_factory: pytest.TempPathFactory) -> str:
    """다중행 description을 담은 실물 번들의 **엔진 실제 렌더**."""
    root = tmp_path_factory.mktemp("injected")
    spec = okf_layers.load_layers_spec()
    _write(
        root,
        *_INJECT,
        description=_INJECTED_DESC,
        resource="https://example.invalid/src",
    )
    _write(root, *_AFTER, resource="https://example.invalid/src")
    proc = subprocess.run(
        [str(OKF), "context", str(root), "--group-by", spec["field"], "--max-chars", str(10**9)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"엔진 호출 실패(rc={proc.returncode}): {proc.stderr.strip()}"
    return proc.stdout


def test_render_has_no_injected_section(injected_render: str):
    """엔진 렌더의 섹션 헤딩은 층 어휘 + 미분류뿐이다 — gist가 개행을 흘리면 안 된다."""
    spec = okf_layers.load_layers_spec()
    allowed = {*spec["values"], "(unclassified)"}
    heads = [ln[3:].strip() for ln in injected_render.split("\n") if ln.startswith("## ")]
    assert set(heads) <= allowed, f"렌더에 층 어휘 밖 섹션: {sorted(set(heads) - allowed)}"


def test_parse_layer_map_survives_injected_heading(injected_render: str):
    """뒤따르는 정상 개념이 **frontmatter의 층 그대로** 분류된다."""
    assert okf_layers.parse_layer_map(injected_render) == {
        _INJECT[0]: _INJECT[2],
        _AFTER[0]: _AFTER[2],
    }


def test_parse_layer_map_has_no_ghost_keys(injected_render: str):
    """맵의 키는 전부 개념 경로다 — description 조각이 개념으로 둔갑하면 안 된다."""
    ghosts = [k for k in okf_layers.parse_layer_map(injected_render) if not k.endswith(".md")]
    assert not ghosts, f"유령 개념 키: {ghosts}"


def test_parse_context_meta_survives_injected_heading(injected_render: str):
    """메타 파서도 같은 면역을 갖는다 — 미분류 보존 축은 그대로."""
    meta = {m["path"]: m["layer"] for m in okf_layers.parse_context_meta(injected_render)}
    assert meta == {_INJECT[0]: _INJECT[2], _AFTER[0]: _AFTER[2]}
