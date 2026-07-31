"""query 배선 게이트 — 조회 표면이 소비 플로우에 실제로 꽂혀 있고, 게이트에는 안 꽂혀 있는지.

`test_census_wiring`과 같은 원리(#334): 이 repo에는 도구를 만들고 흐름에 꽂지 않아
죽은 채로 태어난 전례가 있다. 그래서 "okf query가 존재한다"가 아니라 **"소비 플로우가
query를 부른다"** 를 잠근다. 반대 방향도 같은 무게로 잠근다 — 승격 파이프라인은
query를 **소비하지 않는다**. 재료가 게이트 입력이 되는 순간 임계값 없는 자문이
조용히 판정으로 승격된다.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SKILL = PLUGIN / "skills" / "okf" / "SKILL.md"
QUERY_DOC = PLUGIN / "skills" / "okf" / "reference" / "QUERY.md"
PROMOTE = PLUGIN / "scripts" / "core" / "okf_promote.py"


def _section(text: str, heading: str, stop: str) -> str:
    start = text.index(heading)
    return text[start : text.index(stop, start)]


def test_skill_consumption_flow_calls_query():
    """소비 플로우가 query **실행을 지시**한다 — 낱말 언급이 아니라 명령 형태.

    다른 절에서 언급만 해도 통과하면 정작 호출 지시가 빠진 것을 놓친다(census 게이트가
    감도 실증에서 실제로 걸린 전례).
    """
    body = _section(SKILL.read_text(encoding="utf-8"), "## 4. 소비 플로우", "## 5.")
    assert "`okf query" in body, "소비 플로우가 query 실행을 지시하지 않는다"


def test_skill_points_to_query_reference():
    """레시피 정본(reference/QUERY.md)이 존재하고 SKILL이 그것을 가리킨다."""
    assert QUERY_DOC.is_file(), "reference/QUERY.md가 없다"
    assert "QUERY.md" in SKILL.read_text(encoding="utf-8"), "SKILL이 레시피 정본을 가리키지 않는다"


def test_promotion_pipeline_does_not_consume_query():
    """승격 게이트는 query를 소비하지 않는다 — 재료가 판정 입력이 되지 않게."""
    source = PROMOTE.read_text(encoding="utf-8")
    assert '"query"' not in source and "okf query" not in source, (
        "승격 파이프라인이 query를 소비한다(재료 ≠ 게이트 입력)"
    )


def test_query_doc_marks_judgment_boundary():
    """레시피 문서가 판정 금지 경계를 허용·금지 예시와 함께 명시한다."""
    text = QUERY_DOC.read_text(encoding="utf-8")
    assert "허용" in text and "금지" in text, "판정 금지 경계가 없다"
    assert "LIMIT" in text, "절단은 소비자의 LIMIT 몫이라는 관례가 없다"
