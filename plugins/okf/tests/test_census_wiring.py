"""census 배선 게이트 — 관측이 판정 지점에 실제로 꽂혀 있고, 게이트에는 안 꽂혀 있는지.

이 repo에는 **도구를 만들고 흐름에 꽂지 않아 죽은 채로 태어난** 전례가 있다. 층 축에
의존하는 자문 도구들이 그 축이 비어 있는 동안 아무도 부르지 않는 상태로 남았다. 그래서
"census가 존재한다"가 아니라 **"배치·분류를 판정하는 문서가 census를 부른다"** 를 잠근다.

반대 방향도 같은 무게로 잠근다: 승격 파이프라인은 census를 **소비하지 않는다**. 관측이
게이트 입력이 되는 순간 (1) 관측 누락·오탐이 승격의 정합성을 흔들고 (2) 임계값 없는
자문이라는 성격이 조용히 판정으로 승격된다. 탐색 계약이 같은 이유로 지키는 불변식이다.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SKILL = PLUGIN / "skills" / "okf" / "SKILL.md"
STUDY_CMD = PLUGIN / "commands" / "study.md"
PROMOTE = PLUGIN / "scripts" / "core" / "okf_promote.py"
CENSUS_CALL = '"${CLAUDE_PLUGIN_ROOT}/bin/okf" census'


def _section(text: str, heading: str, stop: str) -> str:
    start = text.index(heading)
    return text[start : text.index(stop, start)]


def test_skill_placement_flow_calls_census():
    """배치·분류를 판정하는 절이 관측을 먼저 확보하도록 **실행을 지시**한다.

    단순히 "census"라는 낱말이 있는지 보면 안 된다 — 다른 항에서 관측을 언급만 해도
    통과해 버려, 정작 호출 지시가 빠진 것을 놓친다(이 게이트를 감도 실증하다 실제로
    걸렸다). 명령 형태를 요구해야 "부른다"가 검사된다.
    """
    body = _section(SKILL.read_text(encoding="utf-8"), "## 2. 작성 플로우", "## 3.")
    assert "`okf census" in body, "작성 플로우가 census 실행을 지시하지 않는다"


def test_study_command_calls_census_before_conceptualizing():
    """승격 커맨드의 개념화 단계가 셔틀 경유로 census를 부른다."""
    body = _section(STUDY_CMD.read_text(encoding="utf-8"), "4. **개념화", "5. **로그")
    assert CENSUS_CALL in body, f"개념화 단계에 {CENSUS_CALL} 호출이 없다"


def test_census_call_uses_engine_shuttle():
    """엔진 호출은 셔틀 경유 — 커맨드 문서의 census 호출이 규약 형태인지."""
    for doc in (SKILL, STUDY_CMD):
        text = doc.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if "census" not in line or "okf-py" in line:
                continue
            if "${CLAUDE_PLUGIN_ROOT}" in line:
                assert CENSUS_CALL in line, f"{doc.name}:{line_no} 셔틀 경유가 아님: {line}"


def test_promotion_pipeline_does_not_consume_census():
    """승격 게이트는 관측을 소비하지 않는다 — 자문이 판정으로 승격되지 않게."""
    source = PROMOTE.read_text(encoding="utf-8")
    assert "census" not in source, "승격 파이프라인이 census를 소비한다(관측 ≠ 게이트 입력)"


def test_skill_does_not_prescribe_vocabulary_growth():
    """'성격이 갈리면 신설'류 처방이 없는지 — 어휘를 늘리기만 하는 논리를 막는다.

    관측은 정직하게 분포를 보여주지만, 그 위에 얹는 지시문이 "같은 값 안에서 성격이
    갈리면 새 값을 만들라"면 저작할 때마다 어휘가 늘고 다음 저작자는 더 갈린 어휘를
    보고 또 늘린다. 신설을 금지하는 대신 **비용**(경계 진술)을 얹는 것이 이 문서의 선택.
    """
    body = _section(SKILL.read_text(encoding="utf-8"), "## 2. 작성 플로우", "## 3.")
    assert "재사용이 기본" in body
    assert "경계를" in body, "신설에 경계 진술 비용이 붙어 있지 않다"
