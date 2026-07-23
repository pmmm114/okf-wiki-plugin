"""다음 버전 제안 — 직전 태그 이후 랜딩된 커밋 타입에서 SemVer 범프를 도출한다 (#164).

main이 버전-중립(`0.0.0.dev0` 플레이스홀더)이라 "직전 실버전"의 단일 원천은 최신 태그다.
이 스크립트는 최신 태그를 base로, 그 이후 스쿼시 로그(PR 1건 = 한 줄)의 타입을 읽어
범프(major/minor/patch)를 계산해 **컷 때 매길 다음 버전**을 제안한다. 버전을 사전 결정하지
않고 실제 랜딩분에서 도출하므로 "이번이 patch냐 minor냐"를 미리 베팅하던 마찰을 없앤다
(docs/releasing.md §0.x 관례).

0.x(pre-1.0) 관례: `feat!`/`BREAKING`도 major가 아니라 **minor**로 승격한다
(bump-minor-pre-major). git 배관(태그 열거·시작 ref·로그 수집)은 `release_notes.py`와
공유한다(단일 원천). 무의존(stdlib)·오프라인·결정론이며 **제안일 뿐** — 실제 번호 확정은
릴리스 PR에서 사람이 한다. stdout은 버전 문자열만(스크립트용), 근거는 stderr로 낸다.
"""

from __future__ import annotations

import argparse
import re
import sys

# git 배관은 release_notes와 공유 — 태그 열거·기본 시작 ref·로그 수집을 재사용(단일 원천).
from release_notes import _default_from, _tags, collect

# 범프 신호를 주는 타입만 본다 — feat=minor, fix=patch. 나머지(docs/chore/…)=계약 무변화.
_TYPE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?:")

_NONE, _PATCH, _MINOR, _MAJOR = 0, 1, 2, 3
_RANK_NAME = {_PATCH: "patch", _MINOR: "minor", _MAJOR: "major"}


def _signal(ctype: str, breaking: bool, pre_1_0: bool) -> int:
    """한 커밋의 범프 등급. 0.x에선 파괴도 minor로 승격(bump-minor-pre-major)."""
    if breaking:
        return _MINOR if pre_1_0 else _MAJOR
    if ctype == "feat":
        return _MINOR
    if ctype == "fix":
        return _PATCH
    return _NONE


def decide_bump(lines: list[str], pre_1_0: bool) -> int:
    """로그 subject 라인들 → 최고 범프 등급(_NONE.._MAJOR). 최고 신호가 이긴다."""
    best = _NONE
    for raw in lines:
        line = raw.strip()
        m = _TYPE.match(line)
        if not m:
            continue
        breaking = bool(m.group("breaking")) or "BREAKING" in line
        best = max(best, _signal(m.group("type"), breaking, pre_1_0))
    return best


def parse_base(tag: str | None) -> tuple[int, int, int]:
    """릴리스 태그 `vX.Y.Z` → (X, Y, Z). 태그 없으면 (0, 0, 0)(첫 릴리스 전)."""
    if not tag:
        return (0, 0, 0)
    core = tag.lstrip("v").split("-", 1)[0].split("+", 1)[0]
    x, y, z = (core.split(".") + ["0", "0", "0"])[:3]
    return (int(x), int(y), int(z))


def bump_version(base: tuple[int, int, int], rank: int) -> tuple[int, int, int]:
    """base 버전에 범프 등급을 적용. _NONE이면 그대로 유지."""
    major, minor, patch = base
    if rank == _MAJOR:
        return (major + 1, 0, 0)
    if rank == _MINOR:
        return (major, minor + 1, 0)
    if rank == _PATCH:
        return (major, minor, patch + 1)
    return base


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="다음 버전 제안(직전 태그 + 랜딩 커밋 타입 → SemVer 범프, stdlib·무의존)"
    )
    p.add_argument("--from", dest="frm", help="시작 ref(배타적). 기본: 최신 태그")
    p.add_argument("--to", default="HEAD", help="끝 ref(포함). 기본: HEAD")
    args = p.parse_args(argv)

    tags = _tags()
    frm = args.frm or _default_from(args.to, tags)
    # base = 범위 시작 버전(= 직전 릴리스). frm이 실태그면 그 버전,
    # 아니면 최신 태그(없으면 첫 릴리스 전).
    base_tag = frm if frm in tags else (tags[-1] if tags else None)
    base = parse_base(base_tag)
    rank = decide_bump(collect(frm, args.to), pre_1_0=base[0] == 0)
    version = ".".join(map(str, bump_version(base, rank)))

    rng = f"{frm}..{args.to}" if frm else args.to
    cur = ".".join(map(str, base))
    if rank == _NONE:
        print(f"(범프 신호 없음: {rng} — feat/fix 없음, 현행 {cur} 유지 제안)", file=sys.stderr)
    else:
        print(f"{_RANK_NAME[rank]}: {cur} → {version}  ({rng})", file=sys.stderr)
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
