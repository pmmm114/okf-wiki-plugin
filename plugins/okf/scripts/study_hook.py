"""study capture 훅 (S2, #74 · #91 V2) — PostToolUse(Write).

Claude Code 메모리 저장을 감지해 ``study.capture`` 정책대로 후보를 inbox에
적재한다. **훅은 절대 승격·디스패치하지 않는다**(모델 부재) — 적재 또는
무동작뿐이다. 메모리 경로 판정과 캡처 스코프 해소(프로젝트/홈 폴백)는
``okf_home``에 위임한다 — 무효 홈 포인터는 이 훅에서 **무음 스킵**이다
(경고 방출은 SessionStart 계열의 몫, #91 §3).

- `capture` `off`(또는 study 부재·홈 미옵트인): 무동작.
- `review`/`auto`: 저장 파일의 최신 라인을 스니펫으로 뽑아 활성 스코프의
  inbox에 적재(이미 promoted/discarded된 내용이면 skip).

#69 훅 컨벤션 정렬: stdlib-only, 무출력 fail-fast ``exit 0``, ``exit 2`` 미발생,
stdin은 바이트로 읽어 로케일 무관 디코드.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import okf_home
import okf_inbox
import study_blocks


def _dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def run(payload: dict, project: str | Path) -> str | None:
    """페이로드를 처리하고 적재 시 안내 문자열을, 아니면 None을 반환한다."""
    file_path = _dig(payload, "tool_input", "file_path")
    if not file_path or not okf_home.is_memory_path(file_path, payload, project):
        return None
    scope = okf_home.resolve_capture(project)
    # 무효 포인터(warning 있음)도 여기선 무음 — PostToolUse는 경고 방출 지점이 아니다
    if scope["capture"] not in ("review", "auto") or scope["runtime_root"] is None:
        return None
    runtime = scope["runtime_root"]  # inbox/ledger는 런타임 루트(홈/폴백=유저 스코프)

    content = _dig(payload, "tool_input", "content")
    if content is None:
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # 저장 내용의 **모든 개념 블록**을 적재한다(#131) — 예전 "마지막 줄만"의 과소 캡처를
    # 없애고 scan과 동일 단위로 통일한다. 이미 처리된 블록(자식 전부 resolved)은 건너뛴다.
    appended = 0
    for block in study_blocks.concept_blocks(content):
        snippet = " ".join(block)
        if not snippet:
            continue
        line_hashes = [okf_inbox.content_hash(line)[:12] for line in block]
        bid = okf_inbox.content_hash(snippet)[:12]
        if okf_inbox.block_resolved(runtime, bid, line_hashes):
            continue  # 블록 id 또는 모든 자식 줄이 이미 promoted/discarded
        okf_inbox.append(runtime, snippet, file_path, line_hashes=line_hashes)
        appended += 1
    if not appended:
        return None

    pending = len(okf_inbox.list_candidates(runtime))
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
