"""훅 동작 기대값 테이블 (#69 · #299).

훅 3종(session-start·post-tool-use·file-changed)의 **Python 단일 구현**을 케이스별
기대값으로 고정한다. 예전에는 셸 구현과의 차동 파리티 하네스였는데, `hooks.json`이
셸을 배선하고 py 구현이 죽은 코드로 남아 있던 시기의 형태다 — #299가 배선을 py로
옮기고 `.sh` 3종을 지우면서 대조 대상이 사라졌다.

py 단독으로 남기면서 고정한 것(셸 시절과 달라진 계약):

  ① **jq 부재 무음이 없다.** 셸 구현은 `command -v jq || exit 0`으로 시작해, jq 없는
     PATH에서 rc=0·무출력을 냈다 — "번들 밖 파일이라 해당 없음"과 **완전히 같은
     신호**라 진단이 불가능했다. Python 구현에는 그 경로 자체가 없다.
  ② **file-changed가 번들 소속을 본다.** 셸 구현은 검사가 없어 번들 밖 파일 변경에도
     "대응 개념을 갱신하라"를 주입했고, 그 오탐이 이 표에 계약으로 박혀 있었다.
  ③ 깨진 config·비JSON payload·타입 불량은 전부 rc 0 + 무동작(관용).
  ④ okf 호출에 상한이 있다(기본 30초, `OKF_HOOKS_TIMEOUT`) — 초과 시 프로세스 그룹
     회수 + stderr 1줄 + 실패 동치.
  ⑤ 읽기 불가 하위 디렉토리는 **부분 결과 + rc 0**(셸은 pipefail로 출력째 폐기).

엔진은 bin/okf 스텁으로 격리하고(호출 기록·응답을 OKF_STUB_DIR로 제어),
실엔진 E2E는 uv가 있을 때만 2케이스 돈다.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
FC_MSG = (
    "번들 파일 변경 감지: {file} — 대응 개념 문서를 갱신하고 "
    "가장 가까운 log.md에 일자 엔트리를 추가하라(§7)."
)
PTU_MSG = (
    "수정한 번들 파일({rel})로 링크하는 개념: {links} "
    "— 관련 개념과 log.md 갱신 필요 여부를 검토하라."
)

STUB_OKF = """#!/usr/bin/env bash
# 파리티 스텁 — 호출 기록·응답을 $OKF_STUB_DIR 파일로 제어한다
printf '%s\\n' "$*" >> "$OKF_STUB_DIR/calls"
if [ -f "$OKF_STUB_DIR/stderr" ]; then cat "$OKF_STUB_DIR/stderr" >&2; fi
if [ -f "$OKF_STUB_DIR/stdout" ]; then cat "$OKF_STUB_DIR/stdout"; fi
exit "$(cat "$OKF_STUB_DIR/exit" 2>/dev/null || echo 0)"
"""


@pytest.fixture()
def henv(tmp_path):
    scripts = tmp_path / "plugin" / "scripts"
    # 실제 배치 구조 미러링 — 진입점은 scripts/hooks/, okf_hooks가 import하는
    # okf_vault·okf_remote는 vault(저장고) 도메인이다(#153). 교차 도메인 해석은
    # run_hook이 bin/okf-py처럼 PYTHONPATH로 잇는다.
    (scripts / "hooks").mkdir(parents=True)
    (scripts / "vault").mkdir(parents=True)
    shutil.copy2(PLUGIN / "scripts" / "hooks" / "okf_hooks.py", scripts / "hooks" / "okf_hooks.py")
    for name in ["okf_vault.py", "okf_remote.py"]:
        shutil.copy2(PLUGIN / "scripts" / "vault" / name, scripts / "vault" / name)
    bin_dir = tmp_path / "plugin" / "bin"
    bin_dir.mkdir()
    (bin_dir / "okf").write_text(STUB_OKF)
    (bin_dir / "okf").chmod(0o755)
    stub = tmp_path / "stub"
    stub.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    return SimpleNamespace(scripts=scripts, stub=stub, project=project)


def run_hook(scripts, hook, *, project, stdin=b"", stub=None, env_override=None, cwd=None):
    cmd = [sys.executable, str(scripts / "hooks" / "okf_hooks.py"), hook]
    env = os.environ.copy()
    # 교차 도메인 import 배선 — 실배치의 bin/okf-py PYTHONPATH 미러
    env["PYTHONPATH"] = os.pathsep.join(
        [str(scripts / "vault"), *filter(None, [env.get("PYTHONPATH")])]
    )
    env["CLAUDE_PROJECT_DIR"] = str(project)
    if stub is not None:
        env["OKF_STUB_DIR"] = str(stub)
    if env_override:
        for key, value in env_override.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    if cwd is None:
        cwd = env_override.get("PWD") if env_override else None
    return subprocess.run(cmd, input=stdin, env=env, capture_output=True, timeout=120, cwd=cwd)


def sem(res):
    """stdout을 의미 비교용 JSON으로 — 무출력이면 None."""
    text = res.stdout.decode("utf-8")
    if not text.strip():
        return None
    parsed = json.loads(text)
    hso = parsed.get("hookSpecificOutput", {})
    if isinstance(hso.get("watchPaths"), list):
        hso["watchPaths"] = sorted(hso["watchPaths"])
    return parsed


def read_and_reset_calls(stub):
    calls = stub / "calls"
    text = calls.read_text() if calls.exists() else ""
    if calls.exists():
        calls.unlink()
    return text


# ── 케이스 테이블 ────────────────────────────────────────────────────────────
# config: 미지정=파일 없음 / dict=JSON 직렬화 / str=원문 그대로(깨진 JSON용)
# payload: 미지정=빈 stdin / dict·list=JSON 직렬화 / bytes=원문 그대로
# bundle: 번들 상대 md 파일 목록(디렉토리 자동 생성), bundle_at: 번들 위치(기본 .okf)
# stub: bin/okf 스텁 응답 {stdout, stderr, exit}
# 기대값: rc(기본 0), out: "none"(기본)|"emit", ctx: additionalContext 리터럴
#   (`{proj}`는 프로젝트 경로로 치환), calls_contain: okf 호출 인자에 있어야 할 조각.
#   `calls_contain`이 없으면 **엔진을 부르지 않았음**을 단언한다 — 기본을 관대하게
#   두면 배선이 끊겨 아무것도 안 불러도 통과한다. stderr: None|"empty"|"nonempty"|"boom"
CASES = [
    # ── session-start ──
    dict(id="ss-config-부재", hook="session-start", calls="none"),
    dict(
        id="ss-깨진-config",  # 비파리티 ② — 양쪽 rc0, 메시지 문구만 다름
        hook="session-start",
        config="{broken",
        calls="none",
        stderr="nonempty",
    ),
    dict(
        id="ss-inject-false",
        hook="session-start",
        config={"inject": False},
        bundle=["a.md"],
        calls="none",
    ),
    dict(
        id="ss-inject-0-함정",  # 0은 false가 아니다 — `is False` 계약
        hook="session-start",
        config={"inject": 0},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="context ",
    ),
    dict(
        id="ss-inject-문자열-false",
        hook="session-start",
        config={"inject": "false"},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="context ",
    ),
    dict(id="ss-번들-부재", hook="session-start", config={}, calls="none"),
    dict(
        id="ss-bundlePath-커스텀",
        hook="session-start",
        config={"bundlePath": "kb"},
        bundle=["a.md"],
        bundle_at="kb",
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="/kb --max-chars 8000",
    ),
    dict(
        id="ss-bundlePath-절대경로-문자열결합",  # join이면 project 탈락 — 결합 계약 실증
        hook="session-start",
        config={"bundlePath": "/abs"},
        bundle=["a.md"],
        bundle_at="abs",
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="//abs --max-chars 8000",
    ),
    dict(
        id="ss-bundlePath-빈문자열",  # jq `//`는 ""를 기본값으로 바꾸지 않는다
        hook="session-start",
        config={"bundlePath": ""},
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="context ",
    ),
    dict(
        id="ss-bundlePath-false-기본값",
        hook="session-start",
        config={"bundlePath": False},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="/.okf --max-chars 8000",
    ),
    dict(
        id="ss-maxChars-커스텀",
        hook="session-start",
        config={"context": {"maxChars": 1234}},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="--max-chars 1234",
    ),
    dict(
        id="ss-maxChars-0-유지",  # jq `//`는 0을 기본값으로 바꾸지 않는다
        hook="session-start",
        config={"context": {"maxChars": 0}},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="--max-chars 0",
    ),
    dict(
        id="ss-maxChars-null-기본값",
        hook="session-start",
        config={"context": {"maxChars": None}},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="--max-chars 8000",
    ),
    dict(
        id="ss-groupBy-커스텀",  # 축 섹션 구분 — sh·py 동일하게 --group-by 부가
        hook="session-start",
        config={"context": {"groupBy": "layer"}},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="--max-chars 8000 --group-by layer",
    ),
    dict(
        id="ss-context-타입불량",  # 비파리티 ④ — sh는 jq 오류 5, py는 기본값 관용
        hook="session-start",
        config={"context": False},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        rc=0,
        out="py-only",
        ctx="CTX",
        calls="skip",
        calls_contain="--max-chars 8000",
    ),
    dict(
        id="ss-bundlePath-후행개행",  # $(jq -r) 후행 개행 스트립 — 값 쪽 등가
        hook="session-start",
        config={"bundlePath": ".okf\n"},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="/.okf --max-chars 8000",
    ),
    dict(
        id="ss-maxChars-후행개행",
        hook="session-start",
        config={"context": {"maxChars": "1234\n"}},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
        calls_contain="--max-chars 1234",
    ),
    dict(
        id="ss-okf-실패-stderr-통과",  # context는 stderr 통과 + 실패 시 무출력 exit 0
        hook="session-start",
        config={},
        bundle=["a.md"],
        stub={"stderr": "boom\n", "exit": 3},
        stderr="boom",
        calls_contain="context ",
    ),
    dict(
        id="ss-ctx-빈문자열도-출력",  # 빈 컨텍스트여도 성공 경로는 JSON 1개
        hook="session-start",
        config={},
        bundle=["a.md"],
        stub={"stdout": ""},
        out="same",
        ctx="",
        calls_contain="context ",
    ),
    # ── post-tool-use ──
    dict(
        id="ptu-config-부재",
        hook="post-tool-use",
        payload={"tool_input": {"file_path": "/x/a.md"}},
        calls="none",
    ),
    dict(
        id="ptu-깨진-config",  # 비파리티 ② — sh 5 → py 0
        hook="post-tool-use",
        config="{broken",
        payload={"tool_input": {"file_path": "/x/a.md"}},
        rc=0,
        calls="none",
    ),
    dict(
        id="ptu-비JSON-payload",  # 비파리티 ③ — sh 5 → py 0
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload=b"notjson",
        rc=0,
        calls="none",
    ),
    dict(
        id="ptu-빈-stdin",  # jq는 빈 입력에 무출력 성공 — 양쪽 rc0
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload=b"",
        calls="none",
    ),
    dict(
        id="ptu-배열-payload",  # 비파리티 ④ — sh 5 → py 0
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload=[1],
        rc=0,
        calls="none",
    ),
    dict(
        id="ptu-file_path-부재",
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {}},
        calls="none",
    ),
    dict(
        id="ptu-top-level-함정",  # 추출 키는 .tool_input.file_path 뿐
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"file_path": "{proj}/.okf/a.md"},
        calls="none",
    ),
    dict(
        id="ptu-file_path-false",
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": False}},
        calls="none",
    ),
    dict(
        id="ptu-file_path-숫자",  # jq -r "123" — 접두사 무매칭으로 무동작
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": 123}},
        calls="none",
    ),
    dict(
        id="ptu-번들-밖",
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "/elsewhere/a.md"}},
        calls="none",
    ),
    dict(
        id="ptu-상대경로-무매칭-고정",
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": ".okf/a.md"}},
        calls="none",
    ),
    dict(
        id="ptu-트레일링-슬래시-무매칭-고정",
        hook="post-tool-use",
        config={"bundlePath": ".okf/"},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        calls="none",
    ),
    dict(
        id="ptu-역링크-유-메시지-바이트",
        hook="post-tool-use",
        config={},
        bundle=["sub/doc.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/sub/doc.md"}},
        stub={"stdout": "a.md\nb.md\n"},
        out="same",
        ctx=PTU_MSG.format(rel="sub/doc.md", links="a.md b.md"),
        calls_contain="--linked-to-exact sub/doc.md",
        stderr="empty",
    ),
    dict(
        id="ptu-inject-false여도-동작",  # 현행 비대칭 유지 계약
        hook="post-tool-use",
        config={"inject": False},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        stub={"stdout": "b.md\n"},
        out="same",
        ctx=PTU_MSG.format(rel="a.md", links="b.md"),
        calls_contain="graph ",
    ),
    dict(
        id="ptu-file_path-후행개행",  # 후행 개행 스트립 — rel·질의 인자 등가
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md\n"}},
        stub={"stdout": "b.md\n"},
        out="same",
        ctx=PTU_MSG.format(rel="a.md", links="b.md"),
        calls_contain="--linked-to-exact a.md",
    ),
    dict(
        id="ptu-graph-개행뿐-무출력",  # $(…) 스트립 등가 — rstrip 함정
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        stub={"stdout": "\n"},
        calls_contain="graph ",
    ),
    dict(
        id="ptu-graph-실패-stderr-억제",  # graph는 stderr 억제 + 실패=링크 없음
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        stub={"stderr": "boom\n", "exit": 3},
        stderr="empty",
        calls_contain="graph ",
    ),
    # ── file-changed ──
    #
    # 번들 소속 검사가 생겼다(#299). 예전에는 검사가 아예 없어 **번들 밖 파일 변경에도**
    # "대응 개념 문서를 갱신하고 log.md에 엔트리를 추가하라"가 주입됐다 — 존재하지 않는
    # 개념을 찾게 만드는 오탐이고, 그 오탐이 이 표에 계약으로 고정돼 있었다(`/x/y.md`가
    # 출력을 낸다는 케이스). 안/밖을 쪼개 **밖은 무출력**으로 뒤집는다. 바로 옆
    # post-tool-use는 처음부터 같은 접두 검사를 하고 있었다.
    dict(
        id="fc-번들-안-file_path",
        hook="file-changed",
        config={},
        bundle=["y.md"],
        payload={"file_path": "{proj}/.okf/y.md"},
        out="emit",
        ctx=FC_MSG.format(file="{proj}/.okf/y.md"),
    ),
    dict(
        id="fc-번들-밖-무출력",  # 오탐 계약 폐기 — 검사 없던 시절엔 여기서 출력이 났다
        hook="file-changed",
        config={},
        bundle=["y.md"],
        payload={"file_path": "/x/y.md"},
    ),
    dict(
        id="fc-config-부재-무출력",  # 번들을 특정할 수 없으면 판정하지 않는다
        hook="file-changed",
        payload={"file_path": "/x/y.md"},
    ),
    dict(
        id="fc-커스텀-bundlePath-안",
        hook="file-changed",
        config={"bundlePath": "kb"},
        bundle=["y.md"],
        bundle_at="kb",
        payload={"file_path": "{proj}/kb/y.md"},
        out="emit",
        ctx=FC_MSG.format(file="{proj}/kb/y.md"),
    ),
    dict(
        id="fc-path-폴백",
        hook="file-changed",
        config={},
        bundle=["z.md"],
        payload={"path": "{proj}/.okf/z.md"},
        out="emit",
        ctx=FC_MSG.format(file="{proj}/.okf/z.md"),
    ),
    dict(
        id="fc-false-후-path-폴백",  # null·false만 다음 후보로 넘어간다
        hook="file-changed",
        config={},
        bundle=["z.md"],
        payload={"file_path": False, "path": "{proj}/.okf/z.md"},
        out="emit",
        ctx=FC_MSG.format(file="{proj}/.okf/z.md"),
    ),
    dict(
        id="fc-숫자-file_path",  # 문자열화되지만 번들 밖이라 무출력
        hook="file-changed",
        config={},
        bundle=["y.md"],
        payload={"file_path": 7},
    ),
    dict(id="fc-키-부재", hook="file-changed", config={}, payload={}),
    dict(
        id="fc-file_path-개행뿐",  # 스트립 후 빈 값
        hook="file-changed",
        config={},
        payload={"file_path": "\n"},
    ),
    dict(
        id="fc-file_path-후행개행",
        hook="file-changed",
        config={},
        bundle=["z.md"],
        payload={"file_path": "{proj}/.okf/z.md\n"},
        out="emit",
        ctx=FC_MSG.format(file="{proj}/.okf/z.md"),
    ),
    dict(
        id="fc-비JSON-payload",
        hook="file-changed",
        payload=b"oops",
        rc=0,
    ),
    dict(id="fc-빈-stdin", hook="file-changed", payload=b"", calls="none"),
]


def _setup(henv, case):
    project = henv.project
    config = case.get("config", "부재")
    if config != "부재":
        text = config if isinstance(config, str) else json.dumps(config)
        (project / ".okf-wiki.json").write_text(text)
    for rel in case.get("bundle", []):
        path = project / case.get("bundle_at", ".okf") / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# doc\n")
    stub = case.get("stub", {})
    if "stdout" in stub:
        (henv.stub / "stdout").write_text(stub["stdout"])
    if "stderr" in stub:
        (henv.stub / "stderr").write_text(stub["stderr"])
    if "exit" in stub:
        (henv.stub / "exit").write_text(str(stub["exit"]))
    payload = case.get("payload", b"")
    if not isinstance(payload, bytes):
        payload = json.dumps(payload).replace("{proj}", str(project)).encode()
    return payload


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_hook_behavior(henv, case):
    payload = _setup(henv, case)
    res = run_hook(henv.scripts, case["hook"], project=henv.project, stdin=payload, stub=henv.stub)
    calls = read_and_reset_calls(henv.stub)

    assert res.returncode == case.get("rc", 0), res.stderr

    if case.get("out", "none") == "none":
        assert res.stdout == b"", res.stdout
    else:
        assert sem(res) is not None
    if "ctx" in case:
        expected = case["ctx"].replace("{proj}", str(henv.project))
        assert sem(res)["hookSpecificOutput"]["additionalContext"] == expected

    # `calls` 기본이 **"none"**이다(sh 대조가 있던 시절의 "same"이 아니라). 엔진을
    # 부르는 케이스는 `calls_contain`으로 무엇을 불렀는지 **명시**해야 한다 — 기본을
    # 관대하게 두면 배선이 끊겨 아무것도 부르지 않아도 통과한다.
    if "calls_contain" in case:
        assert case["calls_contain"] in calls, calls  # 무엇을 불렀는지 명시한 케이스
    elif case.get("calls", "none") == "none":
        assert calls == "", calls

    stderr = case.get("stderr")
    if stderr == "empty":
        assert res.stderr == b"", res.stderr
    elif stderr == "nonempty":
        assert res.stderr != b""
    elif stderr == "boom":
        assert b"boom" in res.stderr


def test_watch_paths_find_equivalence(henv):
    """find -P 등가: 숨김 포함·심링크 파일 제외·심링크 디렉토리 미하강·대문자
    확장자 제외·비정규 파일(FIFO) 제외."""
    bundle = henv.project / ".okf"
    (bundle / "sub").mkdir(parents=True)
    for rel in ["root.md", "sub/nested.md", ".hidden.md", "UPPER.MD", "noext"]:
        (bundle / rel).write_text("# doc\n")
    (bundle / "link.md").symlink_to(bundle / "root.md")
    (bundle / "linkdir").symlink_to(bundle / "sub")
    if hasattr(os, "mkfifo"):
        os.mkfifo(bundle / "fifo.md")
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")

    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    expected = sorted(str(bundle / rel) for rel in [".hidden.md", "root.md", "sub/nested.md"])
    assert sem(res)["hookSpecificOutput"]["watchPaths"] == expected


def test_watch_paths_symlink_bundle_root(henv):
    """번들 루트 자체가 심링크면 find -P는 하강하지 않는다 — watchPaths []."""
    real = henv.project / "real-bundle"
    real.mkdir()
    (real / "a.md").write_text("# doc\n")
    (henv.project / ".okf").symlink_to(real)
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")

    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    assert sem(res)["hookSpecificOutput"]["watchPaths"] == []


def test_pwd_fallback(henv):
    """CLAUDE_PROJECT_DIR 부재·빈 값이면 $PWD로 폴백한다."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("CTX\n")

    for value in (None, ""):
        res = run_hook(
            henv.scripts,
            "session-start",
            project=henv.project,
            stub=henv.stub,
            env_override={"CLAUDE_PROJECT_DIR": value, "PWD": str(henv.project)},
        )
        assert res.returncode == 0, res.stderr
        assert sem(res) is not None, value


def test_pwd_stale_reset(henv, tmp_path):
    """stale $PWD(cwd와 다른 디렉토리)는 무시하고 cwd를 쓴다 — bash 기동 시
    PWD 검증 등가. 검증 없이 env PWD를 믿으면 py만 무동작이 된다."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("CTX\n")
    stale = tmp_path / "stale"
    stale.mkdir()

    res = run_hook(
        henv.scripts,
        "session-start",
        project=henv.project,
        stub=henv.stub,
        env_override={"CLAUDE_PROJECT_DIR": None, "PWD": str(stale)},
        cwd=str(henv.project),
    )
    assert res.returncode == 0, res.stderr
    assert sem(res) is not None
    assert str(henv.project) in sem(res)["hookSpecificOutput"]["watchPaths"][0]


def test_watch_paths_non_utf8_filename(henv):
    """비UTF-8 바이트 파일명은 jq -R처럼 U+FFFD 치환으로 방출한다(성공 경로 유지)."""
    bundle = henv.project / ".okf"
    bundle.mkdir()
    (bundle / "a.md").write_text("# doc\n")
    try:
        with open(os.fsencode(bundle) + b"/\xff bad.md", "wb") as f:
            f.write(b"# doc\n")
    except OSError:
        pytest.skip("파일시스템이 비UTF-8 파일명을 불허")
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")

    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    assert str(bundle / "� bad.md") in sem(res)["hookSpecificOutput"]["watchPaths"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root는 디렉토리 권한을 무시")
def test_unreadable_subdir_yields_partial_result(henv):
    """읽기 불가 하위 디렉토리는 **부분 결과 + exit 0**이다.

    셸 구현은 pipefail로 JSON을 방출한 뒤 exit 1이라 그 출력이 통째로 폐기됐다 —
    읽을 수 있는 파일까지 함께 사라진다. 의도된 변경이라 계약으로 남긴다.
    """
    bundle = henv.project / ".okf"
    locked = bundle / "locked"
    locked.mkdir(parents=True)
    (bundle / "a.md").write_text("# doc\n")
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")
    locked.chmod(0o000)
    try:
        res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    finally:
        locked.chmod(0o755)
    assert res.returncode == 0 and res.stderr == b""
    assert sem(res)["hookSpecificOutput"]["watchPaths"] == [str(bundle / "a.md")]


def test_okf_timeout_diagnosable_and_reaps(henv):
    """비파리티 ⑤ 고정(py 단독): 타임아웃 시 실패 동치(무출력 exit 0)이되
    stderr 1줄을 남기고, 셔틀의 손자 프로세스까지 그룹째 회수한다."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    okf = henv.scripts.parent / "bin" / "okf"
    okf.write_text(
        '#!/usr/bin/env bash\n( sleep 2; echo leaked > "$OKF_STUB_DIR/orphan" ) &\nsleep 30\n'
    )

    res = run_hook(
        henv.scripts,
        "session-start",
        project=henv.project,
        stub=henv.stub,
        env_override={"OKF_HOOKS_TIMEOUT": "0.5"},
    )
    assert res.returncode == 0
    assert res.stdout == b""
    assert "시간 초과".encode() in res.stderr
    import time

    time.sleep(2.5)  # 고아가 살아있다면 orphan 파일을 썼을 시간
    assert not (henv.stub / "orphan").exists()


def test_direct_execution_and_usage_errors(tmp_path):
    """실행 비트+셔뱅으로 직접 실행 가능해야 하고(플립 후 전멸 방지), 서브커맨드
    누락·불명은 exit 1이다(훅 차단 의미인 exit 2 금지)."""
    script = PLUGIN / "scripts" / "hooks" / "okf_hooks.py"
    assert os.access(script, os.X_OK)
    pythonpath = os.pathsep.join(
        [str(PLUGIN / "scripts" / "vault"), *filter(None, [os.environ.get("PYTHONPATH")])]
    )
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "PYTHONPATH": pythonpath}
    ok = subprocess.run([str(script), "session-start"], env=env, capture_output=True)
    assert ok.returncode == 0 and ok.stdout == b""
    for args in ([], ["unknown"]):
        res = subprocess.run([str(script), *args], env=env, capture_output=True)
        assert res.returncode == 1 and res.stderr != b""


# ── 실엔진 E2E (uv 필요) ─────────────────────────────────────────────────────

needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv 필요")


def _real_bundle(project):
    bundle = project / ".okf"
    bundle.mkdir()
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n# Concepts\n\n* [A](a.md) - a 개념.\n* [B](b.md) - b 개념.\n'
    )
    (bundle / "a.md").write_text(
        "---\ntype: concept\ntitle: A\ndescription: a 개념.\n---\n[B](/b.md) 참조.\n"
    )
    (bundle / "b.md").write_text("---\ntype: concept\ntitle: B\ndescription: b 개념.\n---\n본문.\n")
    (project / ".okf-wiki.json").write_text("{}")
    return bundle


@needs_uv
def test_e2e_session_start_real_engine(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    bundle = _real_bundle(project)
    res = run_hook(PLUGIN / "scripts", "session-start", project=project)
    assert res.returncode == 0, res.stderr
    hso = sem(res)["hookSpecificOutput"]
    assert hso["additionalContext"].startswith("<okf-context>")
    assert sorted(str(bundle / f) for f in ["a.md", "b.md", "index.md"]) == hso["watchPaths"]


@needs_uv
def test_e2e_post_tool_use_real_engine(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    bundle = _real_bundle(project)
    payload = json.dumps({"tool_input": {"file_path": str(bundle / "b.md")}}).encode()
    res = run_hook(PLUGIN / "scripts", "post-tool-use", project=project, stdin=payload)
    assert res.returncode == 0, res.stderr
    assert sem(res) is not None
    assert "a.md" in sem(res)["hookSpecificOutput"]["additionalContext"]


@needs_uv
def test_e2e_post_tool_use_axis_adjacent_real_engine(tmp_path):
    """축·정초 인접 후보가 링크 기반 제안과 함께 나온다(#337) — 근거(축=값·via=축) 병기.

    링크는 사람이 이미 이은 것만 담지만 공유 리스트 축 값·정초 엣지는 이어지지 않은
    관계 후보를 드러낸다. 판정·임계값 없이 재료(근거)만 병기하고, 상한은 질의의
    LIMIT뿐이다. 링크가 0건이어도 인접이 있으면 방출한다.
    """
    project = tmp_path / "project"
    project.mkdir()
    bundle = project / ".okf"
    bundle.mkdir()
    (bundle / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n# C\n\n'
        "* [A](a.md) - a.\n* [B](b.md) - b.\n* [C](c.md) - c.\n"
    )
    (bundle / "a.md").write_text(
        "---\ntype: concept\ndescription: a.\ntags: [shared]\n---\n본문.\n"
    )
    (bundle / "b.md").write_text(
        "---\ntype: concept\ndescription: b.\ntags: [shared]\n---\n본문.\n"
    )
    (bundle / "c.md").write_text(
        "---\ntype: concept\ndescription: c.\nderived_from:\n  - /a.md\n---\n본문.\n"
    )
    (project / ".okf-wiki.json").write_text("{}")
    payload = json.dumps({"tool_input": {"file_path": str(bundle / "a.md")}}).encode()
    res = run_hook(PLUGIN / "scripts", "post-tool-use", project=project, stdin=payload)
    assert res.returncode == 0, res.stderr
    assert sem(res) is not None, "링크가 없어도 축 인접이 있으면 방출한다"
    ctx = sem(res)["hookSpecificOutput"]["additionalContext"]
    assert "b.md(tags=shared)" in ctx, ctx  # 공유 리스트 축 값 — 근거 병기
    assert "c.md(via=derived_from)" in ctx, ctx  # 정초 엣지 인접 — 근거 병기


@needs_uv
def test_e2e_session_start_degrades_over_budget(tmp_path):
    """실엔진 E2E — 예산 초과 번들에서 잘린 목록이 아니라 윤곽이 주입된다(#403).

    실측(실번들 41개념·6,320자·개념 줄 평균 153자)에서 절단 임계는 약 52개념이므로
    여기 80·160개념은 둘 다 초과다. 규모 2배 대조로 **주입 크기가 번들 규모와
    무관하게 유계**임을 잠근다 — 절단이 있던 시절에는 이 크기가 예산에 붙어 있고
    잘려나간 개념 수는 아무 데도 나타나지 않았다.
    """
    gist = (
        "개념 {i}의 핵심 한 줄이다. 실번들 개념 줄은 경로·type·요약을 합쳐 평균 "
        "153자이고, 전환 규모를 실물과 맞추려면 재료도 그 길이여야 한다."
    )
    sizes = {}
    for count in (80, 160):
        project = tmp_path / f"p{count}"
        project.mkdir()
        bundle = project / ".okf"
        bundle.mkdir()
        for i in range(count):
            (bundle / f"c{i}.md").write_text(
                f"---\ntype: Note\ndescription: {gist.format(i=i)}\n---\n\n# c{i}\n"
            )
        (project / ".okf-wiki.json").write_text("{}")
        res = run_hook(PLUGIN / "scripts", "session-start", project=project)
        assert res.returncode == 0, res.stderr
        ctx = sem(res)["hookSpecificOutput"]["additionalContext"]
        assert f"개념 {count} · " in ctx, ctx  # 윤곽이다
        assert "c0.md" not in ctx, ctx  # 목록이 아니다 — 잘린 목록도 아니다
        assert len(ctx) <= 8000, len(ctx)
        sizes[count] = len(ctx)
    assert abs(sizes[160] - sizes[80]) < 50, sizes


def test_format_adjacent_groups_basis_per_path():
    """인접 후보 형식 — path별 근거 묶음. 판정·임계값 문구 없음(재료 병기)."""
    import okf_hooks

    rows = [
        {"path": "b.md", "basis": "tags=x"},
        {"path": "b.md", "basis": "tags=y"},
        {"path": "c.md", "basis": "via=derived_from"},
    ]
    text = okf_hooks._format_adjacent(rows)
    assert "b.md(tags=x, tags=y)" in text, text
    assert "c.md(via=derived_from)" in text, text


def test_format_adjacent_rejects_malformed():
    """비JSON·형식 불량 응답은 무음(빈 문자열) — 스텁·오류문이 제안으로 둔갑하지 않게."""
    import okf_hooks

    assert okf_hooks._format_adjacent([]) == ""
    assert okf_hooks._format_adjacent("b.md") == ""
    assert okf_hooks._format_adjacent([{"nope": 1}]) == ""


def test_session_start_outline_config_gate(henv):
    """`context.outline`이 JSON 리터럴 true면 훅이 `--outline`만으로 부른다(#336).

    주입 형태 전환은 설정 게이트로 시작한다 — 기본은 현행 전량 목록(미설정·타입
    불량은 기존 형태 그대로), 전환은 소비자가 관찰로 검증한 뒤 택한다. 윤곽은
    개념 수 무관 크기라 예산 플래그가 필요 없다.
    """
    (henv.project / ".okf-wiki.json").write_text('{"context": {"outline": true}}')
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("<okf-context>\n개념 1\n</okf-context>\n")
    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    calls = read_and_reset_calls(henv.stub)
    assert "--outline" in calls, calls
    assert "--max-chars" not in calls and "--group-by" not in calls, calls
    # 항상 윤곽이면 조건부 저하는 겹칠 수 없다(엔진이 조합을 사용 오류로 막는다, #403)
    assert "--outline-if-over" not in calls, calls
    assert sem(res) is not None


def test_session_start_outline_type_laxity_keeps_default(henv):
    """`outline: "yes"`(타입 불량)는 기본값 관용 — 기존 전량 주입 형태 유지(정책표)."""
    (henv.project / ".okf-wiki.json").write_text('{"context": {"outline": "yes"}}')
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("<okf-context>\n</okf-context>\n")
    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    calls = read_and_reset_calls(henv.stub)
    assert "--outline " not in calls and not calls.rstrip().endswith("--outline"), calls
    assert "--max-chars 8000" in calls, calls


def test_session_start_auto_degrade_is_the_default(henv):
    """기본 주입은 자동 저하를 켠 채 부른다(#403) — 예산 초과 시 절단 대신 윤곽.

    설정 옵트인이 아니라 기본값이다. 절단은 무음이고(잘린 목록이 전량으로 읽힌다)
    그 오독은 존재 대조를 조용히 부분 대조로 만들기 때문에, 켜기를 사용자 선택으로
    두면 아무것도 닫지 못한다.
    """
    (henv.project / ".okf-wiki.json").write_text('{"context": {"groupBy": "layer"}}')
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("<okf-context>\n개념 1\n</okf-context>\n")
    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0, res.stderr
    calls = read_and_reset_calls(henv.stub)
    # 예산·투영 인자는 그대로 — 저하는 그 위에 얹히는 분기다
    assert "--max-chars 8000 --group-by layer --outline-if-over" in calls, calls


# ── 훅 3종의 오류 정책 통일 (#299) ────────────────────────────────────────────
#
# 전면 `except`가 무음이면 내부 오류(예: study.db 손상)가 "메모리 파일 아님"·
# "capture=off"와 **완전히 같은 신호**가 된다. rc는 그대로 0이다(훅은 세션을 깨지
# 않는다) — 바꾸는 것은 진단의 유무뿐이다.

STUDY_HOOKS = [
    ("study_session.py", b""),
    ("study_hook.py", b'{"tool_input":{"file_path":"/x/MEMORY.md"}}'),
]

# import 시점이 아니라 **run() 안**에서 터뜨린다 — 모듈 import 실패는 전면 except가
# 감싸는 구간 밖이라(모듈 최상단) 다른 경로를 검사하게 된다.
BOOM_SCOPE = """\
def is_memory_path(file_path, payload, project):
    return True


def resolve_capture(project):
    raise RuntimeError("boom")
"""


@pytest.mark.parametrize(("script", "stdin"), STUDY_HOOKS, ids=[s for s, _ in STUDY_HOOKS])
def test_study_hooks_diagnose_on_unexpected_error(tmp_path, script, stdin):
    """study 훅이 예상 외 예외에서 stderr 1줄을 남기고 rc 0을 유지한다."""
    # 훅 스크립트를 tmp로 복사한다 — Python은 **스크립트 디렉토리**를 sys.path[0]에
    # 두므로, PYTHONPATH만 조작하면 진짜 study_scope가 먼저 잡혀 아무것도 안 터진다.
    (tmp_path / "study_scope.py").write_text(BOOM_SCOPE, encoding="utf-8")
    shutil.copy2(PLUGIN / "scripts" / "hooks" / script, tmp_path / script)
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(
            str(PLUGIN / "scripts" / d)
            for d in ("hooks", "vault", "capture", "promote", "explore", "doctor")
        ),
    }
    res = subprocess.run(
        [sys.executable, str(tmp_path / script)],
        input=stdin,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr  # fail-fast 유지 — rc는 바뀌지 않는다
    assert res.stdout == b""
    assert res.stderr != b"", "무음이면 오류와 '해당 없음'이 구분되지 않는다"


def test_okf_hooks_diagnoses_unspawnable_shuttle(henv):
    """셔틀을 spawn조차 못하면 stderr 1줄을 남긴다 — '링크 없음'과 구분되게."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.scripts.parent / "bin" / "okf").unlink()  # OSError(ENOENT) 유발

    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0
    assert res.stdout == b""
    assert "실행 불가".encode() in res.stderr, res.stderr


def test_file_changed_uses_vault_fallback_bundle(henv, tmp_path, monkeypatch):
    """vault 폴백(#91 V3) 사용자에게도 file-changed가 산다.

    번들 소속 검사를 넣으면서 프로젝트 설정만 보면, 설정이 없는 것이 정상인 vault
    폴백 모드에서 감시 중인 파일이 바뀌어도 **영원히 무동작**이 된다 — 오탐을 고치다
    무음을 만드는 꼴이다. 대상 번들은 SessionStart와 같은 해소를 거쳐야 한다.
    """
    vault = tmp_path / "vault"
    (vault / ".okf").mkdir(parents=True)
    (vault / ".okf" / "a.md").write_text("# doc\n", encoding="utf-8")
    (vault / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    # 프로젝트에는 설정이 **없다** — 폴백이 성립하는 조건 그대로
    assert not (henv.project / ".okf-wiki.json").exists()

    payload = json.dumps({"file_path": str(vault / ".okf" / "a.md")}).encode()
    res = run_hook(
        henv.scripts,
        "file-changed",
        project=henv.project,
        stdin=payload,
        stub=henv.stub,
        env_override={"OKF_VAULT_PROJECT": str(vault)},
    )
    assert res.returncode == 0, res.stderr
    assert sem(res) is not None, "vault 폴백에서 무음이면 기능이 사라진 것이다"


def test_post_tool_use_uses_vault_fallback_bundle(henv, tmp_path):
    """vault 폴백(#91 V3) 사용자에게도 역링크 제안이 산다(#327).

    세 훅 중 post-tool-use만 프로젝트 설정으로 대상을 풀어, 설정이 없는 것이 정상인
    폴백 모드에서 vault 번들 파일을 편집해도 영원히 무동작이었다 — 사용자가 보는
    것은 "링크하는 개념이 없다"와 구분되지 않는 무음이다.
    """
    vault = tmp_path / "vault"
    (vault / ".okf").mkdir(parents=True)
    (vault / ".okf" / "a.md").write_text("# doc\n", encoding="utf-8")
    (vault / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    assert not (henv.project / ".okf-wiki.json").exists()  # 폴백 성립 조건 그대로

    (henv.stub / "stdout").write_text("b.md\n")  # 역링크 1건 응답
    payload = json.dumps({"tool_input": {"file_path": str(vault / ".okf" / "a.md")}}).encode()
    res = run_hook(
        henv.scripts,
        "post-tool-use",
        project=henv.project,
        stdin=payload,
        stub=henv.stub,
        env_override={"OKF_VAULT_PROJECT": str(vault)},
    )
    assert res.returncode == 0, res.stderr
    assert sem(res) is not None, "vault 폴백에서 무음이면 기능이 사라진 것이다"
    text = res.stdout.decode("utf-8")
    assert PTU_MSG.format(rel="a.md", links="b.md") in text
    assert f"graph {vault / '.okf'} --linked-to-exact a.md" in read_and_reset_calls(henv.stub)


def test_post_tool_use_emits_no_warning_on_invalid_pointer(henv, tmp_path):
    """무효 포인터에서도 PostToolUse는 무음 — 경고 방출 지점은 SessionStart 하나(§3).

    스코프 해소를 통일하면서 resolve_inject의 warning이 딸려 나오면 안 된다(#327
    회귀 방지 게이트 — 바꾸는 것은 대상 해소이지 경고 정책이 아니다).
    """
    assert not (henv.project / ".okf-wiki.json").exists()
    payload = json.dumps(
        {"tool_input": {"file_path": str(tmp_path / "x" / ".okf" / "a.md")}}
    ).encode()
    res = run_hook(
        henv.scripts,
        "post-tool-use",
        project=henv.project,
        stdin=payload,
        stub=henv.stub,
        env_override={"OKF_VAULT_PROJECT": str(tmp_path / "no-such-vault")},
    )
    assert res.returncode == 0, res.stderr
    assert sem(res) is None  # 경고도 제안도 없다


def test_all_hooks_resolve_scope_via_resolve_inject():
    """세 훅이 같은 해소(resolve_inject)를 거친다 — 한 곳만 고쳐지는 드리프트 방지(#327)."""
    import ast

    tree = ast.parse((PLUGIN / "scripts" / "hooks" / "okf_hooks.py").read_text(encoding="utf-8"))
    remaining = {"hook_session_start", "hook_post_tool_use", "hook_file_changed"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in remaining:
            calls = {
                f"{sub.func.value.id}.{sub.func.attr}"
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Name)
            }
            assert "okf_vault.resolve_inject" in calls, f"{node.name}가 resolve_inject를 안 탄다"
            remaining.discard(node.name)
    assert not remaining, f"훅 미발견: {remaining}"


def test_post_tool_use_ignores_engine_output_when_exit_nonzero(henv):
    """엔진이 **stdout을 내면서 비-0으로 끝나면** 그 출력을 쓰지 않는다.

    판정 축이 "stdout이 비어있지 않음"이면 오류문이 그대로 "링크하는 개념"으로
    컨텍스트에 주입된다(#300). 엔진 오류를 stderr로 통일했지만(그쪽이 정본 수정),
    소비 측도 exit code로 판정해야 다음 오염원에 다시 걸리지 않는다.
    """
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("오류: 번들 디렉터리가 아님: /nope\n")
    (henv.stub / "exit").write_text("2")

    payload = json.dumps(
        {"tool_input": {"file_path": str(henv.project / ".okf" / "a.md")}}
    ).encode()
    res = run_hook(
        henv.scripts, "post-tool-use", project=henv.project, stdin=payload, stub=henv.stub
    )
    assert res.returncode == 0
    assert res.stdout == b"", res.stdout


def test_post_tool_use_asks_engine_for_exact_backlinks():
    """훅이 부분일치가 아니라 **정확 일치** 질의를 쓴다(배선 축 단언)."""
    src = (PLUGIN / "scripts" / "hooks" / "okf_hooks.py").read_text(encoding="utf-8")
    assert '"--linked-to-exact"' in src, "훅이 정확 일치 질의를 쓰지 않는다"
    assert '"--linked-to"' not in src, "부분일치 질의가 남아 있다 — 짧은 파일명이 긴 것을 삼킨다"


# ── uv 부재 가시화 (#353) ────────────────────────────────────────────────────
# 테스트는 항상 uv 경유(`uv run`)로 돌므로 실 PATH엔 uv가 있다 — 부재는 PATH를
# 빈 디렉토리로 갈아끼워 결정론화한다(파이썬·훅 스크립트는 절대경로 spawn이라 무관).


def _opted_in_project(henv):
    (henv.project / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("x", encoding="utf-8")


def test_session_start_uv_missing_warns_when_opted_in(henv, tmp_path):
    """옵트인(번들·설정 존재) + uv 부재 = SessionStart 1줄 경고, 엔진 미호출."""
    _opted_in_project(henv)
    empty = tmp_path / "no-uv-bin"
    empty.mkdir()
    res = run_hook(
        henv.scripts,
        "session-start",
        project=henv.project,
        stub=henv.stub,
        env_override={"PATH": str(empty)},
    )
    assert res.returncode == 0
    payload = sem(res)
    assert payload and "uv" in payload["hookSpecificOutput"]["additionalContext"]
    assert "doctor" in payload["hookSpecificOutput"]["additionalContext"]
    assert read_and_reset_calls(henv.stub) == ""  # 죽을 셔틀을 부르지 않는다


def test_session_start_uv_missing_silent_without_optin(henv, tmp_path):
    """옵트인이 없으면 uv가 없어도 무음 — 고장이 아니라 해당 없음이다."""
    empty = tmp_path / "no-uv-bin"
    empty.mkdir()
    res = run_hook(
        henv.scripts,
        "session-start",
        project=henv.project,
        stub=henv.stub,
        env_override={"PATH": str(empty)},
    )
    assert res.returncode == 0 and sem(res) is None


def test_shuttle_exec_failure_127_emits_stderr_diagnosis(henv):
    """셔틀 127(내부 exec 실패 — uv 부재가 이 경로)은 stderr 1줄 진단을 남긴다."""
    _opted_in_project(henv)
    (henv.stub / "exit").write_text("127")
    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0 and sem(res) is None
    assert "127" in res.stderr.decode("utf-8")


def test_shuttle_other_nonzero_stays_silent(henv):
    """127 아닌 비-제로(엔진 판정 실패 등)는 종전대로 무음 실패 동치."""
    _opted_in_project(henv)
    (henv.stub / "exit").write_text("3")
    res = run_hook(henv.scripts, "session-start", project=henv.project, stub=henv.stub)
    assert res.returncode == 0 and sem(res) is None
    assert res.stderr == b""
