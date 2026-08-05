"""study_usage — 저장고 활용 관측의 무출력·무판정 계약 (#400).

이 훅은 **라벨을 생산하려고** 존재한다. 자문 훅의 유용성을 재려면 "어떤 요청 뒤에
어떤 개념이 열렸는가"가 필요한데 그 데이터가 없었다(실측: 프롬프트 5,187건 중 개념
Read 55건). 그래서 여기서 잠그는 것은 정확도가 아니라 **관측이 관측으로 남는 것**이다:

  ① 두 서브커맨드 모두 stdout 0바이트 — 자문하지 않는다(그건 후속 이슈의 몫이다)
  ② `capture=off`면 완전 무음·무기록 — 관측도 옵트인 사다리에 종속이다
  ③ 집계는 건수와 분포뿐 — 비율·문턱·제안 없음, 정렬은 알파벳순(순위로 읽히지 않게)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import study_inbox
import study_scope
import study_usage

SHIM = Path(study_usage.__file__).resolve().parent.parent.parent / "bin" / "okf-py"


def _env(project):
    import os

    return {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}


def _rt(project):
    return study_scope.resolve_capture(project)["runtime_root"]


def _cfg(project, capture="review"):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": []}}), encoding="utf-8"
    )
    bundle = project / ".okf"
    bundle.mkdir(exist_ok=True)
    return bundle


def _events(project, action):
    from study_store import events_with_action

    return events_with_action(_rt(project), action)


def test_prompt_is_recorded_with_original_text(tmp_path):
    """프롬프트가 **원문 그대로** 남는다 — 해시만 남기면 어휘 실측이 불가능해진다."""
    _cfg(tmp_path)
    study_usage.run_prompt({"prompt": "번들 검증은 strict로 돌리나?", "session_id": "s1"}, tmp_path)
    (event,) = _events(tmp_path, "prompt")
    assert event["text"] == "번들 검증은 strict로 돌리나?"
    assert event["id"] == "s1"


def test_prompt_hook_emits_nothing(tmp_path):
    """관측은 자문하지 않는다 — stdout 0바이트(셔틀 실배선 경유)."""
    _cfg(tmp_path)
    res = subprocess.run(
        [str(SHIM), str(Path(study_usage.__file__)), "prompt"],
        input=json.dumps({"prompt": "무엇이든", "session_id": "s"}).encode(),
        capture_output=True,
        env={**_env(tmp_path)},
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == b""


def test_capture_off_records_nothing(tmp_path):
    """`capture=off`는 완전 무음 — 관측도 옵트인 사다리에 종속이다."""
    _cfg(tmp_path, "off")
    study_usage.run_prompt({"prompt": "x", "session_id": "s"}, tmp_path)
    study_usage.run_load({"tool_input": {"file_path": str(tmp_path / ".okf" / "a.md")}}, tmp_path)
    assert _events(tmp_path, "prompt") == []
    assert _events(tmp_path, "concept_load") == []


def test_concept_load_is_recorded_by_bundle_relative_path(tmp_path):
    """번들 개념 Read만 기록하고 식별자는 번들 상대 경로다."""
    bundle = _cfg(tmp_path)
    (bundle / "dev").mkdir()
    study_usage.run_load(
        {"tool_input": {"file_path": str(bundle / "dev" / "a.md")}, "session_id": "s1"}, tmp_path
    )
    (event,) = _events(tmp_path, "concept_load")
    assert event["id"] == "dev/a.md"
    assert event["session"] == "s1"


def test_non_bundle_and_reserved_reads_are_ignored(tmp_path):
    """번들 밖 파일과 예약 파일은 개념이 아니다 — 기록하면 활용률이 부풀려진다."""
    bundle = _cfg(tmp_path)
    for path in (
        tmp_path / "README.md",
        bundle / "index.md",
        bundle / "log.md",
        bundle / "notes.txt",
    ):
        study_usage.run_load({"tool_input": {"file_path": str(path)}}, tmp_path)
    assert _events(tmp_path, "concept_load") == []


def test_usage_stats_counts_only(tmp_path):
    """집계는 건수와 분포뿐 — 비율·문턱·제안이 없고 분포는 알파벳순이다."""
    bundle = _cfg(tmp_path)
    for i in range(3):
        study_usage.run_prompt({"prompt": f"질문 {i}", "session_id": "s"}, tmp_path)
    # 적은 쪽이 알파벳에서 앞서게 짠다 — 두 정렬이 같은 답을 내면 이 단언은
    # 아무것도 잠그지 않는다(뮤테이션 실증: 건수순으로 바꿔도 통과했다).
    for name in ("z.md", "z.md", "a.md"):
        study_usage.run_load({"tool_input": {"file_path": str(bundle / name)}}, tmp_path)
    stats = study_inbox.usage_stats(_rt(tmp_path))
    assert stats == {
        "prompts": 3,
        "loads": 3,
        "concepts": [{"path": "a.md", "loads": 1}, {"path": "z.md", "loads": 2}],
    }
    # 건수 내림차순이면 z.md(2)가 앞이다. 알파벳순이라 a.md(1)가 먼저다 —
    # 정렬이 곧 순위로 읽히므로 "무엇이 많이 쓰였나"의 판단은 보는 쪽에 남긴다.
    assert [c["path"] for c in stats["concepts"]] == ["a.md", "z.md"]
    assert not any(key in stats for key in ("rate", "ratio", "threshold", "suggestion"))


def test_empty_prompt_and_missing_fields_are_noop(tmp_path):
    """빈 프롬프트·필드 부재는 무동작 — 훅은 어떤 payload에도 세션을 깨지 않는다."""
    _cfg(tmp_path)
    for payload in ({}, {"prompt": ""}, {"prompt": "   "}, {"prompt": None}):
        study_usage.run_prompt(payload, tmp_path)
    for payload in (
        {},
        {"tool_input": {}},
        {"tool_input": None},
        {"tool_input": {"file_path": ""}},
    ):
        study_usage.run_load(payload, tmp_path)
    assert _events(tmp_path, "prompt") == []
    assert _events(tmp_path, "concept_load") == []


def test_unknown_subcommand_exits_one(tmp_path):
    """알 수 없는 서브커맨드는 exit 1 — 훅에서 exit 2는 차단성 특수 의미다(#69)."""
    res = subprocess.run(
        [sys.executable, str(Path(study_usage.__file__)), "nope"],
        input=b"{}",
        capture_output=True,
        env=_env(tmp_path),
    )
    assert res.returncode == 1 and res.stderr != b""
