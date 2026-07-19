"""study inbox·resolved 원장 라이브러리 (S3, #75).

inbox(`.okf-study/inbox.md`)는 번들 밖 후보 큐로, log.md식 형식만 차용한다
(컨포먼트일 필요 없음):

    # Study Inbox

    ## YYYY-MM-DD
    * **memory**: <스니펫> — <출처> <!-- id:<hash12> -->

``id``는 스니펫 **내용 해시**(sha256) 앞 12자로, 선택 승격·폐기·중복 판정의
안정 키다. resolved 원장(`.okf-study/ledger`)은 promoted/discarded된 id를 기록해
동일 스니펫의 재적재를 막는다(다르게 고쳐 쓴 메모는 해시가 달라 새 항목).
"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path

STUDY_DIR = ".okf-study"
INBOX_NAME = "inbox.md"
LEDGER_NAME = "ledger"
INBOX_TITLE = "# Study Inbox"
_ID_LEN = 12
_SEP = " — "
_BULLET_RE = re.compile(r"^\* \*\*memory\*\*: (?P<body>.*) <!-- id:(?P<id>[0-9a-f]{12}) -->$")


def content_hash(snippet: str) -> str:
    """스니펫 내용 해시(sha256 hex 전체)."""
    return hashlib.sha256(_sanitize(snippet).encode("utf-8")).hexdigest()


def _sanitize(text: str) -> str:
    """개행·연속 공백을 단일 공백으로 정규화하고 양끝을 다듬는다."""
    return " ".join(str(text).split())


def _inbox_path(project: str | Path) -> Path:
    return Path(project) / STUDY_DIR / INBOX_NAME


def _ledger_path(project: str | Path) -> Path:
    return Path(project) / STUDY_DIR / LEDGER_NAME


def _today() -> str:
    return datetime.date.today().isoformat()


# --- inbox ----------------------------------------------------------------


def _parse(text: str) -> list[dict]:
    """inbox 텍스트를 날짜 그룹 목록으로 파싱한다."""
    groups: list[dict] = []
    current: dict | None = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = {"date": line[3:].strip(), "bullets": []}
            groups.append(current)
        elif current is not None:
            match = _BULLET_RE.match(line)
            if match:
                head, sep, tail = match.group("body").rpartition(_SEP)
                snippet, source = (head, tail) if sep else (tail, "")
                current["bullets"].append(
                    {"id": match.group("id"), "snippet": snippet, "source": source}
                )
    return groups


def _serialize(groups: list[dict]) -> str:
    out = [INBOX_TITLE, ""]
    for group in groups:
        out.append(f"## {group['date']}")
        for bullet in group["bullets"]:
            out.append(
                f"* **memory**: {bullet['snippet']}{_SEP}{bullet['source']} "
                f"<!-- id:{bullet['id']} -->"
            )
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _load(project: str | Path) -> list[dict]:
    path = _inbox_path(project)
    return _parse(path.read_text(encoding="utf-8")) if path.is_file() else []


def _has_id(groups: list[dict], ident: str) -> bool:
    return any(b["id"] == ident for g in groups for b in g["bullets"])


def append(project: str | Path, snippet: str, source: str, date: str | None = None) -> str:
    """후보 스니펫을 inbox에 적재하고 id를 반환한다. 동일 id는 재적재하지 않는다."""
    snippet = _sanitize(snippet)
    source = _sanitize(source)
    ident = content_hash(snippet)[:_ID_LEN]
    groups = _load(project)
    if _has_id(groups, ident):
        return ident
    stamp = date or _today()
    bullet = {"id": ident, "snippet": snippet, "source": source}
    if groups and groups[0]["date"] == stamp:
        groups[0]["bullets"].append(bullet)
    else:
        groups.insert(0, {"date": stamp, "bullets": [bullet]})  # 최신 우선
    path = _inbox_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(groups), encoding="utf-8")
    return ident


def list_candidates(project: str | Path) -> list[dict]:
    """inbox의 후보를 [{id, date, snippet, source}] 목록으로 반환한다(최신 우선)."""
    return [
        {"id": b["id"], "date": g["date"], "snippet": b["snippet"], "source": b["source"]}
        for g in _load(project)
        for b in g["bullets"]
    ]


def _rewrite(project: str | Path, groups: list[dict]) -> None:
    groups = [g for g in groups if g["bullets"]]  # 빈 날짜 그룹 제거
    path = _inbox_path(project)
    if not groups:
        path.unlink(missing_ok=True)  # 후보가 없으면 파일 제거(빈 상태 = 부재)
        return
    path.write_text(_serialize(groups), encoding="utf-8")


def drop(project: str | Path, ids: list[str] | set[str]) -> list[str]:
    """주어진 id의 후보를 제거하고 실제로 제거된 id를 반환한다."""
    ids = set(ids)
    groups = _load(project)
    removed = [b["id"] for g in groups for b in g["bullets"] if b["id"] in ids]
    for group in groups:
        group["bullets"] = [b for b in group["bullets"] if b["id"] not in ids]
    _rewrite(project, groups)
    return removed


def clear(project: str | Path) -> list[str]:
    """inbox의 모든 후보를 제거하고 제거된 id를 반환한다."""
    ids = [c["id"] for c in list_candidates(project)]
    _inbox_path(project).unlink(missing_ok=True)
    return ids


# --- resolved 원장 --------------------------------------------------------


def is_resolved(project: str | Path, ident: str) -> bool:
    """id가 promoted/discarded로 기록됐는지 여부."""
    path = _ledger_path(project)
    if not path.is_file():
        return False
    return any(
        line.split(" ", 1)[0] == ident for line in path.read_text(encoding="utf-8").splitlines()
    )


def record(project: str | Path, ident: str, status: str, ref: str | None = None) -> None:
    """id를 promoted/discarded로 원장에 기록한다(이미 있으면 무시)."""
    if status not in ("promoted", "discarded"):
        raise ValueError(f"알 수 없는 status: {status}")
    if is_resolved(project, ident):
        return
    path = _ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{ident} {status}" + (f" {ref}" if ref else "")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
