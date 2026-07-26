"""디스패치 가능성 판정 단일원천 게이트 (#273, Epic #266 U1).

"핸들러가 배선됐는가"(설정 존재)와 "디스패치가 실제로 가능한가"(경로·git추적·trust)는
**다른 축**이다. 스캐폴드 직후가 그 차이를 드러낸다 — 설정에는 핸들러가 있지만 파일이
아직 미커밋이라 나가지 못한다. 프록시로 판정하면 그 구간이 통과로 읽힌다.

이 파일은 판정이 **한 곳에서만** 나오고, 그 코드 집합이 소비처와 어긋나지 않음을 잠근다.
"""

from __future__ import annotations

import ast
import re
import stat
import subprocess
from pathlib import Path

import study_dispatch
import study_scaffold_handler

CORE = Path(study_dispatch.__file__).resolve().parents[1] / "core"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _repo_with_handler(tmp_path, *, tracked: bool):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    handler = tmp_path / "scripts" / "h.sh"
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    handler.chmod(handler.stat().st_mode | stat.S_IXUSR)
    if tracked:
        _git(tmp_path, "add", "scripts/h.sh")
        _git(tmp_path, "commit", "-m", "add handler")
    return tmp_path


# --- 판정 단일원천 -------------------------------------------------------------


def test_dispatch_has_no_inline_gate():
    """``dispatch``가 판정을 직접 하지 않는다 — 판정은 ``_verdict`` 하나에만 산다.

    두 곳에서 판정하면 한쪽만 고쳐지는 드리프트가 생긴다. 이 repo가 이미 겪은 부류다.
    """
    tree = ast.parse(Path(study_dispatch.__file__).read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "dispatch"
    )
    called = {
        n.func.id
        for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not called & {"resolve_command", "is_git_tracked"}, (
        f"dispatch가 게이트를 직접 부른다: {sorted(called & {'resolve_command', 'is_git_tracked'})}"
    )


def test_verdict_codes_cover_skip_reasons(tmp_path):
    """``_verdict``가 낼 수 있는 code가 ``dispatch``의 skip 사유와 1:1로 대응한다."""
    repo = _repo_with_handler(tmp_path, tracked=False)
    handlers = [{"name": "h", "command": "scripts/h.sh"}]
    verdict = study_dispatch.dispatchability(repo, handlers)[0]
    result = study_dispatch.dispatch(repo, {}, handlers, lambda *_: True)
    assert verdict["code"] == result["skipped"][0]["code"]
    assert verdict["reason"] == result["skipped"][0]["reason"]


def test_skip_reasons_are_byte_preserved(tmp_path):
    """사유 문자열이 바이트 그대로다 — 문서·테스트·소비처가 이 문자열에 걸려 있다."""
    repo = _repo_with_handler(tmp_path, tracked=False)
    outside = study_dispatch.dispatchability(repo, [{"name": "o", "command": "../x.sh"}])[0]
    untracked = study_dispatch.dispatchability(repo, [{"name": "u", "command": "scripts/h.sh"}])[0]
    untrusted = study_dispatch.dispatchability(
        repo, [{"name": "t", "command": "scripts/h.sh"}], lambda *_: False
    )[0]
    assert outside["reason"] == "repo 트리 밖 경로 거부: ../x.sh"
    assert untracked["reason"] == "미추적 경로 거부: scripts/h.sh"
    # trust 축은 추적된 핸들러에서만 평가된다 — 미추적이면 그 앞에서 갈린다
    assert untracked["code"] == "untracked" and untrusted["code"] == "untracked"


def test_trust_axis_evaluated_only_when_check_given(tmp_path):
    """``trust_check=None``이면 trust 축을 평가하지 않는다.

    그때의 ``ok``는 "경로·추적 2축 기준 준비됨"이지 "실행된다"가 아니다 — trust는 머신별
    승인이라 별도 층이고, 마법사는 그것을 따로 안내한다.
    """
    repo = _repo_with_handler(tmp_path, tracked=True)
    handlers = [{"name": "h", "command": "scripts/h.sh"}]
    assert study_dispatch.dispatchability(repo, handlers)[0]["code"] == "ok"
    assert (
        study_dispatch.dispatchability(repo, handlers, lambda *_: False)[0]["code"] == "untrusted"
    )


def test_dispatchability_is_empty_for_no_handlers(tmp_path):
    """핸들러가 없으면 빈 리스트다 — "미배선"은 여기가 아니라 호출자의 조건이다.

    이 성질을 잠그는 이유: 소비처가 "code 집합이 전부"라고 오해하면 미배선 상태가
    판정 밖으로 빠진다(#274가 그 지점을 다룬다).
    """
    assert study_dispatch.dispatchability(_repo_with_handler(tmp_path, tracked=True), []) == []


# --- 프록시가 아닌 판정: 스캐폴드 직후 구간 -------------------------------------


def test_scaffold_window_wired_but_not_dispatchable(tmp_path):
    """스캐폴드 직후 — ``handler_wired``는 참인데 실제로는 나가지 못한다.

    Epic #266이 지목한 '가장 흔한 무음' 구간이다. 설정만 보는 축으로는 이 상태가
    통과로 읽힌다.
    """
    vault = _repo_with_handler(tmp_path, tracked=False)
    (vault / ".okf-wiki.json").write_text(
        '{"study": {"capture": "review", "handlers": [{"name": "h", "command": "scripts/h.sh"}]}}',
        encoding="utf-8",
    )
    state = study_scaffold_handler.writable_state(vault)
    assert state["handler_wired"] is True  # 의미 불변 — 설정 존재 여부
    assert state["ready"] is True  # 의미 불변 — 배선 + capture
    assert state["dispatchable"] is False  # 실제 게이트
    assert [b["code"] for b in state["blockers"]] == ["untracked"]


def test_dispatchable_after_commit(tmp_path):
    """추적되면 dispatchable이 참이 된다(trust 축 제외)."""
    vault = _repo_with_handler(tmp_path, tracked=True)
    (vault / ".okf-wiki.json").write_text(
        '{"study": {"capture": "review", "handlers": [{"name": "h", "command": "scripts/h.sh"}]}}',
        encoding="utf-8",
    )
    state = study_scaffold_handler.writable_state(vault)
    assert state["dispatchable"] is True and state["blockers"] == []


# --- core 경계: _has_handlers 호출처 고정 ---------------------------------------


def test_has_handlers_callers_are_pinned():
    """``_has_handlers``를 부르는 **함수 이름 집합**을 고정한다.

    이 함수는 설정 존재 여부(프록시)일 뿐 디스패치 가능성이 아니다. 소비처가 늘면
    프록시가 판정으로 승격될 위험이 커지므로, 늘어날 때 의식적으로 허용하게 한다.
    개수가 아니라 이름으로 잠그는 이유는 호출처가 옮겨갈 때(#275) 1줄로 끝내기 위함이다.
    """
    # #275(U3)가 라우트 문구를 `_recovery_route`로 뽑으면서 호출처가 그리로 옮겨왔다 —
    # 개수가 아니라 이름으로 잠근 덕에 이 한 줄 갱신으로 끝난다.
    allowed = {"_recovery_route"}
    tree = ast.parse((CORE / "okf_remote.py").read_text(encoding="utf-8"))
    callers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_has_handlers"
    }
    assert callers == allowed, f"_has_handlers 호출처 변경: {sorted(callers)} != {sorted(allowed)}"


def test_has_handlers_docstring_disclaims_gate_role():
    """docstring이 '게이트가 아니다'를 명시한다 — 다음 소비자가 오해하지 않도록."""
    src = (CORE / "okf_remote.py").read_text(encoding="utf-8")
    body = re.search(r"def _has_handlers\(.*?\n(.*?)\"\"\"\n", src, re.DOTALL)
    assert body and "디스패치 가능성" in body.group(1), (
        "_has_handlers docstring에 '디스패치 가능성이 아니다'는 구분이 없다"
    )
