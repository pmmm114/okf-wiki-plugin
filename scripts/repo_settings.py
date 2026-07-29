"""GitHub repo 설정 점검·적용 — docs/branching.md의 서술과 실제 설정을 맞춘다.

브랜치 전략은 문서에만 있으면 지켜지지 않는다. 이 repo에서 실제로 그랬다: 문서는
"머지는 스쿼시 전용", "`core` 잡이 녹색이어야 머지"라고 적어 두었지만 GitHub 쪽에는
merge·rebase가 모두 열려 있었고 required status check 규칙 자체가 없었다. 즉 게이트가
시끄럽게 깨진 게 아니라 **처음부터 없었다.**

그래서 원하는 상태를 여기에 데이터로 두고, 언제든 드리프트를 확인할 수 있게 한다.

    python3 scripts/repo_settings.py            # 확인만(기본) — 다르면 종료코드 1
    python3 scripts/repo_settings.py --apply    # 머지 설정 적용 후 재확인

`gh` CLI의 인증을 그대로 쓴다(토큰을 따로 받거나 저장하지 않는다). 대상 repo는
`origin` 리모트에서 도출하므로 이름을 하드코딩하지 않는다.

**브랜치 룰셋과 보안 기능은 확인만 하고 고치지 않는다.** 룰셋 갱신은 PUT이라 전체를
교체하는데, 한 번 잘못 쓰면 main 보호가 통째로 풀린다. 보안 기능은 반대 방향의
이유다 — 켜는 순간 전체 히스토리 스캔과 push 거절이 걸려 남의 작업 흐름을 바꾸므로
사람이 알고 눌러야 한다. 둘 다 무엇이 어긋났는지 알려 주고 판단은 넘긴다.

종료코드: 0 일치 / 1 드리프트·검증 실패 / 2 실행 오류.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from branch_policy import DEFAULT_BRANCH  # 기본 브랜치 이름 단일원천

_ROOT = Path(__file__).resolve().parent.parent

# 머지 설정의 원하는 상태 — 값과 "왜"를 함께 둔다. 이유 없는 설정은 되돌려지기 쉽다.
DESIRED_SETTINGS: dict[str, tuple[object, str]] = {
    "allow_squash_merge": (True, "스쿼시 전용 — main을 PR 1건 = 커밋 1개 선형 이력으로 유지"),
    "allow_merge_commit": (False, "merge 커밋 금지 — 선형 이력이 깨진다"),
    "allow_rebase_merge": (False, "rebase 머지 금지 — 스쿼시 경계(유닛)가 사라진다"),
    "delete_branch_on_merge": (True, "머지 후 토픽 브랜치 삭제 — 단명 브랜치 원칙"),
    "squash_merge_commit_title": (
        "PR_TITLE",
        "스쿼시 제목을 PR 제목으로 고정 — 정책 게이트가 검사하는 것과 main에 남는 것을 같게 한다",
    ),
    "squash_merge_commit_message": (
        "COMMIT_MESSAGES",
        "스쿼시 본문은 커밋 메시지 — 왜 했는지가 main 이력에 남는다",
    ),
}

# 브랜치 룰셋에서 확인만 하는 항목(고치지 않는다).
REQUIRED_CHECK_CONTEXT = "core"
EXPECTED_RULE_TYPES = {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}

# Epic 통합 브랜치(epic/<n>) 룰셋 — 유닛 PR이 red로/직접 통합 브랜치에 들어가지 못하게.
# 룰셋 생성·수정은 다른 룰셋과 마찬가지로 사람이 한다(위 모듈 주석). 여기선 드리프트만 본다.
EPIC_REF_MARK = "epic/"  # 룰셋 conditions가 epic 통합 브랜치를 겨냥하는지 알아보는 표식
EPIC_EXPECTED_RULE_TYPES = {
    "deletion",
    "non_fast_forward",
    "pull_request",
    "required_status_checks",
}

# GitHub 쪽 보안 기능 — 확인만 한다(룰셋과 같은 이유로 고치지 않는다).
#
# CI 게이트(`.github/actions/security`)는 **PR을 막는** 계층이라 이미 push된 커밋은
# 손대지 못한다. push protection은 그보다 앞, push 시점에서 거절한다. 두 계층은
# 대체가 아니라 순서다 — 그래서 CI를 붙였다고 이쪽을 비워 두면 가장 이른 방어선이
# 없는 채로 남는다. public repo에서는 둘 다 무료다.
DESIRED_SECURITY: dict[str, tuple[str, str]] = {
    "secret_scanning": (
        "enabled",
        "커밋된 시크릿을 GitHub이 탐지 — 이미 들어간 것을 찾는 유일한 경로",
    ),
    "secret_scanning_push_protection": (
        "enabled",
        "시크릿이 든 push를 거절 — CI 게이트보다 앞선 방어선(CI는 이미 push된 뒤다)",
    ),
}

_REMOTE = re.compile(
    r"(?:git@[^:]+:|https?://[^/]+/)(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$"
)


def parse_remote(url: str) -> str | None:
    """리모트 URL에서 `owner/repo`를 뽑는다(ssh·https 둘 다)."""
    m = _REMOTE.search(url.strip())
    return f"{m.group('owner')}/{m.group('repo')}" if m else None


def settings_drift(current: dict) -> list[tuple[str, object, object, str]]:
    """(키, 현재값, 기대값, 이유) 목록. 비면 일치."""
    out = []
    for key, (want, why) in DESIRED_SETTINGS.items():
        got = current.get(key)
        if got != want:
            out.append((key, got, want, why))
    return out


def ruleset_drift(rules: list[dict]) -> list[str]:
    """브랜치 룰셋에서 어긋난 점(문장). 비면 일치."""
    problems = []
    by_type = {r.get("type"): r for r in rules}

    missing = EXPECTED_RULE_TYPES - set(by_type)
    if missing:
        problems.append(f"룰셋에 없는 규칙: {', '.join(sorted(missing))}")

    checks = by_type.get("required_status_checks", {}).get("parameters", {})
    contexts = [c.get("context") for c in checks.get("required_status_checks", [])]
    if REQUIRED_CHECK_CONTEXT not in contexts:
        problems.append(
            f"required status check에 {REQUIRED_CHECK_CONTEXT!r}가 없습니다(현재 {contexts}) — "
            f"CI가 red여도 머지됩니다"
        )

    pr = by_type.get("pull_request", {}).get("parameters", {})
    methods = pr.get("allowed_merge_methods")
    if methods is not None and sorted(methods) != ["squash"]:
        problems.append(f"룰셋 허용 머지 방식이 {methods}입니다 — ['squash']여야 합니다")

    if "required_linear_history" not in by_type:
        problems.append("required_linear_history 규칙이 없습니다 — 선형 이력이 강제되지 않습니다")

    return problems


def _targets_epic(ruleset: dict) -> bool:
    """룰셋 conditions가 epic 통합 브랜치(epic/<n>)를 겨냥하는가."""
    includes = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
    return any(EPIC_REF_MARK in str(inc) for inc in includes)


def _targets_default(ruleset: dict) -> bool:
    """룰셋 conditions가 **기본 브랜치**를 겨냥하는가.

    GitHub은 기본 브랜치를 `~DEFAULT_BRANCH` 토큰이나 `refs/heads/<이름>`으로 적는다.
    epic 브랜치를 겨냥하는 룰셋은 제외한다 — 겨냥 대상이 다르면 다른 룰셋이다.
    """
    if _targets_epic(ruleset):
        return False
    includes = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
    marks = ("~DEFAULT_BRANCH", "~ALL", f"refs/heads/{DEFAULT_BRANCH}", DEFAULT_BRANCH)
    return any(str(inc) in marks for inc in includes)


def default_ruleset_drift(rulesets: list[dict]) -> list[str]:
    """**기본 브랜치를 겨냥한 룰셋**의 어긋난 점(#303).

    예전에는 모든 브랜치 룰셋의 rules를 한 리스트로 합쳐 판정했다. 그러면 main 룰셋에
    required check가 없어도 다른 룰셋(예: epic/*)이 갖고 있으면 "일치"가 나온다 —
    실측으로 drift가 빈 리스트였다. 겨냥 대상이 다른 룰을 합치면 어느 브랜치가
    보호되는지 알 수 없으므로, epic 쪽과 같이 **대상 룰셋을 먼저 고른 뒤** 판정한다.
    """
    target = next((r for r in rulesets if _targets_default(r)), None)
    if target is None:
        return [
            f"{DEFAULT_BRANCH} 브랜치 룰셋이 없습니다 — "
            f"required check·PR 강제가 어디에도 걸려 있지 않습니다"
        ]
    return ruleset_drift(target.get("rules", []))


def epic_ruleset_drift(rulesets: list[dict]) -> list[str]:
    """Epic 통합 브랜치(epic/<n>) 룰셋의 어긋난 점. 룰셋이 아예 없으면 그 자체가 드리프트다."""
    epic = next((r for r in rulesets if _targets_epic(r)), None)
    if epic is None:
        return [
            "epic/* 브랜치 룰셋이 없습니다 — 유닛 PR이 red로·직접 통합 브랜치에 들어갈 수 있습니다"
        ]

    by_type = {r.get("type"): r for r in epic.get("rules", [])}
    problems: list[str] = []

    missing = EPIC_EXPECTED_RULE_TYPES - set(by_type)
    if missing:
        problems.append(f"epic/* 룰셋에 없는 규칙: {', '.join(sorted(missing))}")

    params = by_type.get("required_status_checks", {}).get("parameters", {})
    contexts = [c.get("context") for c in params.get("required_status_checks", [])]
    if REQUIRED_CHECK_CONTEXT not in contexts:
        problems.append(
            f"epic/* 룰셋 required check에 {REQUIRED_CHECK_CONTEXT!r}가 없습니다 — "
            f"유닛이 red로 통합 브랜치에 머지됩니다"
        )
    if not params.get("strict_required_status_checks_policy"):
        problems.append(
            "epic/* 룰셋에 strict(최신 동기) 정책이 없습니다 — 통합 브랜치가 드리프트합니다"
        )

    return problems


def security_drift(current: dict) -> list[str]:
    """GitHub 보안 기능에서 어긋난 점(문장). 비면 일치.

    `security_and_analysis`는 권한이나 repo 종류에 따라 통째로 빠질 수 있다. 그때
    "일치"로 넘기면 꺼져 있는 것과 구분되지 않으므로 못 읽었다는 사실을 그대로 말한다.
    """
    section = current.get("security_and_analysis")
    if not isinstance(section, dict):
        return ["security_and_analysis를 읽지 못했습니다 — 토큰 권한(admin)을 확인하세요"]

    problems = []
    for key, (want, why) in DESIRED_SECURITY.items():
        got = section.get(key, {}).get("status")
        if got != want:
            problems.append(f"{key}: {got!r} → {want!r} — {why}")
    return problems


# --- gh 호출 ------------------------------------------------------------------


def _gh(args: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return res.returncode, res.stdout, res.stderr


def _resolve_repo(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    res = subprocess.run(
        ["git", "-C", str(_ROOT), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_remote(res.stdout) if res.returncode == 0 else None


def _fetch(repo: str) -> tuple[dict, list[dict], list[dict]] | None:
    code, out, err = _gh(["api", f"repos/{repo}"])
    if code != 0:
        print(f"repo 설정을 읽지 못했습니다: {err.strip()}", file=sys.stderr)
        return None
    settings = json.loads(out)

    code, out, err = _gh(["api", f"repos/{repo}/rulesets"])
    if code != 0:
        print(f"룰셋 목록을 읽지 못했습니다: {err.strip()}", file=sys.stderr)
        return None

    rules: list[dict] = []
    rulesets: list[dict] = []
    for rs in json.loads(out):
        if rs.get("target") != "branch":
            continue
        code, detail, err = _gh(["api", f"repos/{repo}/rulesets/{rs['id']}"])
        if code != 0:
            print(f"룰셋 {rs['id']}를 읽지 못했습니다: {err.strip()}", file=sys.stderr)
            return None
        detail_obj = json.loads(detail)
        rules.extend(detail_obj.get("rules", []))
        rulesets.append(detail_obj)
    return settings, rules, rulesets


def _report(settings: dict, rules: list[dict], rulesets: list[dict]) -> int:
    drift = settings_drift(settings)
    rule_problems = default_ruleset_drift(rulesets)
    epic_problems = epic_ruleset_drift(rulesets)

    if drift:
        print("머지 설정 드리프트:")
        for key, got, want, why in drift:
            print(f"  - {key}: {got!r} → {want!r}")
            print(f"      {why}")
    else:
        print("머지 설정 일치")

    if rule_problems:
        print("\n브랜치 룰셋 드리프트(확인만 — 직접 고치지 않습니다):")
        for p in rule_problems:
            print(f"  - {p}")
        print("  고치려면 GitHub UI의 Rules → Rulesets에서 조정합니다.")
    else:
        print("브랜치 룰셋 일치")

    if epic_problems:
        print("\nEpic 통합 브랜치(epic/*) 룰셋 드리프트(확인만):")
        for p in epic_problems:
            print(f"  - {p}")
        print("  epic/* 룰셋을 GitHub UI의 Rules → Rulesets에서 만들거나 조정합니다.")
    else:
        print("Epic 통합 브랜치 룰셋 일치")

    sec_problems = security_drift(settings)
    if sec_problems:
        print("\n보안 기능 드리프트(확인만 — 직접 고치지 않습니다):")
        for p in sec_problems:
            print(f"  - {p}")
        print("  켜려면 GitHub UI의 Settings → Code security에서 조정하거나:")
        print(
            '    printf \'%s\' \'{"security_and_analysis":{"secret_scanning":{"status":'
            '"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}\' \\'
        )
        print("      | gh api --method PATCH repos/<owner>/<repo> --input -")
    else:
        print("보안 기능 일치")

    return 1 if (drift or rule_problems or epic_problems or sec_problems) else 0


def _apply(repo: str, drift: list[tuple[str, object, object, str]]) -> int:
    args = ["api", "--method", "PATCH", f"repos/{repo}"]
    for key, (want, _why) in DESIRED_SETTINGS.items():
        if isinstance(want, bool):
            args += ["-F", f"{key}={'true' if want else 'false'}"]
        else:
            args += ["-f", f"{key}={want}"]

    print("적용할 변경:")
    for key, got, want, _why in drift:
        print(f"  - {key}: {got!r} → {want!r}")

    code, _out, err = _gh(args)
    if code != 0:
        print(f"적용에 실패했습니다: {err.strip()}", file=sys.stderr)
        return 2
    print("적용했습니다. 재확인합니다.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="repo_settings", description="GitHub repo 머지 설정·브랜치 룰셋 점검(기본)과 적용"
    )
    ap.add_argument("--apply", action="store_true", help="머지 설정을 원하는 상태로 적용")
    ap.add_argument("--repo", help="대상 repo(owner/name). 기본값은 origin 리모트에서 도출")
    args = ap.parse_args(argv)

    if shutil.which("gh") is None:
        print("gh CLI가 필요합니다 — https://cli.github.com", file=sys.stderr)
        return 2

    repo = _resolve_repo(args.repo)
    if not repo:
        print(
            "대상 repo를 알 수 없습니다 — origin 리모트를 확인하거나 --repo를 주세요",
            file=sys.stderr,
        )
        return 2
    print(f"대상: {repo}\n")

    fetched = _fetch(repo)
    if fetched is None:
        return 2
    settings, rules, rulesets = fetched

    if args.apply:
        drift = settings_drift(settings)
        if not drift:
            print("머지 설정은 이미 일치합니다 — 적용할 것이 없습니다.\n")
        else:
            rc = _apply(repo, drift)
            if rc != 0:
                return rc
            fetched = _fetch(repo)
            if fetched is None:
                return 2
            settings, rules, rulesets = fetched

    return _report(settings, rules, rulesets)


if __name__ == "__main__":
    sys.exit(main())
