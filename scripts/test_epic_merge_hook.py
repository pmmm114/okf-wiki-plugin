"""epic_merge_hook 후처리 테스트 — gh 호출은 monkeypatch로 대체한다."""

from __future__ import annotations

import branch_policy as bp
import epic_merge_hook as emh


def test_should_run():
    assert emh.should_run(True, "epic/189-study")
    assert not emh.should_run(False, "epic/189-study")  # 안 머지됨
    assert not emh.should_run(True, "main")  # epic 아님
    assert not emh.should_run(True, "feat/x")


def test_roster_comment_body_has_marker_and_table():
    prs = [
        {"number": 205, "title": "U1", "state": "MERGED", "body": "Closes #190", "assignees": []}
    ]
    body = emh.roster_comment_body(prs)
    assert emh.ROSTER_MARKER in body
    assert "구성 유닛" in body
    assert "#190" in body and "#205" in body


def test_close_sub_issue_closes_the_one(monkeypatch):
    calls = []
    monkeypatch.setattr(emh, "_gh", lambda *a: calls.append(a) or "")
    assert emh.close_sub_issue("Closes #190") == [190]
    assert calls and "close" in calls[0]


def test_close_sub_issue_noop_without_closes(monkeypatch):
    def boom(*a):
        raise AssertionError("Closes 없는데 gh를 불렀다")

    monkeypatch.setattr(emh, "_gh", boom)
    assert emh.close_sub_issue("Refs #190") == []


def test_main_skips_non_merged(monkeypatch):
    monkeypatch.setenv("PR_MERGED", "false")
    monkeypatch.setenv("PR_BASE_REF", "epic/189-study")

    def boom(*a):
        raise AssertionError("대상 아닌데 gh를 불렀다")

    monkeypatch.setattr(emh, "_gh", boom)
    assert emh.main() == 0


def test_main_runs_all_three_in_order(monkeypatch):
    calls = []
    monkeypatch.setenv("PR_MERGED", "true")
    monkeypatch.setenv("PR_BASE_REF", "epic/189-study")
    monkeypatch.setenv("PR_BODY", "Closes #190")
    monkeypatch.setattr(emh, "close_sub_issue", lambda body: calls.append("close"))
    monkeypatch.setattr(emh, "upsert_roster", lambda n, b: calls.append(("roster", n, b)))
    monkeypatch.setattr(emh, "nudge_integration_pr", lambda n, b: calls.append(("nudge", n, b)))
    assert emh.main() == 0
    assert calls == ["close", ("roster", 189, "epic/189-study"), ("nudge", 189, "epic/189-study")]


def test_main_fail_open_continues(monkeypatch):
    """한 단계가 실패해도 나머지를 계속하고 0을 반환한다(자동화는 CI를 막지 않음)."""
    ran = []
    monkeypatch.setenv("PR_MERGED", "true")
    monkeypatch.setenv("PR_BASE_REF", "epic/189-study")
    monkeypatch.setenv("PR_BODY", "Closes #190")

    def boom(*a):
        raise RuntimeError("API 다운")

    monkeypatch.setattr(emh, "close_sub_issue", boom)
    monkeypatch.setattr(emh, "upsert_roster", lambda n, b: ran.append("roster"))
    monkeypatch.setattr(emh, "nudge_integration_pr", lambda n, b: ran.append("nudge"))
    assert emh.main() == 0
    assert ran == ["roster", "nudge"]  # 첫 단계 실패 후에도 계속


# --- 닫는 대상은 전량이고, 판정면은 branch_policy와 같다 (#302) ----------------


def test_close_sub_issue_uses_real_template_without_false_target(monkeypatch):
    """**실파일** PR 템플릿 + 진짜 `Closes` 하나 → 닫는 대상은 그 하나뿐이어야 한다.

    템플릿 주석의 예시 번호를 닫으면 무관한 실이슈가 completed로 닫힌다.
    """
    calls = []
    monkeypatch.setattr(emh, "_gh", lambda *a: calls.append(a) or "")
    body = bp.PR_TEMPLATE.read_text(encoding="utf-8") + "\n\nCloses #250\n"
    assert emh.close_sub_issue(body) == [250]
    assert [a[2] for a in calls] == ["250"]


def test_close_sub_issue_closes_all_and_warns(monkeypatch, capsys):
    """마커로 게이트를 통과한 다중 닫힘에서 첫 하나만 닫으면 나머지가 무음으로 남는다."""
    calls = []
    monkeypatch.setattr(emh, "_gh", lambda *a: calls.append(a) or "")
    assert emh.close_sub_issue("Closes #190 Closes #191") == [190, 191]
    assert [a[2] for a in calls] == ["190", "191"]
    assert "2건" in capsys.readouterr().err
