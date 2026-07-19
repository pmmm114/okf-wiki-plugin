"""study SessionStart 나즈 (S5, #77) — capture=auto의 능동 드레인 트리거.

``capture: auto``이고 inbox에 후보가 쌓여 있으면 세션 시작 시 "N개 대기"를
알려 모델이 승격 플로우를 능동적으로 돌리게 한다(auto = 저장 시 magic이 아니라
살아있는 세션의 능동 드레인). `review`/`off`나 후보 0이면 무출력.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import okf_inbox


def run(project: str | Path) -> str | None:
    config = Path(project) / ".okf-wiki.json"
    if not config.is_file():
        return None
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    study = (data.get("study") if isinstance(data, dict) else None) or {}
    if study.get("capture") != "auto":
        return None
    pending = len(okf_inbox.list_candidates(project))
    if pending == 0:
        return None
    return (
        f"study: 승격 대기 후보 {pending}개(capture=auto). "
        "study 승격 플로우로 검토·승격하라 — 핸들러 실행은 로컬 trust 승인이 필요하다."
    )


def main(argv: list[str] | None = None) -> int:
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        message = run(project)
    except Exception:  # 훅은 세션을 깨지 않는다(fail-fast)
        return 0
    if message:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": message,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
