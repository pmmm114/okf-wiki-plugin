"""vendor.lock ↔ ``okf-core/vendor/`` **양방향** 대조 (T-P1-4, #303).

CLAUDE.md는 "vendor는 업스트림 바이트 그대로"를 이 게이트에 위임한다. 그런데 lock에
**적힌 파일만** 해시 대조하면 그 위임이 성립하지 않는다 — 검사 범위를 검사 대상이
정하는 꼴이라, lock을 줄이면 게이트도 같이 줄어든다. 실측(변경 전):

    lock의 entries[*].files를 비움     → "vendor sync check 통과: 0개 파일 일치"  exit 0
    vendor/에 미등록 파일을 넣음        → "vendor sync check 통과: 2개 파일 일치"  exit 0

그래서 두 방향을 함께 본다.

1. **정방향** — lock에 적힌 파일이 실존하고 해시가 맞는가(기존).
2. **역방향** — ``vendor/``의 파일이 전부 lock에 등록됐는가. 등록 면제는 lock 자신 ·
   ``patches/``(수정은 여기 패치로 — CLAUDE.md) · 라이선스 전문뿐이다.
3. **총계 0은 실행 오류**(exit 2) — 정상적으로 대조할 것이 없는 상태는 없다.

종료코드: 0 일치 / 1 불일치 / 2 실행 오류(대조 대상 없음·vendor 트리 없음).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

VENDOR_REL = "okf-core/vendor"
LOCK_REL = f"{VENDOR_REL}/vendor.lock"

# lock 등록이 면제되는 것 — 업스트림 바이트가 아니거나(패치 작업 공간) 우리가 함께
# 보관하는 부속물(라이선스 전문)이다. 이 목록을 늘리는 것이 곧 게이트를 줄이는 것이라
# 여기 명시적으로 둔다.
_EXEMPT_PREFIXES = (f"{VENDOR_REL}/patches/",)
_EXEMPT_PATHS = (LOCK_REL,)


def _is_exempt(rel: str) -> bool:
    if rel in _EXEMPT_PATHS or rel.startswith(_EXEMPT_PREFIXES):
        return True
    return Path(rel).name.startswith("LICENSE")


def _vendor_files(root: Path) -> list[str]:
    """``vendor/`` 아래 **git 추적** 파일의 repo 상대 경로(정렬).

    파일시스템 순회가 아니라 git에게 묻는다. 이 게이트가 지키는 것은 "커밋된 vendor
    바이트가 lock과 일치하는가"이고, 추적되지 않은 것은 아직 그 대상이 아니다.
    순회로 하면 오라클을 한 번 돌리기만 해도 생기는 ``__pycache__``가 미등록으로
    잡혀 게이트가 붉어진다(실측) — 무결성과 무관한 오탐이다.

    git이 없거나 실패하면 순회로 폴백한다 — 역방향 검사를 조용히 끄지 않기 위함이다
    (그때 ``__pycache__`` 오탐이 날 수 있지만, 검사가 사라지는 쪽보다 낫다).
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", VENDOR_REL],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return sorted(ln for ln in proc.stdout.split("\n") if ln.strip())
    vendor = root / VENDOR_REL
    return sorted(p.relative_to(root).as_posix() for p in vendor.rglob("*") if p.is_file())


def check(root: Path) -> tuple[list[str], int]:
    """(문제 목록, 대조한 파일 수). 문제가 비고 수가 0보다 크면 통과."""
    lock_path = root / LOCK_REL
    if not lock_path.is_file():
        return [f"{LOCK_REL}: lock 파일 없음"], 0

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    registered: dict[str, str] = {}
    for entry in lock.get("entries", []):
        registered.update(entry.get("files", {}) or {})

    problems: list[str] = []
    for rel, want in sorted(registered.items()):
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: 파일 없음")
            continue
        got = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            problems.append(f"{rel}: 해시 불일치 (lock={want}, 실측={got})")

    # 역방향 — 등록되지 않은 vendor 파일은 "업스트림 바이트 그대로"의 사각지대다.
    for rel in _vendor_files(root):
        if rel not in registered and not _is_exempt(rel):
            problems.append(f"{rel}: lock 미등록 (vendor 반입은 vendor.lock에 함께 기록한다)")

    return problems, len(registered)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    if not (root / VENDOR_REL).is_dir():
        print(f"vendor sync check 실행 오류: {VENDOR_REL} 디렉터리 없음", file=sys.stderr)
        return 2

    problems, total = check(root)
    if problems:
        print("vendor sync check 실패:")
        for line in problems:
            print(f" - {line}")
        return 1
    if total == 0:
        # "0개 일치"는 통과가 아니라 검사가 실종된 상태다 — 고장을 정상 문장으로
        # 보고하지 않는다(#303).
        print(
            "vendor sync check 실행 오류: lock에 등록된 파일이 0개 — 대조할 대상이 없습니다",
            file=sys.stderr,
        )
        return 2
    print(f"vendor sync check 통과: {total}개 파일 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
