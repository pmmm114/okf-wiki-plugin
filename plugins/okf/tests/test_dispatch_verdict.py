"""수렴점 판결 게이트 (#274, Epic #266 U2) — 세 미반영 상태가 전부 말해지는가.

`/study`와 `/okf-promote`는 같은 `study.py dispatch`로 수렴한다. 그런데 판결을 붙이는
조건이 trust 하나뿐이라, 가장 흔한 상태(스캐폴드 직후 = 미추적)가 **완전 무음**이었다.

이 파일은 (1) 모든 차단 코드가 실행 가능한 복구 지시를 갖고, (2) 두 커맨드 문서가 그
코드 집합 **전체**에 분기를 가지며, (3) 미배선(`unwired`)이 게이트 밖으로 새지 않음을 잠근다.

(3)이 중요한 이유: `dispatchability`는 핸들러 배열을 순회하므로 배선이 없으면 빈 리스트다.
"함수가 낼 수 있는 코드"만 대조하면 Epic 본체인 미배선 상태가 판정 밖으로 빠진다.
그래서 코드 단일원천을 **모듈 상수**로 두고 그 전체를 대조한다.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest
import study
import study_dispatch
import study_trust

COMMANDS = Path(study.__file__).resolve().parents[2] / "commands"


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _vault(tmp_path, *, handlers, tracked=False):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    if handlers:
        h = tmp_path / "scripts" / "h.sh"
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        h.chmod(h.stat().st_mode | stat.S_IXUSR)
        if tracked:
            subprocess.run(["git", "add", "scripts/h.sh"], cwd=tmp_path, check=True)
            subprocess.run(["git", "commit", "-m", "h"], cwd=tmp_path, capture_output=True)
    (tmp_path / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review", "handlers": handlers}}), encoding="utf-8"
    )
    return tmp_path


def _dispatch(project, capsys):
    study.main(["dispatch", str(project), "--concept-path", "x.md"])
    return _out(capsys)


def _dispatch_trusted(project, capsys):
    """trust까지 승인된 정상 상태에서 디스패치 — 게이트 3축을 전부 통과시킨다."""
    study_trust.main(["approve", str(project)])
    capsys.readouterr()
    return _dispatch(project, capsys)


# --- 모든 차단 코드가 복구 지시를 갖는다 ---------------------------------------


def test_every_blocker_code_has_recovery():
    """복구 지시가 비어 있는 코드가 없다 — 판결은 다음 행동을 알려줘야 한다(#238·#239 관례)."""
    assert study_dispatch.BLOCKERS, "BLOCKERS 상수가 없다"
    for code, recovery in study_dispatch.BLOCKERS.items():
        assert recovery.strip(), f"{code}에 복구 지시가 없다"


def test_blockers_covers_unwired_and_dispatchability_codes():
    """코드 집합이 `dispatchability`가 내는 것 **+ 미배선**을 전부 덮는다.

    미배선은 핸들러 배열이 비어 판정할 대상이 없는 상태라 `dispatchability`가 못 낸다.
    그런데 Epic이 지목한 본체가 그 상태다 — 상수에 함께 두지 않으면 게이트 밖으로 샌다.
    """
    from_verdicts = {
        study_dispatch.CODE_ESCAPE,
        study_dispatch.CODE_UNTRACKED,
        study_dispatch.CODE_UNTRUSTED,
    }
    assert from_verdicts < set(study_dispatch.BLOCKERS)
    assert study_dispatch.CODE_UNWIRED in study_dispatch.BLOCKERS
    assert study_dispatch.CODE_OK not in study_dispatch.BLOCKERS  # ok는 차단이 아니다


# --- 세 상태가 전부 판결을 받는다 ----------------------------------------------


def test_state_unwired_is_judged(tmp_path, capsys):
    """C 상태(미배선) — 지금까지 note는 있었지만 복구 지시가 없었다."""
    out = _dispatch(_vault(tmp_path, handlers=[]), capsys)
    assert out["reflected"] is False
    assert [b["code"] for b in out["blockers"]] == [study_dispatch.CODE_UNWIRED]
    assert out["blockers"][0]["recovery"].strip()
    assert out["note"]  # 사람용 표시는 유지


def test_state_untracked_is_judged(tmp_path, capsys):
    """A 상태(미추적, 스캐폴드 직후) — **완전 무음이던 구간**이다."""
    vault = _vault(tmp_path, handlers=[{"name": "h", "command": "scripts/h.sh"}], tracked=False)
    out = _dispatch(vault, capsys)
    assert out["reflected"] is False
    assert [b["code"] for b in out["blockers"]] == [study_dispatch.CODE_UNTRACKED]
    assert out["blockers"][0]["recovery"].strip()
    assert out["note"], "A 상태가 여전히 무음이다"


def test_unwired_output_is_not_ascii_escaped(tmp_path, capsys):
    """미배선 분기만 `ensure_ascii` 기본값이라 한글이 이스케이프됐다."""
    study.main(["dispatch", str(_vault(tmp_path, handlers=[]))])
    assert "\\u" not in capsys.readouterr().out


def test_reflected_is_false_when_anything_blocked(tmp_path, capsys):
    """차단이 하나라도 있으면 `reflected`는 거짓이다."""
    vault = _vault(tmp_path, handlers=[{"name": "o", "command": "../out.sh"}])
    out = _dispatch(vault, capsys)
    assert out["reflected"] is False and out["blockers"]


# --- 문서가 코드 집합 전체에 분기를 갖는다 --------------------------------------


def test_commands_branch_on_every_blocker_code():
    """두 커맨드 문서가 **모든** 차단 코드를 다룬다 — 코드가 늘면 문서 미갱신이 red.

    문자열이 아니라 코드로 분기하게 한 이유가 이것이다. 자연어 매칭은 문구를 다듬는
    순간 조용히 깨지고(실제로 깨져 있었다) 게이트로 잠글 수도 없다.
    """
    for name in ("study.md", "okf-promote.md"):
        body = (COMMANDS / name).read_text(encoding="utf-8")
        # 코드값 표기(백틱)를 요구한다 — 맨 단어 매칭은 산문에 우연히 걸린다
        # ("can escape this", "left untracked" 등). 그러면 문서를 안 고쳐도 통과한다.
        missing = [c for c in study_dispatch.BLOCKERS if f"`{c}`" not in body]
        assert not missing, f"{name}에 분기 없는 코드: {missing}"


def test_commands_do_not_match_note_strings():
    """문서가 `note` 자연어를 판정 키로 쓰지 않는다 — 표현을 다듬는 일이 고장이 되지 않게."""
    for name in ("study.md", "okf-promote.md"):
        body = (COMMANDS / name).read_text(encoding="utf-8")
        assert "핸들러 미승인" not in body, f"{name}이 note 문자열을 매칭한다"


# --- failed[]도 판결 축이다 (#296) ---------------------------------------------
#
# 배선·커밋·trust 승인이 전부 끝난 정상 상태에서 핸들러가 죽으면 `reflected: false`인데
# `blockers`가 비고 `note`도 없었다 — 두 커맨드 문서가 정의한 복구 지시가 하나도 나가지
# 않는다. 실측 페이로드:
#   {"ran": [], "failed": [{"name": "kb-pr", "code": 1}], "skipped": [], ...}  EXIT=0

_FAIL_BODY = '#!/bin/sh\ncat > /dev/null\necho "push 실패: 오프라인" >&2\nexit 1\n'
_BAD_INTERP = "#!/nonexistent/interp\n"


def _handler(tmp_path, body, *, executable=True, tracked=True):
    """`.okf-wiki.json` + 핸들러 하나를 갖춘 repo. 실행권한·추적 여부를 따로 준다."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    h = tmp_path / "scripts" / "h.sh"
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text(body, encoding="utf-8")
    if executable:
        h.chmod(h.stat().st_mode | stat.S_IXUSR)
    (tmp_path / ".okf-wiki.json").write_text(
        json.dumps(
            {
                "study": {
                    "capture": "review",
                    "handlers": [{"name": "kb", "command": "scripts/h.sh"}],
                }
            }
        ),
        encoding="utf-8",
    )
    if tracked:
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "h"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def _handlers_of(project):
    return json.loads((project / ".okf-wiki.json").read_text(encoding="utf-8"))["study"]["handlers"]


def test_verdict_rejects_non_executable_handler(tmp_path):
    """실행권한 없는 핸들러는 게이트에서 걸려야 한다 — 지금은 3축을 전부 통과한다.

    `docs/adopting-study.md`가 fail-closed를 선언하고 사람에게 `chmod +x`를 시키는데,
    mode 100644 핸들러는 모든 게이트를 지나 `subprocess.run`에서 OSError로 떨어졌다.
    """
    project = _handler(tmp_path, "#!/bin/sh\nexit 0\n", executable=False)
    verdict = study_dispatch._verdict(project, _handlers_of(project)[0], lambda _n, _p: True)
    assert verdict["code"] == study_dispatch.CODE_NOT_EXECUTABLE, verdict


def test_dispatch_promotes_handler_failure_to_blocker(tmp_path, capsys):
    """핸들러 exit≠0이 `blockers`에 코드로 올라오고 복구 지시가 붙는다."""
    project = _handler(tmp_path, _FAIL_BODY)
    out = _dispatch_trusted(project, capsys)
    assert out["reflected"] is False
    assert [b["code"] for b in out["blockers"]] == [study_dispatch.CODE_HANDLER_FAILED], out
    assert out["blockers"][0]["recovery"].strip()
    assert out.get("note", "").strip(), "복구 지시가 사람용 한 줄로도 나가야 한다"


@pytest.mark.parametrize("body", [_FAIL_BODY, _BAD_INTERP])
def test_failed_items_have_uniform_shape(tmp_path, capsys, body):
    """비0 종료와 실행 불가(OSError)가 같은 키 집합을 낸다 — 소비처가 분기로 갈리지 않게."""
    project = _handler(tmp_path, body)
    out = _dispatch_trusted(project, capsys)
    assert out["failed"], out
    for item in out["failed"]:
        assert {"name", "code", "reason"} <= set(item), item
        assert item["code"] == study_dispatch.CODE_HANDLER_FAILED


def test_handler_output_is_preserved(tmp_path, capsys):
    """핸들러가 남긴 통지가 판결에 남는다 — `capture_output`이 삼키면 원인이 사라진다."""
    project = _handler(tmp_path, _FAIL_BODY)
    out = _dispatch_trusted(project, capsys)
    assert "push 실패: 오프라인" in out["failed"][0].get("output", ""), out["failed"]
