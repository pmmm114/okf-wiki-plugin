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

import study
import study_dispatch

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
