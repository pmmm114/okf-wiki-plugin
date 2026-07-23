"""sh↔py 훅 차동 파리티 하네스 (#69).

동일성 전제가 아니라 케이스별 기대값 테이블이다 — 오류 정책 통일(#69)로
갈라지는 의도적 비파리티는 sh/py 기대 exit code를 따로 명시한다. 전수 목록:

  ① jq 부재: sh만 조용한 무동작 — PATH 조작 없이 재현 불가라 케이스 제외(문서화만).
  ② 깨진 config: session-start는 양쪽 exit 0(sh는 jq 오류가 테스트형 치환이라
     흡수됨), post-tool-use는 sh 5 → py 0.
  ③ 비JSON payload: sh 5 → py 0. 빈 stdin은 양쪽 0(jq는 빈 입력에 무출력 성공).
  ④ config·payload 타입 불량(context 비객체, 배열 payload): sh는 jq 오류
     전파(5) → py는 기본값 관용/무동작 0.
  ⑤ okf 타임아웃: py만 상한(기본 30초, OKF_HOOKS_TIMEOUT로 단축 가능) — 초과 시
     프로세스 그룹 회수 + stderr 1줄 + 실패 동치. sh는 무한 대기(Claude Code
     60초 훅 타임아웃에 맡김). py 단독 테스트로 고정.
  ⑥ 읽기 불가 하위 디렉토리: sh는 pipefail로 JSON 방출 후 exit 1(출력 폐기됨),
     py는 부분 결과 exit 0(의도된 변경). 차동 테스트로 고정(root에서는 skip).
  ⑦ 비정수 숫자 표기(maxChars 1e3 등): jq는 버전 의존 표기(1.7 "1E+3"),
     py는 str(float) — argv 표기 비파리티. 실엔진은 --max-chars가 int라 양쪽 다
     거부·무동작 동치이므로 케이스 제외(문서화만).

나머지 케이스는 sh == py(exit 0, stdout 의미 동일, okf 호출 인자 동일).
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
SH = {
    "session-start": "session_start.sh",
    "post-tool-use": "post_tool_use.sh",
    "file-changed": "file_changed.sh",
}
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
    # 실제 배치 구조 미러링(#145 U5): 레거시 .sh는 scripts/ 루트, py는 scripts/core/
    (scripts / "core").mkdir(parents=True)
    for name in SH.values():
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    # okf_remote는 okf_hooks가 SessionStart URL 신선도로 import하는 core 모듈(#153).
    for name in ["okf_hooks.py", "okf_vault.py", "okf_remote.py"]:
        shutil.copy2(PLUGIN / "scripts" / "core" / name, scripts / "core" / name)
    bin_dir = tmp_path / "plugin" / "bin"
    bin_dir.mkdir()
    (bin_dir / "okf").write_text(STUB_OKF)
    (bin_dir / "okf").chmod(0o755)
    stub = tmp_path / "stub"
    stub.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    return SimpleNamespace(scripts=scripts, stub=stub, project=project)


def run_hook(scripts, kind, hook, *, project, stdin=b"", stub=None, env_override=None, cwd=None):
    if kind == "sh":
        cmd = [str(scripts / SH[hook])]
    else:
        cmd = [sys.executable, str(scripts / "core" / "okf_hooks.py"), hook]
    env = os.environ.copy()
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
# 기대값: sh_rc/py_rc(기본 0/0), out: "none"(기본)|"same", ctx: additionalContext
#   리터럴(양쪽 동일 + 명시값), calls: "same"(기본)|"none"|"skip", stderr:
#   None(무검사)|"empty"|"nonempty"|"boom"(양쪽 포함)
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
    ),
    dict(
        id="ss-inject-문자열-false",
        hook="session-start",
        config={"inject": "false"},
        bundle=["a.md"],
        stub={"stdout": "CTX\n"},
        out="same",
        ctx="CTX",
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
        sh_rc=5,
        py_rc=0,
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
    ),
    dict(
        id="ss-ctx-빈문자열도-출력",  # 빈 컨텍스트여도 성공 경로는 JSON 1개
        hook="session-start",
        config={},
        bundle=["a.md"],
        stub={"stdout": ""},
        out="same",
        ctx="",
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
        sh_rc=5,
        py_rc=0,
        calls="none",
    ),
    dict(
        id="ptu-비JSON-payload",  # 비파리티 ③ — sh 5 → py 0
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload=b"notjson",
        sh_rc=5,
        py_rc=0,
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
        sh_rc=5,
        py_rc=0,
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
        calls_contain="--linked-to sub/doc.md",
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
    ),
    dict(
        id="ptu-file_path-후행개행",  # $(jq -r) 스트립 — rel·--linked-to 인자 등가
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md\n"}},
        stub={"stdout": "b.md\n"},
        out="same",
        ctx=PTU_MSG.format(rel="a.md", links="b.md"),
        calls_contain="--linked-to a.md",
    ),
    dict(
        id="ptu-graph-개행뿐-무출력",  # $(…) 스트립 등가 — rstrip 함정
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        stub={"stdout": "\n"},
    ),
    dict(
        id="ptu-graph-실패-stderr-억제",  # graph는 stderr 억제 + 실패=링크 없음
        hook="post-tool-use",
        config={},
        bundle=["a.md"],
        payload={"tool_input": {"file_path": "{proj}/.okf/a.md"}},
        stub={"stderr": "boom\n", "exit": 3},
        stderr="empty",
    ),
    # ── file-changed ──
    dict(
        id="fc-file_path",  # 설정·번들 검사 없음 — config 부재에서도 동작
        hook="file-changed",
        payload={"file_path": "/x/y.md"},
        out="same",
        ctx=FC_MSG.format(file="/x/y.md"),
        calls="none",
    ),
    dict(
        id="fc-path-폴백",
        hook="file-changed",
        payload={"path": "z.md"},
        out="same",
        ctx=FC_MSG.format(file="z.md"),
        calls="none",
    ),
    dict(
        id="fc-false-후-path-폴백",
        hook="file-changed",
        payload={"file_path": False, "path": "z.md"},
        out="same",
        ctx=FC_MSG.format(file="z.md"),
        calls="none",
    ),
    dict(
        id="fc-숫자-file_path",
        hook="file-changed",
        payload={"file_path": 7},
        out="same",
        ctx=FC_MSG.format(file="7"),
        calls="none",
    ),
    dict(id="fc-키-부재", hook="file-changed", payload={}, calls="none"),
    dict(
        id="fc-file_path-개행뿐",  # $(jq -r) 스트립 후 빈 값 — [ -n ] 실패 등가
        hook="file-changed",
        payload={"file_path": "\n"},
        calls="none",
    ),
    dict(
        id="fc-file_path-후행개행",
        hook="file-changed",
        payload={"file_path": "z.md\n"},
        out="same",
        ctx=FC_MSG.format(file="z.md"),
        calls="none",
    ),
    dict(
        id="fc-비JSON-payload",  # 비파리티 ③ — sh 5 → py 0
        hook="file-changed",
        payload=b"oops",
        sh_rc=5,
        py_rc=0,
        calls="none",
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
def test_parity(henv, case):
    payload = _setup(henv, case)
    sh_res = run_hook(
        henv.scripts, "sh", case["hook"], project=henv.project, stdin=payload, stub=henv.stub
    )
    sh_calls = read_and_reset_calls(henv.stub)
    py_res = run_hook(
        henv.scripts, "py", case["hook"], project=henv.project, stdin=payload, stub=henv.stub
    )
    py_calls = read_and_reset_calls(henv.stub)

    assert sh_res.returncode == case.get("sh_rc", 0), sh_res.stderr
    assert py_res.returncode == case.get("py_rc", 0), py_res.stderr

    out = case.get("out", "none")
    if out == "none":
        assert sh_res.stdout == b""
        assert py_res.stdout == b""
    elif out == "py-only":
        assert sh_res.stdout == b""
        assert sem(py_res) is not None
    else:
        assert sem(sh_res) == sem(py_res)
        assert sem(py_res) is not None
    if "ctx" in case:
        assert sem(py_res)["hookSpecificOutput"]["additionalContext"] == case["ctx"]

    calls = case.get("calls", "same")
    if calls == "none":
        assert sh_calls == "" and py_calls == ""
    elif calls == "same":
        assert sh_calls == py_calls
    if "calls_contain" in case:
        assert case["calls_contain"] in py_calls

    stderr = case.get("stderr")
    if stderr == "empty":
        assert sh_res.stderr == b"" and py_res.stderr == b""
    elif stderr == "nonempty":
        assert sh_res.stderr != b"" and py_res.stderr != b""
    elif stderr == "boom":
        assert b"boom" in sh_res.stderr and b"boom" in py_res.stderr


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

    results = {}
    for kind in ("sh", "py"):
        res = run_hook(henv.scripts, kind, "session-start", project=henv.project, stub=henv.stub)
        assert res.returncode == 0, res.stderr
        results[kind] = sem(res)["hookSpecificOutput"]["watchPaths"]
    expected = sorted(str(bundle / rel) for rel in [".hidden.md", "root.md", "sub/nested.md"])
    assert results["sh"] == results["py"] == expected


def test_watch_paths_symlink_bundle_root(henv):
    """번들 루트 자체가 심링크면 find -P는 하강하지 않는다 — watchPaths []."""
    real = henv.project / "real-bundle"
    real.mkdir()
    (real / "a.md").write_text("# doc\n")
    (henv.project / ".okf").symlink_to(real)
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")

    for kind in ("sh", "py"):
        res = run_hook(henv.scripts, kind, "session-start", project=henv.project, stub=henv.stub)
        assert res.returncode == 0, res.stderr
        assert sem(res)["hookSpecificOutput"]["watchPaths"] == [], kind


def test_pwd_fallback(henv):
    """CLAUDE_PROJECT_DIR 부재·빈 값이면 $PWD로 폴백한다."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("CTX\n")

    for value in (None, ""):
        results = []
        for kind in ("sh", "py"):
            res = run_hook(
                henv.scripts,
                kind,
                "session-start",
                project=henv.project,
                stub=henv.stub,
                env_override={"CLAUDE_PROJECT_DIR": value, "PWD": str(henv.project)},
            )
            assert res.returncode == 0, res.stderr
            results.append(sem(res))
        assert results[0] == results[1] is not None


def test_pwd_stale_reset(henv, tmp_path):
    """stale $PWD(cwd와 다른 디렉토리)는 무시하고 cwd를 쓴다 — bash 기동 시
    PWD 검증 등가. 검증 없이 env PWD를 믿으면 py만 무동작이 된다."""
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.project / ".okf").mkdir()
    (henv.project / ".okf" / "a.md").write_text("# doc\n")
    (henv.stub / "stdout").write_text("CTX\n")
    stale = tmp_path / "stale"
    stale.mkdir()

    results = []
    for kind in ("sh", "py"):
        res = run_hook(
            henv.scripts,
            kind,
            "session-start",
            project=henv.project,
            stub=henv.stub,
            env_override={"CLAUDE_PROJECT_DIR": None, "PWD": str(stale)},
            cwd=str(henv.project),
        )
        assert res.returncode == 0, res.stderr
        results.append(sem(res))
    assert results[0] == results[1] is not None
    assert str(henv.project) in results[1]["hookSpecificOutput"]["watchPaths"][0]


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

    results = {}
    for kind in ("sh", "py"):
        res = run_hook(henv.scripts, kind, "session-start", project=henv.project, stub=henv.stub)
        assert res.returncode == 0, res.stderr
        results[kind] = sem(res)["hookSpecificOutput"]["watchPaths"]
    assert results["sh"] == results["py"]
    assert str(bundle / "� bad.md") in results["py"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root는 디렉토리 권한을 무시")
def test_unreadable_subdir_divergence(henv):
    """비파리티 ⑥ 고정: 읽기 불가 하위 디렉토리 — sh는 JSON 방출 후 pipefail
    exit 1(출력 폐기), py는 부분 결과 exit 0(의도된 변경)."""
    bundle = henv.project / ".okf"
    locked = bundle / "locked"
    locked.mkdir(parents=True)
    (bundle / "a.md").write_text("# doc\n")
    (henv.project / ".okf-wiki.json").write_text("{}")
    (henv.stub / "stdout").write_text("CTX\n")
    locked.chmod(0o000)
    try:
        sh_res = run_hook(henv.scripts, "sh", "session-start", project=henv.project, stub=henv.stub)
        read_and_reset_calls(henv.stub)
        py_res = run_hook(henv.scripts, "py", "session-start", project=henv.project, stub=henv.stub)
    finally:
        locked.chmod(0o755)
    assert sh_res.returncode == 1 and sh_res.stderr != b""
    assert py_res.returncode == 0 and py_res.stderr == b""
    assert sem(py_res)["hookSpecificOutput"]["watchPaths"] == [str(bundle / "a.md")]


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
        "py",
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
    script = PLUGIN / "scripts" / "core" / "okf_hooks.py"
    assert os.access(script, os.X_OK)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
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
    results = {}
    for kind in ("sh", "py"):
        res = run_hook(PLUGIN / "scripts", kind, "session-start", project=project)
        assert res.returncode == 0, res.stderr
        results[kind] = sem(res)
    assert results["sh"] == results["py"]
    hso = results["py"]["hookSpecificOutput"]
    assert hso["additionalContext"].startswith("<okf-context>")
    assert sorted(str(bundle / f) for f in ["a.md", "b.md", "index.md"]) == hso["watchPaths"]


@needs_uv
def test_e2e_post_tool_use_real_engine(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    bundle = _real_bundle(project)
    payload = json.dumps({"tool_input": {"file_path": str(bundle / "b.md")}}).encode()
    results = {}
    for kind in ("sh", "py"):
        res = run_hook(PLUGIN / "scripts", kind, "post-tool-use", project=project, stdin=payload)
        assert res.returncode == 0, res.stderr
        results[kind] = sem(res)
    assert results["sh"] == results["py"] is not None
    assert "a.md" in results["py"]["hookSpecificOutput"]["additionalContext"]
