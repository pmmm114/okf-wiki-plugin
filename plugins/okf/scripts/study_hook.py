"""study capture 훅 (S2, #74) — PostToolUse(Write).

Claude Code 메모리 저장(``~/.claude/projects/*/memory/*.md``으로의 Write)을 감지해
``study.capture`` 정책대로 후보를 inbox에 적재한다. **훅은 절대 승격·디스패치하지
않는다**(모델 부재) — 적재 또는 무동작뿐이다.

- `capture` `off`(또는 study 부재): 무동작.
- `review`/`auto`: 저장 파일의 최신 라인을 스니펫으로 뽑아 inbox에 적재(이미
  promoted/discarded된 내용이면 skip).

#69 훅 컨벤션 정렬: stdlib-only, 무출력 fail-fast ``exit 0``, ``exit 2`` 미발생,
stdin은 바이트로 읽어 로케일 무관 디코드. 메모리 경로 매칭은 Claude Code 버전에
취약한 지점(견고성 리스크)으로 문서화한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import okf_inbox

_MEMORY_RE = re.compile(r"/\.claude/projects/[^/]+/memory/[^/]+\.md$")
_BULLET_RE = re.compile(r"^[*\-+]\s+")


def _dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _is_memory_path(path: str) -> bool:
    return bool(_MEMORY_RE.search(path))


def _load_capture(project: str | Path) -> str:
    config = Path(project) / ".okf-wiki.json"
    if not config.is_file():
        return "off"
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "off"
    study = data.get("study") if isinstance(data, dict) else None
    return (study or {}).get("capture", "off")


def _extract_snippet(content: str) -> str:
    """저장 내용에서 후보 스니펫(최신 비헤딩 라인)을 뽑는다."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith("#"):
            return _BULLET_RE.sub("", line)
    return ""  # 헤딩뿐이면 지식 후보 아님


def run(payload: dict, project: str | Path) -> str | None:
    """페이로드를 처리하고 적재 시 안내 문자열을, 아니면 None을 반환한다."""
    file_path = _dig(payload, "tool_input", "file_path")
    if not file_path or not _is_memory_path(file_path):
        return None
    if _load_capture(project) not in ("review", "auto"):
        return None

    content = _dig(payload, "tool_input", "content")
    if content is None:
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    snippet = _extract_snippet(content)
    if not snippet:
        return None

    ident = okf_inbox.content_hash(snippet)[:12]
    if okf_inbox.is_resolved(project, ident):
        return None  # 이미 promoted/discarded된 메모리 → 재적재 안 함

    okf_inbox.append(project, snippet, file_path)
    pending = len(okf_inbox.list_candidates(project))
    return f"메모리 후보를 study 인박스에 적재({pending}개 대기). /study로 검토·승격하라."


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        message = run(payload, project)
    except Exception:  # 훅은 어떤 경우에도 세션을 깨지 않는다(fail-fast)
        return 0
    if message:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": message,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
