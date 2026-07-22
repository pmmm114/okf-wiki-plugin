"""study scan + okf_doctor 테스트 (#91 V6, #20).

매트릭스 대응: #20(미큐잉 회복 — 결정론 탐지·멱등 재적재·discard 영구 제외·
조건부 회복 안내), #12·#13·#15·#18(doctor 진단 표면).
"""

from __future__ import annotations

import json

import okf_doctor
import okf_home
import okf_inbox
import pytest
import study as study_cli
import study_hook


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    # 기본형 글롭이 실환경을 훑지 않게 config dir도 격리
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))


def _memory_file(tmp_path, lines: list[str]):
    memory = tmp_path / "cfg" / "projects" / "proj" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    path = memory / "MEMORY.md"
    path.write_text("# Memory\n\n" + "\n".join(f"* {line}" for line in lines) + "\n", "utf-8")
    return path


def _project(tmp_path):
    project = tmp_path / "work"
    project.mkdir(exist_ok=True)
    return project


# --- scan -------------------------------------------------------------------


def _rt(project):
    # scan은 스코프 미해소 시 in-repo 런타임으로 폴백한다(#114) — 원장·인박스가 사는 곳
    return project / ".okf-study"


def test_scan_detects_unqueued(tmp_path):
    _memory_file(tmp_path, ["alpha fact", "beta fact"])
    project = _project(tmp_path)
    okf_inbox.record(_rt(project), okf_inbox.content_hash("alpha fact")[:12], "promoted")
    result = study_cli.scan_memory(project)
    assert [c["snippet"] for c in result["unqueued"]] == ["beta fact"]  # 원장 차집합


def test_scan_enqueue_idempotent(tmp_path):
    _memory_file(tmp_path, ["gamma fact"])
    project = _project(tmp_path)
    first = study_cli.scan_memory(project, enqueue=True)
    assert first["enqueued"] and len(okf_inbox.list_candidates(_rt(project))) == 1
    second = study_cli.scan_memory(project, enqueue=True)
    assert second["unqueued"] == []  # inbox 차집합 — 재실행 무변화
    assert len(okf_inbox.list_candidates(_rt(project))) == 1


def test_scan_discarded_never_returns(tmp_path):
    _memory_file(tmp_path, ["delta fact"])
    project = _project(tmp_path)
    ident = okf_inbox.content_hash("delta fact")[:12]
    okf_inbox.record(_rt(project), ident, "discarded")
    result = study_cli.scan_memory(project, enqueue=True)
    assert result["unqueued"] == [] and okf_inbox.list_candidates(_rt(project)) == []


def test_scan_hash_aligns_with_hook_capture(tmp_path):
    # 훅이 잡은 라인은 scan에서 미큐잉으로 재등장하지 않아야 한다(해시 정렬)
    path = _memory_file(tmp_path, ["epsilon fact"])
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    payload = {"tool_input": {"file_path": str(path), "content": "* epsilon fact\n"}}
    assert study_hook.run(payload, project)
    result = study_cli.scan_memory(project)
    assert result["unqueued"] == []


def test_scan_cli_outputs_json(tmp_path, capsys):
    _memory_file(tmp_path, ["zeta fact"])
    project = _project(tmp_path)
    assert study_cli.main(["scan", str(project)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scanned_files"] == 1 and len(out["unqueued"]) == 1


# --- doctor -----------------------------------------------------------------


def _valid_home(tmp_path, study=None):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    config = {"study": study} if study is not None else {}
    (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def test_doctor_fallback_trace(monkeypatch, tmp_path):
    home = _valid_home(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "홈 폴백" in out and str(home) in out
    assert "(유효)" in out


def test_doctor_invalid_pointer_recovery_hint(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "무효" in out and "[회복]" in out and "study scan" in out


def test_doctor_half_state_note(monkeypatch, tmp_path):
    home = _valid_home(tmp_path)  # study 블록 없음
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "주입 전용 홈" in out
    assert "캡처 활성 제안" in out  # 회복 안내 — /okf-init --home 재실행


def test_doctor_capture_off_note(monkeypatch, tmp_path):
    home = _valid_home(tmp_path, {"capture": "off"})  # 블록은 있으나 off
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "홈 캡처 off" in out and "review로" in out


def test_doctor_meaningless_scope_combo(monkeypatch, tmp_path):
    home = _valid_home(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"scope": "home", "handlers": [{"name": "x", "command": "y"}]}}),
        encoding="utf-8",
    )
    out = okf_doctor.run(str(project))
    assert "무의미 조합" in out and "handlers는 무시" in out


def test_doctor_auto_memory_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "자동 메모리: 비활성" in out


def test_doctor_shows_recent_journal(monkeypatch, tmp_path):
    # #114 U5 — doctor가 이벤트 저널 최근 이력을 보인다
    home = _valid_home(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.append(okf_home.user_scope_runtime(), "저널 한 줄", "MEMORY.md")
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "최근 이력" in out and "capture" in out


def test_doctor_unqueued_recovery_hint(monkeypatch, tmp_path):
    _memory_file(tmp_path, ["eta fact"])
    home = _valid_home(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "[회복]" in out and "미큐잉 후보 1개" in out and "--enqueue" in out
