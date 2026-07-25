"""epic_prs 로스터 포맷 테스트 — gh 호출은 빼고 순수 포맷 계약을 고정한다."""

from __future__ import annotations

import epic_prs as ep


def _pr(**kw):
    base = {
        "number": 205,
        "title": "U1 승격 신호 리포트",
        "state": "MERGED",
        "isDraft": False,
        "body": "Closes #190",
        "assignees": [{"login": "kb"}],
    }
    base.update(kw)
    return base


def test_roster_row_maps_fields():
    row = ep.roster_row(_pr())
    assert "#190" in row  # 닫는 sub-issue (Closes에서)
    assert "#205" in row  # PR 번호
    assert "머지됨" in row
    assert "@kb" in row


def test_state_labels():
    assert ep._state(_pr(state="MERGED")) == "머지됨"
    assert ep._state(_pr(state="OPEN", isDraft=False)) == "열림"
    assert ep._state(_pr(state="OPEN", isDraft=True)) == "draft"
    assert ep._state(_pr(state="CLOSED")) == "닫힘"


def test_no_assignee_shows_dash():
    assert "| — |" in ep.roster_row(_pr(assignees=[])) + "|"


def test_no_closing_issue_shows_dash():
    """Refs만 있으면 닫는 sub-issue가 없다 — 첫 열은 —."""
    assert ep.roster_row(_pr(body="Refs #190")).startswith("| — |")


def test_pipe_in_title_escaped():
    assert "a \\| b" in ep.roster_row(_pr(title="a | b"))


def test_roster_markdown_has_header_and_rows():
    md = ep.roster_markdown([_pr(), _pr(number=206, body="Closes #191")])
    assert "| sub-issue | 유닛 PR |" in md
    assert md.count("\n") >= 3  # 헤더 2줄 + 2 행


def test_roster_markdown_empty():
    assert "없습니다" in ep.roster_markdown([])


def test_reuses_branch_policy_closing_source():
    """닫는 이슈 판정은 정책 게이트와 같은 함수 — 규칙이 갈리지 않게."""
    assert ep.closing_issues is __import__("branch_policy").closing_issues
