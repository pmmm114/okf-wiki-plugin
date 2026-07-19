"""log.md 조작 (T-P2-5).

§7 구조를 유지하며 항목을 추가한다: 날짜 그룹(`## YYYY-MM-DD`, 최신 우선) 아래
``* **<종류>**: <메시지>`` 불릿. 파일이 없으면 제목 헤딩과 함께 생성하고, 같은
날짜 그룹이 이미 맨 위에 있으면 그 그룹에 불릿을 추가한다. 출력은 항상
§9(OKF9.3 — ISO 날짜 헤딩)를 통과해야 한다.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

LOG_NAME = "log.md"
DEFAULT_TITLE = "# Directory Update Log"


def append_entry(
    directory: str | Path,
    message: str,
    kind: str = "Update",
    date: str | None = None,
) -> Path:
    """directory/log.md에 항목을 추가하고 파일 경로를 반환한다."""
    directory = Path(directory)
    path = directory / LOG_NAME
    stamp = date or datetime.date.today().isoformat()
    bullet = f"* **{kind}**: {message}"

    if not path.is_file():
        path.write_text(f"{DEFAULT_TITLE}\n\n## {stamp}\n{bullet}\n", encoding="utf-8")
        return path

    lines = path.read_text(encoding="utf-8").split("\n")
    heading = f"## {stamp}"
    for i, line in enumerate(lines):
        if line.strip() == heading:
            lines.insert(i + 1, bullet)  # 기존 최신 그룹에 합류
            break
        if line.startswith("## "):
            lines[i:i] = [heading, bullet, ""]  # 더 오래된 그룹 앞에 새 그룹(최신 우선)
            break
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", heading, bullet]
    text = "\n".join(lines)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf log", description="log.md 항목 추가(§7)")
    sub = ap.add_subparsers(dest="command", required=True)
    append = sub.add_parser("append", help="디렉터리의 log.md에 항목 추가")
    append.add_argument("directory", help="대상 디렉터리(log.md 위치)")
    append.add_argument("-m", "--message", required=True, help="기록할 메시지")
    append.add_argument("--kind", default="Update", help="선두 볼드 종류(기본 Update)")
    args = ap.parse_args(argv)

    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"오류: 디렉터리가 아님: {directory}")
        return 2
    print(append_entry(directory, args.message, kind=args.kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
