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
from branch_policy import (
    DEFAULT_BRANCH,
    EPIC_PREFIX,
    MULTI_UNIT_MARKER,
    _epic_number,
    closing_issues,
)

# Epic 이슈에서 이 봇 코멘트를 다시 찾기 위한 숨은 마커(upsert 키).
ROSTER_MARKER = "<!-- okf-epic-roster -->"


def should_run(merged: bool, base_ref: str) -> bool:
    """머지됐고 base가 Epic 통합 브랜치일 때만 후처리한다."""
    return merged and base_ref.startswith(f"{EPIC_PREFIX}/")


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def close_sub_issue(body: str) -> list[int]:
    """머지된 유닛 PR이 ``Closes``한 sub-issue를 **전량** 닫는다.

    "게이트가 ≤1을 보장한다"는 전제로 ``closes[0]``만 닫던 시절이 있었는데, 그 전제는
    거짓이다 — ``check_closing_issues``는 ``MULTI_UNIT_MARKER``가 있으면 2건 이상을
    통과시킨다. 마커를 정당하게 쓴 원자적 변경에서 첫 하나만 닫히고 나머지는 열린 채
    남아, 통합 PR이 `Epic 미완결`로 red가 됐다. 전제를 고치는 대신 전량을 닫는다.

    참조 판정은 ``branch_policy.closing_issues``와 **같은 함수**를 쓴다 — 게이트가 세는
    것과 후처리가 닫는 것이 갈리면, 게이트가 통과시킨 PR이 엉뚱한 이슈를 닫는다.
    """
    closes = closing_issues(body)
    if not closes:
        return []
    if len(closes) > 1:
        refs = ", ".join(f"#{n}" for n in closes)
        print(
            f"경고: 닫는 이슈가 {len(closes)}건입니다({refs}) — 전부 닫습니다. "
            f"유닛당 PR이 기본이므로 `{MULTI_UNIT_MARKER}` 마커의 의도를 확인하세요",
            file=sys.stderr,
        )
    for num in closes:
        _gh("issue", "close", str(num), "--reason", "completed")
    return closes


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
