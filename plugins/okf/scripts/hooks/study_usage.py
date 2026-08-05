#!/usr/bin/env python3
"""study 활용 관측 훅 (#400) — UserPromptSubmit(프롬프트) · PostToolUse(개념 로드).

**라벨을 생산한다.** 저장고가 실제로 얼마나 쓰이는지, 어떤 요청 뒤에 어떤 개념이
열리는지에 대한 데이터가 없어서 자문 훅의 유용성을 잴 수 없었다(실측: 프롬프트
5,187건 중 개념 Read 55건·개념 19/41종 — 문턱 판정이 서지 않는 표본). 없는 라벨을
가정하는 대신 관측을 켜서 만든다(#393이 중복 판정 실수요에 쓴 방법과 같다).

**관측만 한다.** 자문하지 않고(두 서브커맨드 모두 stdout 0바이트) 판정하지 않으며
비율·임계·제안을 만들지 않는다. 기록은 `study.capture` 사다리에 종속이라
``off``면 완전 무음이다 — 새 설정 키를 늘리지 않는다.

프롬프트는 **원문**으로 남긴다. 나중에 어휘 겹침 재현율을 재려면 어휘가 그대로
필요하고, 해시만 남기면 활용률은 재도 자문의 유용성은 영영 못 잰다. 저장소는 로컬
유저 스코프 ``study.db``로 커밋되지 않는 소모성 런타임이다.

#69 훅 컨벤션 정렬: stdlib-only, 무출력 fail-fast ``exit 0``, ``exit 2`` 미발생.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import study_scope
import study_store
from okf_hooks import diagnose as _diagnose

ACTION_PROMPT = "prompt"
ACTION_LOAD = "concept_load"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _runtime(project: str | Path) -> str | None:
    """관측 대상 런타임 — 캡처 사다리에 종속(``off``·무스코프는 None)."""
    scope = study_scope.resolve_capture(project)
    if scope["capture"] not in ("review", "auto") or scope["runtime_root"] is None:
        return None
    return scope["runtime_root"]


def _bundle_rel(file_path: str, project: str | Path) -> str | None:
    """읽힌 파일이 승격 대상 번들의 개념이면 번들 상대 경로, 아니면 None.

    판정은 캡처 스코프가 해소한 **승격 대상**(``target``) 기준이다 — 주입 대상과
    같은 곳을 보게 되므로 vault 폴백에서도 관측이 끊기지 않는다. 예약 파일
    (``index.md``·``log.md``)은 개념이 아니라 제외한다.
    """
    scope = study_scope.resolve_capture(project)
    target = scope.get("target")
    if not target:
        return None
    bundle = os.path.join(str(target), ".okf")
    prefix = f"{bundle}/"
    if not file_path.startswith(prefix) or not file_path.endswith(".md"):
        return None
    rel = file_path[len(prefix) :]
    if os.path.basename(rel) in ("index.md", "log.md"):
        return None
    return rel


def run_prompt(payload: dict, project: str | Path) -> None:
    """프롬프트를 원장에 기록한다 — 자문·판정 없음."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return
    runtime = _runtime(project)
    if runtime is None or not study_store.available():
        return
    session = payload.get("session_id")
    study_store.append_event(
        runtime,
        _now(),
        ACTION_PROMPT,
        session if isinstance(session, str) and session else "-",
        {"text": prompt},
    )


def run_load(payload: dict, project: str | Path) -> None:
    """읽힌 개념을 원장에 기록한다 — 어느 세션의 읽기인지만 함께 남긴다."""
    tool_input = payload.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return
    rel = _bundle_rel(file_path, project)
    if rel is None:
        return
    runtime = _runtime(project)
    if runtime is None or not study_store.available():
        return
    session = payload.get("session_id")
    extra = {"session": session} if isinstance(session, str) and session else None
    study_store.append_event(runtime, _now(), ACTION_LOAD, rel, extra)


HANDLERS = {"prompt": run_prompt, "load": run_load}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in HANDLERS:
        print(f"사용법: study_usage.py <{' | '.join(HANDLERS)}>", file=sys.stderr)
        return 1
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        HANDLERS[argv[0]](payload, project)
    except Exception:  # 훅은 어떤 경우에도 세션을 깨지 않는다(#299 — rc는 0, 진단만)
        _diagnose("study_usage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
