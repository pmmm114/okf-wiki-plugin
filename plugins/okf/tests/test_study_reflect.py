"""study_reflect — 턴 경계·실패/거부 구분·capture 분기·fail-fast 테스트.

픽스처의 레코드 모양과 문구는 **실측**에서 왔다(Claude Code 2.1.220 트랜스크립트):
``tool_result``의 ``content``는 문자열이고, 거부는 하네스 고정 문구로, 실패는
``Exit code N``으로 남는다. 훅은 성패를 문자열로 추정하지 않고 ``is_error``만 본다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import study_reflect

SCRIPT = Path(study_reflect.__file__)
# 직접 spawn 대신 실배선(hooks.json)과 동일하게 bin/okf-py 셔틀 경유 — 셔틀이
# scripts/core·scripts/study를 PYTHONPATH로 노출한다(#145 U5 교차 디렉토리 import)
SHIM = SCRIPT.resolve().parent.parent.parent / "bin" / "okf-py"

TURN = "ba8d8604-3e2d-48d0-9f74-e038656f51de"
OTHER = "0d6b534b-a07a-4ac7-ae78-62b07b5fb171"

REJECTED = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected "
    "(eg. if it was a file edit, the new_string was NOT written to the file)"
)
FAILED = "Exit code 1\nls: /nonexistent-xyz-123: No such file or directory"


def _cfg(project, capture):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": []}}), encoding="utf-8"
    )


def _err(text, ident=None):
    return {"type": "tool_result", "is_error": True, "content": text, "tool_use_id": ident}


def _ok(text="fine"):
    return {"type": "tool_result", "content": text, "tool_use_id": "toolu_ok"}


def _rec(prompt_id, *items):
    return {"type": "user", "promptId": prompt_id, "message": {"content": list(items)}}


def _transcript(tmp_path, *records, name="t.jsonl"):
    path = tmp_path / name
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return path


def _payload(transcript, prompt_id=TURN, **extra):
    return {"transcript_path": str(transcript), "prompt_id": prompt_id, **extra}


def test_counts_failure_and_rejection_of_this_turn(tmp_path):
    _cfg(tmp_path, "review")
    transcript = _transcript(
        tmp_path,
        _rec(TURN, _err(FAILED, "toolu_a")),
        _rec(TURN, _err(REJECTED, "toolu_b")),
        _rec(TURN, _err(FAILED, "toolu_c")),
    )
    assert study_reflect.count_turn_errors(transcript, TURN) == (2, 1)
    message = study_reflect.run(_payload(transcript), tmp_path)
    assert message and "2건" in message and "1건" in message


def test_previous_turn_is_not_counted(tmp_path):
    """턴 경계는 promptId다 — 이전 턴의 실패를 다시 세면 매 턴 같은 잔소리가 된다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(
        tmp_path,
        _rec(OTHER, _err(FAILED, "toolu_old1")),
        _rec(OTHER, _err(REJECTED, "toolu_old2")),
        _rec(TURN, _err(FAILED, "toolu_new")),
    )
    assert study_reflect.count_turn_errors(transcript, TURN) == (1, 0)


def test_successful_results_are_not_counted(tmp_path):
    """판정은 is_error 표식만 본다 — 출력 문자열로 성패를 추정하지 않는다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(
        tmp_path,
        _rec(TURN, _ok("Exit code 1 이라는 글자가 든 정상 출력")),
        _rec(TURN, _ok()),
    )
    assert study_reflect.count_turn_errors(transcript, TURN) == (0, 0)
    assert study_reflect.run(_payload(transcript), tmp_path) is None


def test_unknown_error_text_folds_into_failure(tmp_path):
    """거부 문구를 못 알아보면 실패로 접는다 — 과소 분류는 안전, 누락은 아니다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(tmp_path, _rec(TURN, _err("Error calling tool 'x': boom", "toolu_z")))
    assert study_reflect.count_turn_errors(transcript, TURN) == (1, 0)


def test_same_tool_use_counted_once(tmp_path):
    _cfg(tmp_path, "review")
    transcript = _transcript(
        tmp_path,
        _rec(TURN, _err(FAILED, "toolu_dup")),
        _rec(TURN, _err(FAILED, "toolu_dup")),
    )
    assert study_reflect.count_turn_errors(transcript, TURN) == (1, 0)


def test_missing_prompt_id_counts_nothing(tmp_path):
    """경계를 못 그으면 세지 않는다 — 세션 전체를 싸잡는 것보다 조용한 편이 낫다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(tmp_path, _rec(TURN, _err(FAILED, "toolu_a")))
    assert study_reflect.count_turn_errors(transcript, None) == (0, 0)
    assert study_reflect.run(_payload(transcript, prompt_id=None), tmp_path) is None


def test_capture_off_is_silent(tmp_path):
    _cfg(tmp_path, "off")
    transcript = _transcript(tmp_path, _rec(TURN, _err(FAILED, "toolu_a")))
    assert study_reflect.run(_payload(transcript), tmp_path) is None


def test_stop_hook_active_suppresses_reemission(tmp_path):
    """되돌림이 되돌림을 부르지 않게 — 한 턴에 한 번으로 묶는다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(tmp_path, _rec(TURN, _err(FAILED, "toolu_a")))
    assert study_reflect.run(_payload(transcript, stop_hook_active=True), tmp_path) is None


def test_missing_transcript_is_silent(tmp_path):
    _cfg(tmp_path, "review")
    payload = {"transcript_path": str(tmp_path / "gone.jsonl"), "prompt_id": TURN}
    assert study_reflect.run(payload, tmp_path) is None


def test_broken_lines_are_skipped(tmp_path):
    """트랜스크립트에 깨진 줄이 섞여도 나머지를 센다 — 훅은 세션을 깨지 않는다."""
    _cfg(tmp_path, "review")
    path = tmp_path / "t.jsonl"
    path.write_text(
        "{not json\n"
        + json.dumps(_rec(TURN, _err(FAILED, "toolu_a")), ensure_ascii=False)
        + "\n[]\n",
        encoding="utf-8",
    )
    assert study_reflect.count_turn_errors(path, TURN) == (1, 0)


def test_chunk_boundary_does_not_lose_records(tmp_path):
    """청크 경계에서 반토막 난 줄도 이어 붙여 살린다 — 버리면 그 레코드가 통째로 사라진다."""
    _cfg(tmp_path, "review")
    records = [_rec(TURN, _err(FAILED, f"toolu_{i}")) for i in range(30)]
    transcript = _transcript(tmp_path, *records)
    lines = [ln for ln in study_reflect._reversed_lines(transcript, chunk=64) if ln.strip()]
    assert len(lines) == len(records)  # 레코드보다 작은 청크로도 전부 살아난다
    assert all(json.loads(line) for line in lines)  # 모두 온전한 JSON
    assert json.loads(lines[0])["message"]["content"][0]["tool_use_id"] == "toolu_29"  # 역순


def test_large_turn_is_counted_whole(tmp_path):
    """턴 안의 실패는 개수와 무관하게 전부 센다 — 앞부분을 잘라 놓치지 않는다.

    실측 근거: 사용자 트랜스크립트 280세션 중 4건에 2MB를 넘는 턴이 있었고 최대 5.65MB다.
    고정 2MB 창이었다면 그 턴의 64%를 못 봤다.
    """
    _cfg(tmp_path, "review")
    records = [_rec(TURN, _err(FAILED, f"toolu_{i}")) for i in range(200)]
    transcript = _transcript(tmp_path, *records)
    assert study_reflect.count_turn_errors(transcript, TURN) == (200, 0)


def test_scan_cap_degrades_to_undercount(tmp_path, monkeypatch):
    """상한에 걸리면 폭주 대신 과소 집계로 끝난다 — 손상된 트랜스크립트의 안전판이다."""
    monkeypatch.setattr(study_reflect, "_MAX_SCAN_BYTES", 400)
    _cfg(tmp_path, "review")
    records = [_rec(TURN, _err(FAILED, f"toolu_{i}")) for i in range(50)]
    transcript = _transcript(tmp_path, *records)
    failed, _ = study_reflect.count_turn_errors(transcript, TURN)
    assert 0 < failed < 50


def test_scan_stops_at_turn_boundary(tmp_path):
    """이전 턴이 아무리 길어도 경계에서 멈춘다 — 읽는 양은 턴 크기에 비례한다."""
    _cfg(tmp_path, "review")
    old = [_rec(OTHER, _err(FAILED, f"toolu_old{i}")) for i in range(500)]
    transcript = _transcript(tmp_path, *(old + [_rec(TURN, _err(FAILED, "toolu_new"))]))
    assert study_reflect.count_turn_errors(transcript, TURN) == (1, 0)


def test_hook_spawn_emits_stop_context(tmp_path):
    """실배선과 같은 경로(bin/okf-py)로 spawn — Stop 이벤트명으로 되돌린다."""
    _cfg(tmp_path, "review")
    transcript = _transcript(tmp_path, _rec(TURN, _err(FAILED, "toolu_a")))
    proc = subprocess.run(
        [str(SHIM), str(SCRIPT)],
        input=json.dumps(_payload(transcript)),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0
    emitted = json.loads(proc.stdout)
    assert emitted["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert emitted["hookSpecificOutput"]["additionalContext"]


def test_hook_spawn_survives_garbage_stdin(tmp_path):
    proc = subprocess.run(
        [str(SHIM), str(SCRIPT)],
        input="not json at all",
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_hook_is_wired_as_stop_event():
    """hooks.json 배선 — 이벤트명이 갈리면 훅은 조용히 안 불린다."""
    hooks = json.loads(
        (SCRIPT.resolve().parent.parent.parent / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    entries = hooks["hooks"]["Stop"]
    commands = [h for entry in entries for h in entry["hooks"]]
    assert any(SCRIPT.name in arg for h in commands for arg in h["args"])
    # exec form(#108) — command는 따옴표·공백 없는 단일 실행파일이어야 spawn된다
    for hook in commands:
        assert " " not in hook["command"] and '"' not in hook["command"]
