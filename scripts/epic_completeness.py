"""Epic 통합 PR 완결성 게이트 — `epic/<n>` → main PR은 그 Epic의 모든 sub-issue가
닫히기 전에는 머지되지 못하게 한다.

완결 신호는 Epic 이슈의 ``sub_issues_summary.completed == total``(GitHub API)이다.
PR 로스터(epic_prs)가 보는 PR 상태가 아니라 **이슈 완결도**가 "Epic 완료"의 정의라
여기가 권위 신호다.

통합 PR **하나에만** 도는 core 스텝이라 fail-closed를 감당할 수 있다: API가 흔들리면
통과가 아니라 재시도 후 실행 오류(2)로 세운다. 드문 통합 PR 하나만 영향받으므로
"조용히 통과"보다 "재실행하세요"가 옳다.

env(PR 이벤트)에서 읽는다 — PR_BASE_REF·PR_HEAD_REF. 대상(base=main·head=epic/<n>)이
아니면 통과한다.

종료코드: 0 통과(대상 아님·완결) / 1 미완결 / 2 실행 오류(Epic 번호 불명·API 소진).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# Epic 통합 브랜치 이름 형식은 branch_policy가 정본(_EPIC_REF)이다. 여기선 번호만
# 뽑아 쓰므로 그 함수를 재사용해 형식이 갈리지 않게 한다.
from branch_policy import DEFAULT_BRANCH, EPIC_PREFIX, _epic_number

_RETRY_ATTEMPTS = 3
_RETRY_DELAY_S = 2.0


def sub_issue_summary(issue: int) -> dict:
    """Epic 이슈의 ``sub_issues_summary``(completed·total)를 gh로 읽는다."""
    out = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{issue}", "--jq", ".sub_issues_summary"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out or "{}")


def _fetch_with_retry(issue: int, *, sleep=time.sleep) -> dict:
    """일시적 API 실패는 재시도한다. 소진하면 마지막 예외를 올린다(fail-closed)."""
    last: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return sub_issue_summary(issue)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            last = e
            if attempt + 1 < _RETRY_ATTEMPTS:
                sleep(_RETRY_DELAY_S)
    raise last if last else RuntimeError("완결도 확인 실패")


def check(base_ref: str, head_ref: str) -> tuple[int, str]:
    """(종료코드, 메시지). 통합 PR(epic/<n> → main)에만 완결성을 강제한다."""
    if not (head_ref.startswith(f"{EPIC_PREFIX}/") and base_ref == DEFAULT_BRANCH):
        return 0, "통합 PR이 아닙니다 — 완결성 대상 아님(통과)"

    n = _epic_number(head_ref)
    if n is None:
        return 2, f"epic 브랜치명에서 Epic 번호를 읽지 못했습니다: `{head_ref}`"

    try:
        summary = _fetch_with_retry(n)
    except Exception as e:  # noqa: BLE001 — 어떤 API 실패든 fail-closed로 세운다
        return 2, f"Epic #{n} 완결도를 확인하지 못했습니다(재시도 소진) — 재실행하세요: {e}"

    total = summary.get("total", 0)
    completed = summary.get("completed", 0)
    if not total:
        return 1, f"Epic #{n}에 sub-issue가 없습니다 — 통합 브랜치가 맞습니까?"
    if completed >= total:
        return 0, f"Epic #{n} 완결: {completed}/{total}"
    return (
        1,
        f"Epic #{n} 미완결: {completed}/{total} — 모든 sub-issue가 닫혀야 통합 PR을 머지합니다",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="epic_completeness", description="Epic 통합 PR 완결성 게이트")
    ap.add_argument("--check-pr", action="store_true", help="PR 컨텍스트를 env에서 읽어 판정")
    ap.parse_args(argv)

    base_ref = os.environ.get("PR_BASE_REF", "")
    head_ref = os.environ.get("PR_HEAD_REF", "")
    code, msg = check(base_ref, head_ref)
    print(msg, file=sys.stderr if code else sys.stdout)
    if code == 1:
        print("규약: docs/branching.md §Epic과 유닛 분해", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
