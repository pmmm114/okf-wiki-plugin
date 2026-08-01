"""study 회고 나즈 — Stop(턴 종료)에서 막힌 자리를 세어 지식화를 유도한다.

캡처 입구(``study_hook``)는 **모델이 이미 남기기로 판단한 것**(메모리 저장)만 잡는다.
그래서 막히고 우회한 경험 — 실패한 명령, 거부된 도구 — 은 그 판단이 일어나지 않으면
흔적 없이 사라진다. 이 훅은 그 자리를 턴 단위로 세어 모델에게 되돌린다.

**훅은 무엇이 지식인지 판정하지 않는다**(모델 부재). 적재도 하지 않는다 — 세어서
되돌릴 뿐이고, 남길지와 무엇을 남길지는 모델이, 적재는 기존 캡처 입구가 한다.
그래서 이 훅은 inbox·원장·db를 건드리지 않고 상태도 남기지 않는다.

관측은 하네스가 준 **구조적 표식**만 쓴다 — 트랜스크립트 ``tool_result``의
``is_error``. 명령 성패를 출력 문자열로 추정하지 않는다(실측: Bash 결과의 stdout에
에러가 섞여 들어오고 stderr는 비어 있어 문자열 판정은 오탐한다). 실패와 거부의
**구분**만 하네스 고정 문구에 기대며, 못 알아보면 실패로 접는다(과소 분류는 안전).

턴 경계는 ``prompt_id``다 — Stop 페이로드의 값과 트랜스크립트 레코드의 ``promptId``가
같은 값이라 이전 턴을 다시 세지 않는다.

- `capture` `off`(또는 study 부재·vault 미옵트인): 무동작.
- `review`/`auto`: 이번 턴에 실패·거부가 있으면 1회 되돌린다. 없으면 무출력.

#69 훅 컨벤션 정렬: stdlib-only, 무출력 fail-fast ``exit 0``, ``exit 2`` 미발생,
stdin은 바이트로 읽어 로케일 무관 디코드.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import study_scope

# 역방향으로 한 번에 읽는 크기. 우리가 보는 것은 **마지막 턴 하나**뿐이고 그 경계에서
# 멈추므로, 세션이 수십 MB여도 실제로 읽는 양은 턴 크기에 비례한다.
_CHUNK_BYTES = 256 * 1024

# 폭주 방어 상한. 고정 창으로 자르면 큰 턴의 앞부분을 놓치는데, 실측상 그것이 드물지
# 않다 — 사용자 트랜스크립트 280세션 중 4건(1.4%)에 2MB를 넘는 턴이 있었고 최대는
# 5.65MB였다(세션 최대는 41MB). 그래서 상한은 턴 경계 탐색이 실패했을 때만 걸리도록
# 넉넉히 둔다. 여기 걸리면 과소 집계로 끝난다(=조용함).
_MAX_SCAN_BYTES = 16 * 1024 * 1024

# 하네스가 사용자 거부에 붙이는 고정 문구. 이것 하나로 "막은 것"과 "깨진 것"이 갈린다.
_REJECTED = "The tool use was rejected"


def _reversed_lines(path: str | Path, chunk: int = _CHUNK_BYTES):
    """파일 끝에서 역방향으로 줄을 하나씩 준다(메모리는 청크 하나 분량).

    소비자가 턴 경계에서 멈추므로 파일 전체를 읽지 않는다. 청크 경계에서 반토막 난
    앞부분은 다음 청크와 이어 붙여 살린다 — 잘라 버리면 그 레코드가 통째로 사라진다.
    """
    target = Path(path)
    pos = target.stat().st_size
    scanned = 0
    pending = b""  # 청크 앞쪽의 미완성 조각 — 다음(=더 앞) 청크와 이어 붙는다
    with target.open("rb") as handle:
        while pos > 0 and scanned < _MAX_SCAN_BYTES:
            # 상한은 남은 양에도 걸린다 — 청크 경계에서만 보면 파일이 청크보다 작을 때
            # 상한이 전혀 걸리지 않는다(전체를 한 번에 읽어 버린다).
            step = min(chunk, pos, _MAX_SCAN_BYTES - scanned)
            pos -= step
            scanned += step
            handle.seek(pos)
            block = handle.read(step) + pending
            pieces = block.split(b"\n")
            pending = pieces.pop(0) if pos > 0 else b""
            for raw in reversed(pieces):
                yield raw.decode("utf-8", "replace")
        if pending:
            yield pending.decode("utf-8", "replace")


def count_turn_errors(transcript: str | Path, prompt_id: str | None) -> tuple[int, int]:
    """이번 턴의 (실패, 거부) 수를 센다.

    뒤에서부터 훑다가 **다른 턴의 레코드**를 만나면 멈춘다. ``prompt_id``가 없으면
    (하네스가 안 준 경우) 경계를 못 그으므로 세지 않는다 — 세션 전체를 싸잡아 세는
    것보다 조용한 편이 낫다.
    """
    if not prompt_id:
        return 0, 0
    failed = rejected = 0
    seen: set[str] = set()
    for line in _reversed_lines(transcript):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        owner = record.get("promptId")
        if owner and owner != prompt_id:
            break  # 이전 턴에 닿았다 — 역순이므로 더 볼 것이 없다
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or not item.get("is_error"):
                continue
            ident = item.get("tool_use_id")
            if ident:
                if ident in seen:
                    continue  # 같은 호출이 재기록될 수 있다 — 한 번만 센다
                seen.add(ident)
            body = item.get("content")
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            if _REJECTED in text:
                rejected += 1
            else:
                failed += 1
    return failed, rejected


def _message(failed: int, rejected: int) -> str:
    counts = []
    if failed:
        counts.append(f"도구 실패 {failed}건")
    if rejected:
        counts.append(f"거부 {rejected}건")
    return (
        f"이번 턴에 {'·'.join(counts)}. 다음에도 적용될 원인·제약을 알게 됐다면 "
        "메모리에 한 줄로 남겨라 — 이번 오류 자체가 아니라 재발을 막는 규칙만. "
        "남길 것이 없으면 아무것도 하지 않는다."
    )


def run(payload: dict, project: str | Path) -> str | None:
    """페이로드를 처리하고 되돌릴 안내 문자열을, 아니면 None을 반환한다."""
    # 이미 이 턴에서 Stop 훅이 개입했다면 같은 실패를 다시 세게 된다 — 되돌림이
    # 되돌림을 부르지 않도록 한 턴에 한 번으로 묶는다.
    if payload.get("stop_hook_active"):
        return None
    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return None
    scope = study_scope.resolve_capture(project)
    # 무효 포인터(warning 있음)도 여기선 무음 — 경고 방출은 SessionStart 계열의 몫이다
    if scope["capture"] not in ("review", "auto"):
        return None
    failed, rejected = count_turn_errors(transcript, payload.get("prompt_id"))
    if not failed and not rejected:
        return None
    return _message(failed, rejected)


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
                        "hookEventName": "Stop",
                        "additionalContext": message,
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
