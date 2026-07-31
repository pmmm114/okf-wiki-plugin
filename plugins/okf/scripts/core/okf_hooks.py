#!/usr/bin/env python3
"""OKF 플러그인 훅 단일 진입점(#69) — 셸 3종(session_start·post_tool_use·file_changed)을 대체.

서브커맨드: session-start | post-tool-use | file-changed. 동작 계약(파리티
체크리스트)과 오류 정책 통일표는 이슈 #69 본문이 정본이다. 핵심 계약:
fail-fast 경로는 stdout 0바이트 + exit 0, 성공 경로는 JSON 정확히 1개,
exit 2 금지(훅에서 차단성 오류의 특수 의미 — argparse도 같은 이유로 미사용),
예상 외 예외는 exit 1. stdlib 전용 — 소비 머신의 시스템 python3(하한 3.10)로
직접 실행되며 엔진 호출은 `../../bin/okf` 셔틀 서브프로세스로만 한다.
OKF_HOOKS_DEBUG가 비어있지 않으면 트레이스백을 stderr로 출력한다.
"""

import json
import os
import signal
import subprocess
import sys

import okf_remote
import okf_vault

# URL 모드(#153) SessionStart fetch 상한 — fetch-only는 주입 신선도에 기여하지 않으므로
# (워킹트리 미변경) 짧게 잡는다. 실패는 backoff로 dedup되어 반복 스톨을 막는다(D3·D-design).
# 30초 context 호출과 합쳐도 60초 훅 예산 안. 사용자 주도 /study refresh는 별도 긴 상한.
_REMOTE_FETCH_TIMEOUT = 5.0


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
    """`../../bin/okf` 셔틀 실행. 비-제로 종료·OSError·타임아웃은 전부 실패(None) 동치.

    셔틀이 uv를 exec하지 않아 엔진은 손자 프로세스다 — 타임아웃 시 프로세스
    그룹째 회수하고(고아 방지), 유일하게 진단 생산자를 죽이는 경로이므로
    stderr 1줄을 남긴다(다른 실패 경로의 무음과 달리).
    """
    okf = os.path.join(_here(), "..", "..", "bin", "okf")
    stderr = subprocess.DEVNULL if suppress_stderr else None
    try:
        proc = subprocess.Popen(
            [okf, *args], stdout=subprocess.PIPE, stderr=stderr, start_new_session=True
        )
    except OSError as exc:
        # 셔틀을 spawn조차 못한 것이다(uv 부재·권한·경로 손상). 무음으로 두면 이 실패가
        # "링크가 없다"·"설정이 없다"와 **완전히 같은 무출력**이 되어 진단 경로가 없다(#299).
        # 반환값은 그대로 실패 동치(None) — 바꾸는 것은 진단의 유무뿐이다.
        print(
            f"okf_hooks: okf {args[0]} 실행 불가({exc.__class__.__name__}) — 생략", file=sys.stderr
        )
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


def _remote_freshness():
    """URL 모드 포인터의 신선도 갱신 — SessionStart **단일 지점** fetch-only(#153 U1-5).

    origin ref만 최신화하고 worktree는 건드리지 않는다(ff는 /study 진입에서, U3-2).
    bounded·TTL dedup·무URL/오프라인/미생성은 무동작. 실패(오프라인·인증)는 캐시로
    저하하고 무음 — 이 훅은 신선도로 세션을 깨지 않는다. 예외는 전부 삼킨다.
    """
    try:
        okf_remote.session_fetch(timeout=_REMOTE_FETCH_TIMEOUT)
    except Exception:
        pass


def hook_session_start():
    project = _project_dir()
    _remote_freshness()  # #153: URL 모드면 fetch-only(무URL·오프라인은 무동작)
    # vault 폴백(#91 V3): 프로젝트 설정 존재가 판별자, 없으면 유효 vault로. SessionStart는
    # 무효 포인터 경고의 방출 지점이다(§3) — PostToolUse 계열은 무음 유지.
    resolved = okf_vault.resolve_inject(project)
    if resolved["warning"]:
        _emit("SessionStart", {"additionalContext": resolved["warning"]})
        return 0
    if resolved["target"] is None:
        return 0
    project = resolved["target"]
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
    okf_args = ["context", bundle, "--max-chars", _jq_out(max_chars)]
    # 인식층 등 임의 축으로 주입 컨텍스트를 섹션 구분(엔진 --group-by에 그대로 위임).
    # 비어있지 않은 문자열일 때만 부가 — 미설정·빈 값은 현행 그대로(파리티 보존).
    group_by = context_cfg.get("groupBy")
    if isinstance(group_by, str) and group_by.strip():
        okf_args += ["--group-by", _jq_out(group_by)]
    ctx = _run_okf(okf_args, suppress_stderr=False)
    if ctx is None:
        return 0
    _emit("SessionStart", {"additionalContext": ctx, "watchPaths": _watch_paths(bundle)})
    return 0


def _format_adjacent(rows) -> str:
    """축·정초 인접 후보 문장 — path별 근거(축=값·via=축) 병기. 형식 불량은 빈 문자열.

    판정·임계값 문구를 만들지 않는다(재료 제공 규율) — 무엇을 이을지는 사람+모델의
    몫이고 이 문장은 근거 딸린 후보 목록일 뿐이다. 스텁·오류문 등 비정형 응답이
    제안으로 둔갑하지 않게 형식이 어긋나면 통째로 버린다.
    """
    if not isinstance(rows, list) or not rows:
        return ""
    by_path: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            return ""
        path, basis = row.get("path"), row.get("basis")
        if not isinstance(path, str) or not isinstance(basis, str):
            return ""
        by_path.setdefault(path, []).append(basis)
    items = " ".join(f"{path}({', '.join(bases)})" for path, bases in sorted(by_path.items()))
    return f"축·정초 인접 후보(근거 병기): {items} — 링크로 이어지지 않은 관계 검토용."


# 공유 리스트 축 값(태그류) + 정초 엣지(via=축) 인접 — 축 이름을 하드코딩하지 않는다.
# 리스트 kind만 값 공유를 보는 이유: type류 단일값 분류 축은 번들 절반이 공유해
# 재료가 아니라 소음이 된다(#329의 다중값 술어와 같은 kind 기준). 상한은 LIMIT
# (절단은 소비자 몫 — 엔진은 절단하지 않는다).
_ADJACENT_SQL = (
    "SELECT a2.path AS path, a1.axis || '=' || a1.value AS basis "
    "FROM axis_value a1 JOIN axis_value a2 ON a2.axis = a1.axis "
    "AND a2.value = a1.value AND a2.path <> a1.path "
    "WHERE a1.path = '{rel}' AND a1.kind = 'list' "
    "UNION "
    "SELECT CASE WHEN src = '{rel}' THEN dst ELSE src END AS path, "
    "'via=' || via AS basis FROM edge "
    "WHERE via IS NOT NULL AND (src = '{rel}' OR dst = '{rel}') "
    "ORDER BY path, basis LIMIT 12"
)


def _axis_adjacent(bundle: str, rel: str) -> str:
    """`okf query`로 축·정초 인접 후보를 얻는다(#337) — 실패·비JSON은 빈 문자열.

    rel은 작은따옴표 이스케이프로 리터럴에 넣는다(엔진 query는 단문 SQL 계약이라
    파라미터 바인딩이 없다).
    """
    safe = rel.replace("'", "''")
    out = _run_okf(
        ["query", bundle, _ADJACENT_SQL.format(rel=safe), "--json"], suppress_stderr=True
    )
    if not out:
        return ""
    try:
        rows = json.loads(out)
    except ValueError:
        return ""
    return _format_adjacent(rows)


def hook_post_tool_use():
    # 대상 번들은 `resolve_inject`로 푼다(#327) — 프로젝트 설정만 보면 vault 폴백
    # (#91 V3) 모드에서 vault 번들 파일을 편집해도 역링크 제안이 영원히 없다(세 훅 중
    # 이 훅만 다른 해소를 쓰던 공백). 경고는 방출하지 않는다 — 무효 포인터 경고의
    # 방출 지점은 SessionStart 하나다(§3, PostToolUse 계열 무음 유지).
    resolved = okf_vault.resolve_inject(_project_dir())
    target = resolved["target"]
    if target is None:
        return 0
    cfg = _load_config(target)
    if cfg is None:
        return 0
    payload = _read_payload()
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    # 추출 키는 `.tool_input.file_path` — top-level file_path는 무동작(#69 계약)
    file_path = _payload_str(tool_input.get("file_path")) if isinstance(tool_input, dict) else None
    if not file_path:
        return 0
    bundle = _bundle_dir(target, cfg)
    # 정규화 없는 문자열 접두사 판정(${file#"$bundle"/} 등가) — 트레일링 슬래시·
    # 상대경로·심링크 불일치는 "무동작"으로 고정(#69 계약)
    prefix = f"{bundle}/"
    if not file_path.startswith(prefix):
        return 0
    rel = file_path[len(prefix) :]
    # **정확 일치**로 묻는다(#300). `rel`은 이미 번들 상대 정규 경로라 부분일치를 쓰면
    # 짧은 파일명이 긴 파일명을 삼킨다 — `a.md`가 `banana.md`를 물어, 무관한 개념이
    # "이 파일을 링크한다"며 컨텍스트로 주입된다.
    links = _run_okf(["graph", bundle, "--linked-to-exact", rel], suppress_stderr=True)
    # 축·정초 인접(#337) — 링크는 이미 이어진 관계만 담으므로, 공유 리스트 축 값과
    # 정초 엣지로 이어지지 않은 관계 후보를 근거와 함께 병기한다. 링크 0건이어도
    # 인접이 있으면 방출한다(링크 없는 개념끼리의 연결 후보가 핵심 이득).
    adjacent = _axis_adjacent(bundle, rel)
    if not links and not adjacent:
        return 0
    if links:
        joined = links.replace("\n", " ")
        message = (
            f"수정한 번들 파일({rel})로 링크하는 개념: {joined} "
            f"— 관련 개념과 log.md 갱신 필요 여부를 검토하라."
        )
    else:
        message = f"수정한 번들 파일({rel}) — 관련 개념과 log.md 갱신 필요 여부를 검토하라."
    if adjacent:
        message = f"{message} {adjacent}"
    _emit("PostToolUse", {"additionalContext": message})
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
    # 번들 소속 검사(#299). 없으면 **번들 밖 파일 변경에도** "대응 개념을 갱신하고
    # log.md에 엔트리를 추가하라"가 주입된다 — 존재하지 않는 개념을 찾게 만드는 오탐이다.
    # 판정 방식은 `hook_post_tool_use`와 같은 **정규화 없는 문자열 접두사**로 맞춘다.
    #
    # 대상 번들은 `resolve_inject`로 푼다 — 그냥 `_load_config(project)`를 쓰면 vault
    # 폴백(#91 V3) 사용자에게 **기능이 사라진다**. 그 모드에서는 프로젝트에 설정이
    # 없고 watchPaths가 vault 번들을 가리키므로, 프로젝트 설정만 보면 감시 중인 파일이
    # 바뀌어도 영원히 무동작이다(오탐을 고치다 무음을 만드는 꼴).
    resolved = okf_vault.resolve_inject(_project_dir())
    target = resolved["target"]
    if target is None:
        return 0
    cfg = _load_config(target)
    if cfg is None:
        return 0
    prefix = f"{_bundle_dir(target, cfg)}/"
    if not file_path.startswith(prefix):
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


def diagnose(source: str) -> None:
    """전면 ``except``의 **진단 1줄**. 반환 코드는 호출자가 정한다.

    훅의 fail-fast(세션을 깨지 않는다)는 의도된 설계이고 이 함수는 그것을 바꾸지 않는다
    — 바꾸는 것은 **무음의 유무**다. 출력이 0줄이면 내부 오류(예: study.db 손상)가
    "메모리 파일 아님"·"capture=off"와 **완전히 같은 신호**가 되어 진단이 불가능하다.

    훅 3종의 단일 오류 정책이다(#299) — 각자 다른 문구·다른 스위치를 쓰면 소비처가
    무엇을 켜야 상세를 보는지 알 수 없다.
    """
    if os.environ.get("OKF_HOOKS_DEBUG"):
        import traceback

        traceback.print_exc()
    else:
        print(
            f"{source}: 예상 외 오류 — OKF_HOOKS_DEBUG=1로 재실행하면 상세 출력",
            file=sys.stderr,
        )


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
        diagnose("okf_hooks")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
