"""bin/okf-py 부트스트랩 셔틀 + bare python3 금지 게이트 (#108).

훅 커맨드는 로그인 쉘 PATH 보정 없이 직접 spawn될 수 있어(GUI 앱 최소 PATH)
bare `python3`가 ENOENT로 죽는다 — v0.2.0 실사용 회귀. 셔틀의 해석 순서
(OKF_PYTHON → PATH python3 → 관례 절대경로 → PATH python)와 통과 계약
(stdin·인자·exit code 무변형), 그리고 재유입을 막는 그렙 게이트를 고정한다.
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


# --- 재유입 게이트 (#108 — bare python3 금지) --------------------------------


def test_hooks_json_commands_shuttle_only():
    data = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for groups in data["hooks"].values()
        for entry in groups
        for h in entry["hooks"]
    ]
    assert commands
    for cmd in commands:
        assert "python3" not in cmd, cmd
        assert cmd.startswith('"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" ') or cmd.startswith(
            "${CLAUDE_PLUGIN_ROOT}/scripts/"  # 레거시 .sh 직접 경로(셔뱅 spawn)
        ), cmd


def test_command_docs_no_bare_python3():
    docs = sorted((PLUGIN / "commands").glob("*.md"))
    assert docs
    for md in docs:
        assert "python3" not in md.read_text(encoding="utf-8"), md.name
