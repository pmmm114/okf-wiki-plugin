"""유닛 PR이 Epic 통합 브랜치(``epic/<n>``)로 머지될 때 도는 후처리 — 자동화(게이트 아님).

세 가지를 하며 각기 **fail-open**이다(실패해도 경고만, 다음 것을 계속하고 종료코드는 0):

1. **sub-issue 자동닫힘** — base가 기본 브랜치가 아니라 GitHub이 ``Closes``를 자동
   발동하지 않으므로, 머지된 PR 본문의 ``Closes #N``을 API로 닫아 1:1 미러를 복원한다.
2. **로스터 upsert** — epic_prs로 유닛 로스터를 재생성해 Epic 이슈의 마커 코멘트를 갱신.
3. **통합 PR 재실행 nudge** — 열린 ``epic/<n>`` → main 통합 PR의 core 런을 re-run한다.
   유닛이 닫히는 이 순간이 완결도가 바뀌는 순간이라, 완결성 게이트(U2)의 교차객체
   stale-red를 여기서 해소한다.

env(PR closed 이벤트): PR_MERGED · PR_BASE_REF · PR_BODY · GH_TOKEN.
종료코드: 항상 0 — 자동화는 CI를 red로 만들지 않는다.
"""

from __future__ import annotations

import os
import subprocess
import sys

import epic_prs
from branch_policy import DEFAULT_BRANCH, EPIC_PREFIX, _epic_number, closing_issues

# Epic 이슈에서 이 봇 코멘트를 다시 찾기 위한 숨은 마커(upsert 키).
ROSTER_MARKER = "<!-- okf-epic-roster -->"


def should_run(merged: bool, base_ref: str) -> bool:
    """머지됐고 base가 Epic 통합 브랜치일 때만 후처리한다."""
    return merged and base_ref.startswith(f"{EPIC_PREFIX}/")


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def close_sub_issue(body: str) -> int | None:
    """머지된 유닛 PR이 ``Closes``한 sub-issue를 닫는다(단수 — 게이트가 ≤1 보장)."""
    closes = closing_issues(body)
    if not closes:
        return None
    _gh("issue", "close", str(closes[0]), "--reason", "completed")
    return closes[0]


def roster_comment_body(prs: list[dict]) -> str:
    """마커 + 진행 헤딩 + 로스터 표. 마커로 upsert 대상 코멘트를 찾는다."""
    return f"{ROSTER_MARKER}\n\n### 구성 유닛 진행\n\n{epic_prs.roster_markdown(prs)}"


def _find_roster_comment(epic_num: int) -> str | None:
    jq = f'.[] | select(.body | contains("{ROSTER_MARKER}")) | .id'
    out = _gh("api", f"repos/{{owner}}/{{repo}}/issues/{epic_num}/comments", "--jq", jq)
    ids = [ln for ln in out.split() if ln.strip()]
    return ids[0] if ids else None


def upsert_roster(epic_num: int, base_ref: str) -> None:
    """유닛 로스터를 재생성해 Epic 이슈의 마커 코멘트를 갱신(없으면 생성)."""
    body = roster_comment_body(epic_prs.fetch_unit_prs(base_ref))
    cid = _find_roster_comment(epic_num)
    if cid:
        path = f"repos/{{owner}}/{{repo}}/issues/comments/{cid}"
        _gh("api", "-X", "PATCH", path, "-f", f"body={body}")
    else:
        _gh("api", f"repos/{{owner}}/{{repo}}/issues/{epic_num}/comments", "-f", f"body={body}")


def nudge_integration_pr(epic_num: int, base_ref: str) -> None:
    """열린 ``epic/<n>`` → main 통합 PR의 최신 core 런을 재실행(완결성 stale-red 해소)."""
    num = _gh(
        "pr",
        "list",
        "--base",
        DEFAULT_BRANCH,
        "--head",
        base_ref,
        "--state",
        "open",
        "--json",
        "number",
        "--jq",
        ".[0].number",
    ).strip()
    if not num:
        return
    rid = _gh(
        "run",
        "list",
        "--branch",
        base_ref,
        "--workflow",
        "ci.yml",
        "--limit",
        "1",
        "--json",
        "databaseId",
        "--jq",
        ".[0].databaseId",
    ).strip()
    if rid:
        _gh("run", "rerun", rid)


def main() -> int:
    merged = os.environ.get("PR_MERGED", "").lower() == "true"
    base_ref = os.environ.get("PR_BASE_REF", "")
    body = os.environ.get("PR_BODY", "")
    if not should_run(merged, base_ref):
        print("머지된 epic/* PR이 아닙니다 — 후처리 없음")
        return 0
    n = _epic_number(base_ref)
    if n is None:
        print(f"경고: epic 브랜치명에서 번호를 못 읽음: {base_ref}", file=sys.stderr)
        return 0

    steps = [
        ("sub-issue 자동닫힘", lambda: close_sub_issue(body)),
        ("로스터 upsert", lambda: upsert_roster(n, base_ref)),
        ("통합 PR 재실행 nudge", lambda: nudge_integration_pr(n, base_ref)),
    ]
    for label, fn in steps:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — fail-open, 자동화는 다음 단계를 막지 않는다
            print(f"경고: {label} 실패(무시): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
