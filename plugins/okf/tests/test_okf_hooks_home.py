"""okf_hooks session-start 홈 폴백 + okf_home CLI 테스트 (#91 V3).

매트릭스 대응: #9·#19(SessionStart 경고 방출), 주입 3단 규칙, 무회귀(포인터 부재).
파리티 하네스(sh↔py)는 포인터 부재 전제라 기존 표 그대로 유효하다 — 여기서는
폴백이 "켜졌을 때"의 py 단독 동작을 고정한다.
"""

from __future__ import annotations

import json

import okf_home
import okf_hooks
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _make_home(tmp_path, *, inject=None):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    config: dict = {"bundlePath": ".okf"}
    if inject is not None:
        config["inject"] = inject
    (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    bundle = home / ".okf"
    bundle.mkdir()
    (bundle / "index.md").write_text("# index\n", encoding="utf-8")
    return home


def _emitted(capfd):
    out = capfd.readouterr().out
    return json.loads(out) if out.strip() else None


def test_session_start_injects_home_bundle(monkeypatch, tmp_path, capfd):
    home = _make_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    monkeypatch.setattr(okf_hooks, "_run_okf", lambda args, suppress_stderr: "HOME-CTX")
    assert okf_hooks.hook_session_start() == 0
    out = _emitted(capfd)
    hso = out["hookSpecificOutput"]
    assert hso["additionalContext"] == "HOME-CTX"
    assert hso["watchPaths"] == [str(home / ".okf" / "index.md")]


def test_session_start_project_config_wins(monkeypatch, tmp_path, capfd):
    home = _make_home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = tmp_path / "work"
    (project / ".okf").mkdir(parents=True)
    (project / ".okf" / "note.md").write_text("x\n", encoding="utf-8")
    (project / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setattr(okf_hooks, "_run_okf", lambda args, suppress_stderr: "PROJ-CTX")
    assert okf_hooks.hook_session_start() == 0
    hso = _emitted(capfd)["hookSpecificOutput"]
    assert hso["additionalContext"] == "PROJ-CTX"
    assert hso["watchPaths"] == [str(project / ".okf" / "note.md")]


def test_session_start_warns_on_invalid_pointer(monkeypatch, tmp_path, capfd):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    assert okf_hooks.hook_session_start() == 0
    hso = _emitted(capfd)["hookSpecificOutput"]
    assert "홈 포인터 무효" in hso["additionalContext"]


def test_session_start_home_inject_false_is_silent(monkeypatch, tmp_path, capfd):
    home = _make_home(tmp_path, inject=False)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    assert okf_hooks.hook_session_start() == 0
    assert _emitted(capfd) is None


def test_session_start_no_pointer_regression(monkeypatch, tmp_path, capfd):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    assert okf_hooks.hook_session_start() == 0
    assert _emitted(capfd) is None  # 옵트인 안 함 = 현행과 동일한 완전 무음


def test_session_start_injects_from_managed_clone(monkeypatch, tmp_path, capfd):
    # #153: URL 포인터 + 물질화된 관리형 clone → 하류(주입)는 로컬 경로와 동일 파이프라인.
    url = "git@example.com:o/r.git"
    clone = okf_home.managed_clone_path(okf_home.canonicalize_url(url))
    (clone / ".git").mkdir(parents=True)
    (clone / ".okf-wiki.json").write_text(json.dumps({"bundlePath": ".okf"}), encoding="utf-8")
    (clone / ".okf").mkdir()
    (clone / ".okf" / "index.md").write_text("# index\n", encoding="utf-8")
    monkeypatch.setenv(okf_home.POINTER_ENV, url)
    monkeypatch.setenv("OKF_REMOTE_OFFLINE", "1")  # SessionStart fetch는 무동작(git 미필요)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    monkeypatch.setattr(okf_hooks, "_run_okf", lambda args, suppress_stderr: "URL-CTX")
    assert okf_hooks.hook_session_start() == 0
    hso = _emitted(capfd)["hookSpecificOutput"]
    assert hso["additionalContext"] == "URL-CTX"
    assert hso["watchPaths"] == [str(clone / ".okf" / "index.md")]


def test_session_start_url_missing_clone_warns(monkeypatch, tmp_path, capfd):
    # URL 포인터인데 clone 미생성 → SessionStart가 전용 사유로 1줄 경고(무네트워크)
    monkeypatch.setenv(okf_home.POINTER_ENV, "git@example.com:o/r.git")
    monkeypatch.setenv("OKF_REMOTE_OFFLINE", "1")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scratch))
    assert okf_hooks.hook_session_start() == 0
    ctx = _emitted(capfd)["hookSpecificOutput"]["additionalContext"]
    assert "홈 포인터 무효" in ctx and "관리형 clone 미생성" in ctx


# --- okf_home CLI (set/status) ---------------------------------------------


def test_cli_set_writes_pointer(monkeypatch, tmp_path, capsys):
    home = _make_home(tmp_path)
    assert okf_home.main(["set", str(home)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["written"] is True
    pointer = tmp_path / "isolated-home" / ".claude" / "okf" / "home-project"
    assert pointer.read_text(encoding="utf-8").strip() == str(home)
    # 기록 후 폴백이 실제로 발동하는지 (env 없이 파일 경유)
    assert okf_home.home_state() == (str(home), None)


def test_cli_set_rejects_invalid(tmp_path, capsys):
    assert okf_home.main(["set", str(tmp_path / "nowhere")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["written"] is False
    assert result["reason"] == okf_home.INVALID_MISSING
    pointer = tmp_path / "isolated-home" / ".claude" / "okf" / "home-project"
    assert not pointer.exists()  # 무효 대상은 기록하지 않는다


def test_cli_status_reports_resolution(monkeypatch, tmp_path, capsys):
    home = _make_home(tmp_path)
    (home / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert okf_home.main(["status", str(scratch)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["home"] == str(home)
    assert result["inject"]["target"] == str(home)
    # #145 U3 — 캡처 해소는 study 층(study_scope status) 소관: generic status에 없음
    assert "capture" not in result
