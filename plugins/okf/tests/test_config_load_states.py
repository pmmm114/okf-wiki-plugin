"""설정 로더의 '부재'와 '파스 실패' 분리 (#301).

`read_json`이 세 상태(부재·깨짐·비객체)를 전부 `None`으로 접었다. 그런데 '파일 없음'과
'파일은 있는데 JSON이 깨짐'은 **처방이 정반대**다 — 전자는 옵트인 안 한 상태라 무음이
정답이고, 후자는 사용자가 고쳐야 할 고장이다.

같은 입력에 세 정책이 공존했다: `okf_hooks`는 stderr 1줄, `study_scaffold`는 exit 2,
`read_json`은 무음 `None`. 그 결과 doctor가 깨진 설정을 **"study 블록 없음"**이라고
진단한다 — 실제로는 블록이 있는데 파일이 파싱되지 않은 것이라, 사용자는 존재하는
설정을 다시 쓰라는 안내를 받는다. 그 사이 캡처는 무음으로 꺼져 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

import okf_vault
import pytest
import study_doctor
import study_scope

BROKEN = '{"study": {"capture": "auto"'  # 닫히지 않은 JSON — 블록은 "있다"
GOOD = {"study": {"capture": "auto"}}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _write(root: Path, body) -> None:
    text = body if isinstance(body, str) else json.dumps(body)
    (root / ".okf-wiki.json").write_text(text, encoding="utf-8")


# --- 로더 자체 ---------------------------------------------------------------


def test_read_json_separates_absent_from_broken(project):
    path = project / ".okf-wiki.json"
    absent = okf_vault.read_json_result(path)
    assert absent["ok"] is False and absent["reason"] == okf_vault.CONFIG_ABSENT

    _write(project, BROKEN)
    broken = okf_vault.read_json_result(path)
    assert broken["ok"] is False and broken["reason"] == okf_vault.CONFIG_BROKEN

    _write(project, GOOD)
    good = okf_vault.read_json_result(path)
    assert good["ok"] is True and good["data"] == GOOD and good["reason"] is None


def test_read_json_keeps_lenient_shape_for_existing_callers(project):
    """기존 `read_json`은 그대로 관용적이다 — 호출자 동작 보존."""
    assert okf_vault.read_json(project / ".okf-wiki.json") is None
    _write(project, BROKEN)
    assert okf_vault.read_json(project / ".okf-wiki.json") is None
    _write(project, GOOD)
    assert okf_vault.read_json(project / ".okf-wiki.json") == GOOD


# --- 세 진입점이 같은 사실을 말한다 -------------------------------------------


def test_scope_status_exposes_config_error(project):
    _write(project, BROKEN)
    status = study_scope.resolve_capture(project)
    assert status["config_error"] is True, status


def test_scope_status_config_error_false_when_absent(project):
    """**부재는 고장이 아니다** — 옵트인 안 한 상태이므로 조용해야 한다."""
    assert study_scope.resolve_capture(project)["config_error"] is False


def test_doctor_does_not_call_broken_config_a_missing_block(project):
    """doctor가 '파스 실패'와 'study 블록 없음'을 **다른 문장**으로 낸다."""
    _write(project, BROKEN)
    joined = "\n".join(study_doctor.capture_trace(str(project)))
    assert "파스 실패" in joined, joined
    assert "블록 없음" not in joined, joined


def test_doctor_still_says_missing_block_when_config_is_valid(project):
    """유효 설정 + study 블록 없음은 여전히 '블록 없음'이다(회귀 방지)."""
    _write(project, {"bundlePath": ".okf"})
    joined = "\n".join(study_doctor.capture_trace(str(project)))
    assert "파스 실패" not in joined, joined


def test_absent_config_stays_silent_everywhere(project):
    """설정 부재는 세 진입점 모두 조용하다 — 이 유닛이 시끄럽게 하는 것은 '파스 실패'뿐이다."""
    joined = "\n".join(study_doctor.capture_trace(str(project)))
    assert "파스 실패" not in joined, joined
    assert study_scope.resolve_capture(project)["config_error"] is False
    assert okf_vault.resolve_inject(project)["scope"] == "none"


def test_resolve_inject_uses_the_same_loader(project):
    """깨진 설정은 `is_file()`만 보고 'project 스코프'라고 하지 않는다.

    `resolve_inject`가 존재 여부만 봤다 — 그러면 훅은 파스 실패로 생략하는데 해소기는
    "프로젝트 스코프다"라고 말해, 두 축이 서로 다른 사실을 보고한다.
    """
    _write(project, BROKEN)
    resolved = okf_vault.resolve_inject(project)
    assert resolved["config_error"] is True, resolved
