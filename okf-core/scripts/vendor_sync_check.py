"""vendor.lock의 files 해시를 재계산해 대조한다 — 불일치·부재 시 목록 출력 후 exit 1 (T-P1-4)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    lock = json.loads((root / "okf-core/vendor/vendor.lock").read_text(encoding="utf-8"))
    bad: list[str] = []
    total = 0
    for entry in lock.get("entries", []):
        for rel, want in entry.get("files", {}).items():
            total += 1
            path = root / rel
            if not path.is_file():
                bad.append(f"{rel}: 파일 없음")
                continue
            got = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if got != want:
                bad.append(f"{rel}: 해시 불일치 (lock={want}, 실측={got})")
    if bad:
        print("vendor sync check 실패:")
        for line in bad:
            print(f" - {line}")
        return 1
    print(f"vendor sync check 통과: {total}개 파일 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
