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

**브랜치 룰셋은 확인만 하고 고치지 않는다.** 룰셋 갱신은 PUT이라 전체를 교체하는데,
한 번 잘못 쓰면 main 보호가 통째로 풀린다. 무엇이 어긋났는지 알려 주고 사람이
판단하게 두는 편이 안전하다.

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


def _fetch(repo: str) -> tuple[dict, list[dict]] | None:
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
    for rs in json.loads(out):
        if rs.get("target") != "branch":
            continue
        code, detail, err = _gh(["api", f"repos/{repo}/rulesets/{rs['id']}"])
        if code != 0:
            print(f"룰셋 {rs['id']}를 읽지 못했습니다: {err.strip()}", file=sys.stderr)
            return None
        rules.extend(json.loads(detail).get("rules", []))
    return settings, rules


def _report(settings: dict, rules: list[dict]) -> int:
    drift = settings_drift(settings)
    rule_problems = ruleset_drift(rules)

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

    return 1 if (drift or rule_problems) else 0


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
    settings, rules = fetched

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
            settings, rules = fetched

    return _report(settings, rules)


if __name__ == "__main__":
    sys.exit(main())
