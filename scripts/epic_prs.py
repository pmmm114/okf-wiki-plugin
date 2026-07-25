"""Epic 통합 브랜치로 열린 유닛 PR 로스터 — 스쿼시로 main에서 사라질 유닛 경계를
마크다운 표로 박제한다.

Epic 통합 PR의 '구성 유닛' 섹션과 Epic 이슈 진행 코멘트의 소스다. base가
``epic/<n>``인 PR들을 모아 sub-issue·PR·상태·담당을 표로 낸다. 닫는 sub-issue 판정은
정책 게이트와 같은 ``branch_policy.closing_issues``를 쓴다(판정 단일 원천).

담당은 GitHub assignee까지만 낸다. 세션 단위 추적은 사설 텔레메트리 영역이라 공개
로스터에 세션 식별자를 남기지 않는다(참조 방향 정책).

종료코드: 0 정상 / 2 실행 오류.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from branch_policy import closing_issues

# PR 상태(gh state + isDraft) → 사람이 읽는 라벨.
_STATE_LABEL = {"MERGED": "머지됨", "OPEN": "열림", "DRAFT": "draft", "CLOSED": "닫힘"}

_HEADER = "| sub-issue | 유닛 PR | 요약 | 상태 | 담당 |\n| --- | --- | --- | --- | --- |"


def _cell(text: str) -> str:
    """표 셀용 — 파이프를 이스케이프해 열이 깨지지 않게 한다."""
    return (text or "").replace("|", "\\|").strip()


def _state(pr: dict) -> str:
    if pr.get("state") == "OPEN" and pr.get("isDraft"):
        return _STATE_LABEL["DRAFT"]
    return _STATE_LABEL.get(pr.get("state", ""), pr.get("state", "?"))


def _assignee(pr: dict) -> str:
    logins = [f"@{a['login']}" for a in pr.get("assignees") or [] if a.get("login")]
    return ", ".join(logins) if logins else "—"


def roster_row(pr: dict) -> str:
    """PR 하나 → 표 한 행. 닫는 sub-issue는 본문 Closes에서 뽑는다."""
    closes = closing_issues(pr.get("body") or "")
    cells = [
        f"#{closes[0]}" if closes else "—",
        f"#{pr.get('number')}",
        _cell(pr.get("title")),
        _state(pr),
        _assignee(pr),
    ]
    return "| " + " | ".join(cells) + " |"


def roster_markdown(prs: list[dict]) -> str:
    """유닛 PR 목록 → 마크다운 표(비었으면 안내 한 줄)."""
    if not prs:
        return "_이 Epic 통합 브랜치로 열린 유닛 PR이 없습니다._"
    rows = "\n".join(roster_row(pr) for pr in prs)
    return f"{_HEADER}\n{rows}"


def fetch_unit_prs(base_branch: str) -> list[dict]:
    """base가 ``base_branch``인 PR을 gh로 모은다(열림·닫힘·머지 전부)."""
    out = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--base",
            base_branch,
            "--state",
            "all",
            "--json",
            "number,title,state,isDraft,body,assignees,url",
            "--limit",
            "100",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out or "[]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="epic_prs", description="Epic 통합 브랜치 유닛 PR 로스터")
    ap.add_argument("--base", required=True, help="Epic 통합 브랜치 이름 (예: epic/189-study)")
    args = ap.parse_args(argv)
    try:
        prs = fetch_unit_prs(args.base)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"유닛 PR을 읽지 못했습니다: {e}", file=sys.stderr)
        return 2
    print(roster_markdown(prs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
