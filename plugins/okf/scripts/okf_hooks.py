#!/usr/bin/env python3
"""OKF 플러그인 훅 단일 진입점(#69) — 셸 3종(session_start·post_tool_use·file_changed)을 대체.

서브커맨드: session-start | post-tool-use | file-changed. 동작 계약(파리티
체크리스트)과 오류 정책 통일표는 이슈 #69 본문이 정본이다. 핵심 계약:
fail-fast 경로는 stdout 0바이트 + exit 0, 성공 경로는 JSON 정확히 1개,
exit 2 금지(훅에서 차단성 오류의 특수 의미 — argparse도 같은 이유로 미사용),
예상 외 예외는 exit 1. stdlib 전용 — 소비 머신의 시스템 python3(하한 3.10)로
직접 실행되며 엔진 호출은 `../bin/okf` 셔틀 서브프로세스로만 한다.
OKF_HOOKS_DEBUG가 비어있지 않으면 트레이스백을 stderr로 출력한다.
"""

import json
import os
import signal
import subprocess
import sys


def _okf_timeout():
    # 초 — Claude Code 훅 타임아웃 한도(60초)를 잠식하지 않는 상한.
    # OKF_HOOKS_TIMEOUT은 테스트·디버그용 오버라이드(비정상 값은 기본값).
    try:
        return float(os.environ["OKF_HOOKS_TIMEOUT"])
    except (KeyError, ValueError):
        return 30.0


class _Skip(Exception):
    """fail-fast 조기 종료(무출력 exit 0). 인자가 있으면 stderr 1줄을 남긴다."""


def _here():
    # BASH_SOURCE 기반 dirname 등가 — 심링크를 해소하면 ../bin 상대 구조가
    # 깨질 수 있으므로 resolve 없이 논리 경로만 절대화한다.
    return os.path.dirname(os.path.abspath(__file__))


def _project_dir():
    # ${CLAUDE_PROJECT_DIR:-$PWD} 등가 — 빈 값도 폴백. bash는 기동 시 상속 PWD를
    # cwd와 stat 대조해 stale이면 리셋하므로, env PWD는 cwd와 같은 디렉토리일
    # 때만(심링크 별칭 논리 경로 보존) 채택하고 아니면 물리 cwd로 폴백한다.
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        return project
    pwd = os.environ.get("PWD")
    if pwd and os.path.isabs(pwd):
        try:
            if os.path.samefile(pwd, os.getcwd()):
                return pwd
        except OSError:
            pass
    return os.getcwd()


def _emit(event, fields):
    out = {"hookSpecificOutput": {"hookEventName": event, **fields}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _jq_str(value):
    # jq -r 등가 문자열화 — 비문자열 설정값·payload 값의 표기를 셸판과 맞춘다.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _fallback(value, default):
    # jq `//` 등가 — null·false만 기본값으로 대체한다(""·0은 유지).
    return default if value is None or value is False else value


def _jq_out(value):
    # `$(jq -r …)` 커맨드 치환 등가 — -r 표기 후 후행 개행 전부 스트립.
    return _jq_str(value).rstrip("\n")


def _load_config(project):
    """`.okf-wiki.json` 로드. 부재 → None, 깨짐·비객체 → stderr 1줄 + exit 0(정책표)."""
    path = f"{project}/.okf-wiki.json"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        raise _Skip(f".okf-wiki.json 파스 실패 — 훅 생략: {exc}") from exc
    if not isinstance(cfg, dict):
        raise _Skip(".okf-wiki.json이 JSON 객체가 아님 — 훅 생략")
    return cfg


def _read_payload():
    """stdin JSON 파스. 비JSON·빈 입력 → stderr 1줄 + exit 0(정책표 — 셸판 exit 5 통일)."""
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise _Skip(f"훅 payload 파스 실패 — 생략: {exc}") from exc


def _payload_str(value):
    # jq `// empty` 등가 — null·false·부재는 None(fail-fast 신호), 그 외
    # `$(jq -r …)` 표기(후행 개행 스트립 포함). 폴백 판정은 raw 값 기준이고
    # 스트립은 -r 출력 이후라는 순서가 셸판과 같다.
    if value is None or value is False:
        return None
    return _jq_out(value)


def _bundle_dir(project, cfg):
    bundle_rel = _jq_out(_fallback(cfg.get("bundlePath"), ".okf"))
    # 문자열 결합 유지 — os.path.join은 절대경로 bundlePath에서 project를
    # 탈락시켜 셸판의 무동작을 동작으로 바꾼다(#69 계약).
    bundle = f"{project}/{bundle_rel}"
    if not os.path.isdir(bundle):
        raise _Skip()
    return bundle


def _run_okf(args, suppress_stderr):
    """`../bin/okf` 셔틀 실행. 비-제로 종료·OSError·타임아웃은 전부 실패(None) 동치.

    셔틀이 uv를 exec하지 않아 엔진은 손자 프로세스다 — 타임아웃 시 프로세스
    그룹째 회수하고(고아 방지), 유일하게 진단 생산자를 죽이는 경로이므로
    stderr 1줄을 남긴다(다른 실패 경로의 무음과 달리).
    """
    okf = os.path.join(_here(), "..", "bin", "okf")
    stderr = subprocess.DEVNULL if suppress_stderr else None
    try:
        proc = subprocess.Popen(
            [okf, *args], stdout=subprocess.PIPE, stderr=stderr, start_new_session=True
        )
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=_okf_timeout())
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait()
        print(f"okf_hooks: okf {args[0]} 시간 초과({_okf_timeout():g}초) — 생략", file=sys.stderr)
        return None
    if proc.returncode != 0:
        return None
    # $(…) 커맨드 치환 등가 — 후행 개행 전부 제거(additionalContext 바이트 파리티 전제)
    return out.decode("utf-8", "replace").rstrip("\n")


def _watch_paths(bundle):
    # `find "$bundle" -type f -name '*.md'` 등가(-P): 숨김 포함, 정규 파일만
    # (FIFO 등 제외), 심링크 파일 제외, 심링크 디렉토리·번들 루트 미하강.
    # 비UTF-8 바이트 파일명은 jq -R처럼 U+FFFD로 치환(surrogate가 새어나가면
    # UTF-8 인코딩이 터진다). 읽기 불가 하위 디렉토리는 조용히 건너뛴다 —
    # 셸판은 pipefail로 JSON 방출 후 exit 1이지만 비-0 종료는 출력 폐기라
    # 부분 결과 exit 0으로 통일(의도된 변경, 오류 정책표와 정합).
    # 순서만 sorted로 결정론화(계약 아님).
    if os.path.islink(bundle):
        return []
    paths = []
    for root, _dirs, files in os.walk(bundle):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                paths.append(os.fsencode(path).decode("utf-8", "replace"))
    return sorted(paths)


def hook_session_start():
    project = _project_dir()
    cfg = _load_config(project)
    if cfg is None:
        return 0
    # JSON 리터럴 false만 off — `== False`는 0을 오판하므로 `is False`(#69 계약)
    if cfg.get("inject") is False:
        return 0
    bundle = _bundle_dir(project, cfg)
    context_cfg = cfg.get("context")
    if not isinstance(context_cfg, dict):
        context_cfg = {}  # 타입 불량은 기본값 관용(정책표 — 셸판 exit 5 통일)
    max_chars = _fallback(context_cfg.get("maxChars"), 8000)
    ctx = _run_okf(["context", bundle, "--max-chars", _jq_out(max_chars)], suppress_stderr=False)
    if ctx is None:
        return 0
    _emit("SessionStart", {"additionalContext": ctx, "watchPaths": _watch_paths(bundle)})
    return 0


def hook_post_tool_use():
    project = _project_dir()
    cfg = _load_config(project)
    if cfg is None:
        return 0
    payload = _read_payload()
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    # 추출 키는 `.tool_input.file_path` — top-level file_path는 무동작(#69 계약)
    file_path = _payload_str(tool_input.get("file_path")) if isinstance(tool_input, dict) else None
    if not file_path:
        return 0
    bundle = _bundle_dir(project, cfg)
    # 정규화 없는 문자열 접두사 판정(${file#"$bundle"/} 등가) — 트레일링 슬래시·
    # 상대경로·심링크 불일치는 "무동작"으로 고정(#69 계약)
    prefix = f"{bundle}/"
    if not file_path.startswith(prefix):
        return 0
    rel = file_path[len(prefix) :]
    links = _run_okf(["graph", bundle, "--linked-to", rel], suppress_stderr=True)
    if not links:
        return 0
    joined = links.replace("\n", " ")
    _emit(
        "PostToolUse",
        {
            "additionalContext": (
                f"수정한 번들 파일({rel})로 링크하는 개념: {joined} "
                f"— 관련 개념과 log.md 갱신 필요 여부를 검토하라."
            )
        },
    )
    return 0


def hook_file_changed():
    payload = _read_payload()
    if not isinstance(payload, dict):
        return 0
    # `.file_path // .path // empty` 3단 폴백 — null·false만 다음 후보로 넘어간다
    file_path = _payload_str(payload.get("file_path"))
    if file_path is None:
        file_path = _payload_str(payload.get("path"))
    if not file_path:
        return 0
    _emit(
        "FileChanged",
        {
            "additionalContext": (
                f"번들 파일 변경 감지: {file_path} — 대응 개념 문서를 갱신하고 "
                f"가장 가까운 log.md에 일자 엔트리를 추가하라(§7)."
            )
        },
    )
    return 0


HOOKS = {
    "session-start": hook_session_start,
    "post-tool-use": hook_post_tool_use,
    "file-changed": hook_file_changed,
}


def main(argv):
    if len(argv) != 1 or argv[0] not in HOOKS:
        names = " | ".join(HOOKS)
        print(f"사용법: okf_hooks.py <{names}>", file=sys.stderr)
        return 1
    try:
        return HOOKS[argv[0]]()
    except _Skip as skip:
        if skip.args:
            print(f"okf_hooks: {skip.args[0]}", file=sys.stderr)
        return 0
    except Exception:
        if os.environ.get("OKF_HOOKS_DEBUG"):
            import traceback

            traceback.print_exc()
        else:
            print(
                "okf_hooks: 예상 외 오류 — OKF_HOOKS_DEBUG=1로 재실행하면 상세 출력",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
