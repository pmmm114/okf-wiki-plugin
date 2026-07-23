"""레거시 markdown 스테이징 리더 — 마이그레이션 전용 (U5, #134).

U1(#130) 이전 스테이징은 markdown ``inbox.md`` + 평문 ``ledger`` + jsonl
``journal.jsonl`` 3종 파일이었다. ``study migrate``가 이 옛 포맷을 읽어 ``study.db``
로 이관하려고, U1에서 걷어낸 파서를 여기 둔다(엔진 아닌 마이그레이션 셔틀).

두 위치가 대상이다: (a) pre-0.4 vault ``<vault>/.okf-study``, (b) 0.4.x 유저 스코프
``~/.claude/okf/study`` — 둘 다 같은 markdown 포맷이다.
"""

from __future__ import annotations

import re
from pathlib import Path

INBOX_NAME = "inbox.md"
LEDGER_NAME = "ledger"
JOURNAL_NAME = "journal.jsonl"

_SEP = " — "
_BULLET_RE = re.compile(r"^\* \*\*memory\*\*: (?P<body>.*) <!-- id:(?P<id>[0-9a-f]{12}) -->$")

_LEGACY_NAMES = (INBOX_NAME, LEDGER_NAME, JOURNAL_NAME)


def has_legacy(directory: str | Path) -> bool:
    """디렉토리에 옛 markdown 스테이징 파일이 하나라도 있는지."""
    base = Path(directory)
    return any((base / name).is_file() for name in _LEGACY_NAMES)


def read_candidates(directory: str | Path) -> list[dict]:
    """옛 ``inbox.md``를 [{id, date, snippet, source}]로 파싱한다(최신 우선)."""
    path = Path(directory) / INBOX_NAME
    if not path.is_file():
        return []
    out: list[dict] = []
    date = ""
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            date = line[3:].strip()
            continue
        match = _BULLET_RE.match(line)
        if match:
            head, sep, tail = match.group("body").rpartition(_SEP)
            snippet, source = (head, tail) if sep else (tail, "")
            out.append(
                {"id": match.group("id"), "date": date, "snippet": snippet, "source": source}
            )
    return out


def read_resolutions(directory: str | Path) -> list[tuple[str, str, str | None]]:
    """옛 평문 ``ledger``를 (id, status, ref) 목록으로 파싱한다."""
    path = Path(directory) / LEDGER_NAME
    if not path.is_file():
        return []
    out: list[tuple[str, str, str | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 2 and parts[1] in ("promoted", "discarded"):
            out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else None))
    return out


def remove_legacy(directory: str | Path) -> list[str]:
    """옛 markdown 파일들을 제거하고 지운 파일명을 반환한다(이관 후 소모)."""
    base = Path(directory)
    removed = []
    for name in _LEGACY_NAMES:
        path = base / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    return removed
