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
    data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return [h for groups in data["hooks"].values() for entry in groups for h in entry["hooks"]]


def test_hooks_json_exec_form_no_shell_quoting():
    """훅은 exec form(`args` 존재)으로 spawn된다 — 셸이 없어 `command`의 따옴표·
    공백이 벗겨지지 않는다. #108(`posix_spawn 'python3'`)을 셔틀로 고치며 command에
    셸용 따옴표를 남겼더니, 그 따옴표가 파일명에 그대로 박혀 다시 ENOENT가 났다
    (`posix_spawn '"…/bin/okf-py"'` — #108 후속 회귀). 계약: `command`는 따옴표·공백
    없는 단일 실행파일, 인자·서브커맨드는 전부 `args`로. bare python3 금지도 유지."""
    specs = _hook_specs()
    assert specs
    for h in specs:
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
        if cmd.endswith("/bin/okf-py"):  # Python 훅: 셔틀 + 스크립트 경로는 args로
            assert cmd == "${CLAUDE_PLUGIN_ROOT}/bin/okf-py", cmd
            assert args and args[0].startswith("${CLAUDE_PLUGIN_ROOT}/scripts/"), args
            assert args[0].endswith(".py"), args
        else:  # 레거시 .sh: 셔뱅 절대경로로 직접 spawn(#108 미해당)
            assert cmd.startswith("${CLAUDE_PLUGIN_ROOT}/scripts/"), cmd
            assert cmd.endswith(".sh"), cmd


def test_command_docs_no_bare_python3():
    docs = sorted((PLUGIN / "commands").glob("*.md"))
    assert docs
    for md in docs:
        assert "python3" not in md.read_text(encoding="utf-8"), md.name
