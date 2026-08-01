"""bin/okf-py 부트스트랩 셔틀 + 훅 spawn 게이트 (#108).

훅 커맨드는 로그인 쉘 PATH 보정 없이 직접 spawn된다(exec form: `args` 존재 →
셸 없음). 그래서 두 가지 ENOENT 회귀가 났다 — (1) bare `python3`는 최소 PATH
(GUI 앱)에서 죽고, (2) #108을 셔틀로 고치며 `command`에 남긴 셸용 따옴표가
벗겨지지 않아 파일명에 박혀 다시 죽었다(`posix_spawn '"…/bin/okf-py"'`). 셔틀의
해석 순서(OKF_PYTHON → PATH python3 → 관례 절대경로 → PATH python)와 통과 계약
(stdin·인자·exit code 무변형), 그리고 재유입을 막는 그렙 게이트(bare python3 +
exec form 따옴표·공백)를 고정한다.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
SHIM = PLUGIN / "bin" / "okf-py"
ABS_CANDIDATES = ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"]


def run_shim(args, *, shim=SHIM, env_override=None, stdin=b""):
    env = os.environ.copy()
    for key, value in (env_override or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run([str(shim), *args], input=stdin, env=env, capture_output=True, timeout=60)


# --- 통과 계약 ---------------------------------------------------------------


def test_exec_bit_and_runs_script_with_args_and_stdin(tmp_path):
    assert os.access(SHIM, os.X_OK)  # 실행 비트 유실 = 훅 전멸
    script = tmp_path / "t.py"
    script.write_text("import sys; print(sys.argv[1]); sys.stdout.write(sys.stdin.read())\n")
    res = run_shim([str(script), "ARG"], stdin=b'{"a":1}', env_override={"OKF_PYTHON": None})
    assert res.returncode == 0, res.stderr
    assert res.stdout == b'ARG\n{"a":1}'


def test_exit_code_propagates(tmp_path):
    # 스캐폴드 가드 exit 3 같은 의미 있는 코드가 셔틀에서 뭉개지면 안 된다
    script = tmp_path / "t.py"
    script.write_text("import sys; sys.exit(3)\n")
    assert run_shim([str(script)], env_override={"OKF_PYTHON": None}).returncode == 3


# --- 해석 순서 ---------------------------------------------------------------


def test_okf_python_override_wins_without_path(tmp_path):
    # PATH에 아무것도 없어도 OKF_PYTHON 명시 지정이면 동작한다(최우선)
    script = tmp_path / "t.py"
    script.write_text("print('V')\n")
    res = run_shim(
        [str(script)],
        env_override={"OKF_PYTHON": sys.executable, "PATH": str(tmp_path)},
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == b"V\n"


def test_minimal_path_resolves_absolute_candidate(tmp_path):
    # 회귀 재현(#108): python3 없는 최소 PATH spawn에서도 절대경로 폴백으로 동작
    if not any(Path(p).exists() for p in ABS_CANDIDATES):
        pytest.skip("관례 절대경로에 python3 없음")
    script = tmp_path / "t.py"
    script.write_text("print('P')\n")
    res = run_shim([str(script)], env_override={"OKF_PYTHON": None, "PATH": str(tmp_path)})
    assert res.returncode == 0, res.stderr
    assert res.stdout == b"P\n"


def test_no_interpreter_visible_127(tmp_path):
    # 전 후보 실패 시: stderr 1줄 진단 + exit 127(조용한 실패 금지)
    body = SHIM.read_text(encoding="utf-8")
    for cand in ABS_CANDIDATES:
        body = body.replace(cand, str(tmp_path / "nonexistent-python3"))
    crippled = tmp_path / "okf-py"
    crippled.write_text(body, encoding="utf-8")
    crippled.chmod(0o755)
    script = tmp_path / "t.py"
    script.write_text("print('X')\n")
    res = run_shim(
        [str(script)],
        shim=crippled,
        env_override={"OKF_PYTHON": None, "PATH": str(tmp_path)},
    )
    assert res.returncode == 127
    assert res.stdout == b""
    assert b"OKF_PYTHON" in res.stderr


# --- 재유입 게이트 (#108 bare python3 금지 + exec form 따옴표·공백 금지) ------


def _hook_specs():
    """``(event, matcher, spec)`` 3튜플 — matcher가 기대 테이블의 대조 대상이 된다.

    예전에는 spec만 돌려줬다. 그래서 **어느 이벤트의 어느 matcher에 걸린 배선인지**를
    테스트가 볼 수 없었고, 같은 이벤트의 두 엔트리가 서로 다른 matcher를 갖는 비대칭
    (`Write` vs `Write|Edit`)이 게이트 밖에 있었다(#299).
    """
    data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return [
        (event, entry.get("matcher"), h)
        for event, groups in data["hooks"].items()
        for entry in groups
        for h in entry["hooks"]
    ]


# 배선 스크립트 → (이벤트, matcher). matcher는 `None`이 "전체"다(FileChanged·SessionStart).
#
# study 캡처 훅의 matcher가 `Write`뿐이라, 기존 메모리 파일에 **Edit로 덧붙인** 사실은
# 훅이 spawn조차 되지 않아 inbox에 들어가지 않았다 — `study_hook.py`는 `content` 부재
# (=Edit 페이로드) 시 디스크에서 읽는 폴백을 **이미 구현**하고 있었는데도. 바로 옆
# PostToolUse 엔트리가 `Write|Edit`이었으므로 같은 이벤트 안에서 비대칭이었다(#299).
EXPECTED_WIRING = {
    "hooks/okf_hooks.py": {
        ("SessionStart", None),
        ("PostToolUse", "Write|Edit"),
        ("FileChanged", None),  # 감시 등록은 watchPaths 몫이라 matcher를 두지 않는다
    },
    "hooks/study_session.py": {("SessionStart", None)},
    "hooks/study_hook.py": {("PostToolUse", "Write|Edit")},
    # 턴 종료 회고는 도구 종류를 가리지 않는다 — 무엇이 막혔든 세야 하므로 matcher가 없다
    "hooks/study_reflect.py": {("Stop", None)},
}


def test_hook_wiring_matches_expected_matchers():
    """배선 스크립트별 (이벤트, matcher)가 기대 테이블과 정확히 같다.

    같은 이벤트에 걸린 엔트리들이 서로 다른 matcher를 갖는 비대칭은 **조용한 미발화**다
    — 훅이 안 도는 것과 "돌았는데 해당 없음"이 사용자에게 똑같이 보인다.
    """
    actual: dict[str, set] = {}
    for event, matcher, spec in _hook_specs():
        args = spec.get("args", [])
        script = args[0] if args else spec["command"]
        rel = script.split("/scripts/")[-1]
        actual.setdefault(rel, set()).add((event, matcher))
    assert actual == EXPECTED_WIRING, f"배선 불일치\n실제: {actual}\n기대: {EXPECTED_WIRING}"


def test_hooks_json_exec_form_no_shell_quoting():
    """훅은 exec form(`args` 존재)으로 spawn된다 — 셸이 없어 `command`의 따옴표·
    공백이 벗겨지지 않는다. #108(`posix_spawn 'python3'`)을 셔틀로 고치며 command에
    셸용 따옴표를 남겼더니, 그 따옴표가 파일명에 그대로 박혀 다시 ENOENT가 났다
    (`posix_spawn '"…/bin/okf-py"'` — #108 후속 회귀). 계약: `command`는 따옴표·공백
    없는 단일 실행파일, 인자·서브커맨드는 전부 `args`로. bare python3 금지도 유지."""
    specs = _hook_specs()
    assert specs
    for _event, _matcher, h in specs:
        cmd = h["command"]
        args = h.get("args", [])
        # exec form: command는 단일 토큰이어야 한다. 따옴표는 리터럴 경로 문자가
        # 되고(=회귀 원인), 공백은 argv 분리를 일으킨다 — 둘 다 spawn을 깬다.
        assert '"' not in cmd, f"command 따옴표 금지(exec form 오염): {cmd}"
        assert " " not in cmd, f"command 공백 금지(인자는 args로): {cmd}"
        # #108: 인터프리터는 bin/okf-py 셔틀 경유 — bare python3 직접 spawn 금지.
        assert "python3" not in cmd, cmd
        assert all("python3" not in a for a in args), args
        # 각 args 원소는 argv 하나로 그대로 전달된다 — 따옴표 금지(리터럴이 된다).
        assert all('"' not in a for a in args), args
        # 훅은 **전부** Python이다 — 레거시 `.sh` 허용 분기는 #299에서 제거했다.
        # 그 분기가 있는 한 "셸로 되돌리는 것"이 게이트를 통과한다.
        assert cmd == "${CLAUDE_PLUGIN_ROOT}/bin/okf-py", (
            f"훅 command는 bin/okf-py 셔틀이어야 한다(셸 훅 금지): {cmd}"
        )
        assert args and args[0].startswith("${CLAUDE_PLUGIN_ROOT}/scripts/"), args
        assert args[0].endswith(".py"), args


def test_command_docs_no_bare_python3():
    docs = sorted((PLUGIN / "commands").glob("*.md"))
    assert docs
    for md in docs:
        assert "python3" not in md.read_text(encoding="utf-8"), md.name


# --- 배달되는 스크립트는 전부 도달 가능해야 한다 (#299) -------------------------
#
# 죽은 스크립트는 **진단을 잘못된 곳으로 보낸다**. 실제로 `session_start.sh`가
# `hooks.json`에서 빠진 뒤에도 남아 있었고, 문서가 그것을 설정 소비 주체로 서술했다.
# U11이 "문서가 배선 안 된 것을 지목하는가"를 막았다면 이 게이트는 반대편 —
# **배달물 안에 도달 불가능한 실행 스크립트가 있는가** — 를 막는다(전이 도달성).

SCRIPTS = PLUGIN / "scripts"


def _all_scripts() -> dict[str, Path]:
    return {p.name: p for p in SCRIPTS.rglob("*") if p.suffix in (".py", ".sh") and p.is_file()}


def _entry_points() -> set[str]:
    """훅·커맨드가 직접 부르는 스크립트 이름 — 도달성 탐색의 뿌리."""
    roots: set[str] = set()
    for _event, _matcher, spec in _hook_specs():
        for token in [spec["command"], *spec.get("args", [])]:
            if token.endswith((".py", ".sh")):
                roots.add(token.rsplit("/", 1)[-1])
    for md in (PLUGIN / "commands").rglob("*.md"):
        # **호출 꼴**만 센다(`…/scripts/<하위>/<이름>`). 맨 이름 등장까지 뿌리로 삼으면
        # 산문의 언급이 진입점이 되어 고아가 살아 있는 것처럼 보인다 — 도달성 게이트에서
        # 관대함은 곧 **탐지 실패**다(U11이 문서 쪽 판별을 좁힌 것과 같은 이유).
        text = md.read_text(encoding="utf-8")
        roots |= {name for name in _all_scripts() if "/scripts/" in text and f"/{name}" in text}
    return roots


def _reachable() -> set[str]:
    """진입점에서 import로 닿는 전이 폐포."""
    scripts = _all_scripts()
    sources = {name: path.read_text(encoding="utf-8") for name, path in scripts.items()}
    stems = {path.stem: name for name, path in scripts.items()}

    seen: set[str] = set()
    frontier = list(_entry_points() & set(scripts))
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(sources[name])) if name.endswith(".py") else ():
            if isinstance(node, ast.Import):
                targets = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module.split(".")[0]]
            else:
                continue
            frontier += [stems[t] for t in targets if t in stems and stems[t] not in seen]
    return seen


def test_every_shipped_script_is_reachable():
    """`scripts/**`의 실행 스크립트가 전부 진입점에서 도달 가능하다."""
    orphans = sorted(set(_all_scripts()) - _reachable())
    assert not orphans, (
        f"도달 불가능한 스크립트: {orphans} — 배선하거나 삭제하라. "
        "남겨 두면 문서·진단이 죽은 코드를 가리킨다."
    )
