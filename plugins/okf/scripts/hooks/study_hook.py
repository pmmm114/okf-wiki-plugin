"""study capture 훅 (S2, #74 · #91 V2) — PostToolUse(Write·Edit).

Claude Code 메모리 저장을 감지해 ``study.capture`` 정책대로 후보를 inbox에
적재한다. **훅은 절대 승격·디스패치하지 않는다**(모델 부재) — 적재 또는
무동작뿐이다. 메모리 경로 판정과 캡처 스코프 해소(프로젝트/vault 폴백)는
``study_scope``에 위임한다 — 무효 vault 포인터는 이 훅에서 **무음 스킵**이다
(경고 방출은 SessionStart 계열의 몫, #91 §3).

- `capture` `off`(또는 study 부재·vault 미옵트인): 무동작.
- `review`/`auto`: 저장 내용을 파일 추적 스냅샷과 diff해 **새로 나타난 개념 블록만**
  활성 스코프의 inbox에 적재한다(#369 — 무변경 재저장은 완전 무동작·무보고,
  recurrence는 저장 이벤트가 아니라 출현 전이 수). 이미 promoted/discarded된 블록은
  skip. 적재 보고는 사건 유형(추가/변경)을 병기하고 레벨로 갈린다(#366, #352와 같은
  원리) — review는 관측형(승격은 사람 주도), auto만 /study 지시형.

#69 훅 컨벤션 정렬: stdlib-only, 무출력 fail-fast ``exit 0``, ``exit 2`` 미발생,
stdin은 바이트로 읽어 로케일 무관 디코드.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import study_inbox
import study_scope
from okf_hooks import diagnose as _diagnose

# 적재 보고 문구 — 사건 유형(#369)×capture 레벨(#366) 분기의 단일원천. review는
# 관측형(지시 없음 — 승격은 사람 주도), auto만 능동 드레인 지시(#352가 SessionStart에
# 적용한 것과 같은 원리). 대기 규모 표기는 두 레벨 공통이다(무음 적체 방지 — #352).
# 무변경 저장은 문구 자체가 없다(fast-path 무보고 — 아무 일도 없었으므로 신호도 없음).
_EVENT_LABELS = {"added": "메모리 파일 추가", "changed": "메모리 파일 변경"}
_REPORT_FORMS = {
    "review": (
        "{event}(이 파일) — 새 후보 {appended}건을 study 인박스에 적재. "
        "전체 대기 파일 {files}개·{total}건."
    ),
    "auto": (
        "{event}(이 파일) — 새 후보 {appended}건을 study 인박스에 적재. "
        "전체 대기 파일 {files}개·{total}건. /study로 검토·승격하라."
    ),
}


def _dig(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def run(payload: dict, project: str | Path) -> str | None:
    """페이로드를 처리하고 적재 시 안내 문자열을, 아니면 None을 반환한다."""
    file_path = _dig(payload, "tool_input", "file_path")
    if not file_path or not study_scope.is_memory_path(file_path, payload, project):
        return None
    scope = study_scope.resolve_capture(project)
    # 무효 포인터(warning 있음)도 여기선 무음 — PostToolUse는 경고 방출 지점이 아니다
    if scope["capture"] not in ("review", "auto") or scope["runtime_root"] is None:
        return None
    runtime = scope["runtime_root"]  # inbox/ledger는 런타임 루트(vault/폴백=유저 스코프)

    content = _dig(payload, "tool_input", "content")
    if content is None:
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # 파일 추적 diff 캡처(#369) — 스냅샷과 비교해 새로 나타난 블록만 적재된다.
    # 무변경·재출현뿐·전부 기처리면 새 리뷰거리가 없으므로 무보고.
    result = study_inbox.capture_file(runtime, file_path, content)
    if not result["appended"]:
        return None

    # 보고는 파일 단위(#257) — 훅은 호출당 파일 1개를 처리하므로 "이번 저장분"과
    # "전체 대기"를 분리 표기한다. 대기 집계는 캡처 스냅샷 누적이다(파일 현재 상태 아님).
    pending = study_inbox.list_candidates(runtime)
    files = len({c["source"] for c in pending})
    return _REPORT_FORMS[scope["capture"]].format(
        event=_EVENT_LABELS[result["event"]],
        appended=result["appended"],
        files=files,
        total=len(pending),
    )


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
        # rc는 그대로 0이다 — 바꾸는 것은 **진단의 유무**뿐이다(#299). 무음이면
        # study.db 손상이 "메모리 파일 아님"·"capture=off"와 같은 신호가 된다.
        _diagnose("study_hook")
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
