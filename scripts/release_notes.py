"""릴리스 노트 생성 — 태그 범위의 conventional-commit 로그를 타입별로 묶어 마크다운으로 낸다.

스쿼시 트렁크라 태그 사이 `main` 로그가 **PR 1건 = 한 줄**(`type(scope): 제목 (#NN)`)이다.
이 스크립트는 그 로그를 파싱해 **추가(feat)/수정(fix)/문서(docs)/기타**로 그룹핑한다 —
`docs/releasing.md`가 수기로 하던 `git log --pretty` + prefix 그룹핑의 자동화다. 출력은
CHANGELOG 섹션과 GitHub Release 본문 양쪽에 그대로 쓴다.

무의존(stdlib만)·오프라인·결정론이다 — 같은 로그 입력이면 같은 출력. `chore`·`release`
(버전 범프·릴리스 커밋)는 릴리스 노트에서 기본 제외한다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# type prefix → 출력 카테고리(선언 순서가 곧 출력 순서)
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("추가", ("feat",)),
    ("수정", ("fix",)),
    ("문서", ("docs",)),
]
OTHER = "기타"
# 릴리스 노트에서 기본 제외 — 버전 범프·릴리스 커밋·순수 잡음(사용자 무관)
EXCLUDED = {"chore", "release", "ci", "build", "test", "style"}

_PREFIX = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)


def _category(ctype: str) -> str | None:
    """type prefix를 카테고리로 매핑. 제외 대상은 None, 미지의 type은 기타로."""
    for name, types in CATEGORIES:
        if ctype in types:
            return name
    if ctype in EXCLUDED:
        return None
    return OTHER


def _clean(scope: str | None, subject: str, breaking: str | None) -> str:
    body = f"**{scope}**: {subject}" if scope else subject
    return f"⚠️ {body}" if breaking else body


def parse_log(lines: list[str], include_excluded: bool = False) -> dict[str, list[str]]:
    """로그 subject 라인들 → {카테고리: [항목, ...]} (등장 순서 보존)."""
    groups: dict[str, list[str]] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _PREFIX.match(line)
        if not m:
            # conventional 형식이 아니면 그대로 기타로 — 아무것도 조용히 사라지지 않게
            groups.setdefault(OTHER, []).append(line)
            continue
        cat = _category(m.group("type"))
        if cat is None:
            if not include_excluded:
                continue
            cat = OTHER
        groups.setdefault(cat, []).append(
            _clean(m.group("scope"), m.group("subject"), m.group("breaking"))
        )
    return groups


def render(groups: dict[str, list[str]], heading: str = "###") -> str:
    """그룹을 마크다운으로. 비어 있는 카테고리는 생략, 카테고리 순서는 고정."""
    order = [name for name, _ in CATEGORIES] + [OTHER]
    out: list[str] = []
    for name in order:
        items = groups.get(name)
        if not items:
            continue
        out.append(f"{heading} {name}")
        out.append("")
        out.extend(f"- {it}" for it in items)
        out.append("")
    return "\n".join(out).rstrip() + "\n" if out else ""


def _run(args: list[str]) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _tags() -> list[str]:
    out = _run(["tag", "--list", "v*", "--sort=version:refname"])
    return [t for t in out.splitlines() if t.strip()]


def _default_from(to: str, tags: list[str]) -> str | None:
    """기본 시작 ref — `to`가 태그면 그 직전 태그, 아니면(HEAD 등) 가장 최신 태그."""
    if to in tags:
        i = tags.index(to)
        return tags[i - 1] if i > 0 else None
    return tags[-1] if tags else None


def collect(frm: str | None, to: str) -> list[str]:
    rng = f"{frm}..{to}" if frm else to
    return _run(["log", rng, "--no-merges", "--pretty=format:%s"]).splitlines()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="릴리스 노트 생성(태그 범위 → 타입별 마크다운, stdlib·무의존)"
    )
    p.add_argument("--from", dest="frm", help="시작 ref(배타적). 기본: 직전 태그")
    p.add_argument("--to", default="HEAD", help="끝 ref(포함). 기본: HEAD")
    p.add_argument("--all", action="store_true", help="chore/release 등 제외 타입도 기타로 포함")
    p.add_argument("--heading", default="###", help="섹션 헤딩 접두(기본: ###)")
    args = p.parse_args(argv)

    frm = args.frm or _default_from(args.to, _tags())
    groups = parse_log(collect(frm, args.to), include_excluded=args.all)
    text = render(groups, heading=args.heading)
    if not text:
        rng = f"{frm}..{args.to}" if frm else args.to
        print(f"(변경 없음: {rng})", file=sys.stderr)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
