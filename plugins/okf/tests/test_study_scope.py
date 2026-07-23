"""study_scope — 캡처 스코프 해소·캡처 입구 판정·vault 캡처 활성 테스트 (#145 U3).

okf_vault 분할로 study 층에 이동한 절반의 테스트다(원 출처 test_okf_vault.py —
#91 V2 매트릭스 번호 유지): #1·#3(단일 스코프), #5(주입 전용 repo), #13(반쪽
상태), #14(자기 위임), #15~#17(캡처 입구), #18(scope 의미 한정), #114(런타임
루트 분리), 마법사 캡처 활성(0.3.0) + CLI(set의 capture_ready·enable-capture).
"""

from __future__ import annotations

import json
from pathlib import Path

import okf_vault
import pytest
import study_scope


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """실환경 포인터·설정이 새어들지 않게 HOME·env를 테스트별로 격리한다."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _vault(tmp_path, config: dict | None) -> Path:
    """유효 vault 골격(git + 설정)을 만든다. config=None이면 설정 파일 생략."""
    vault = tmp_path / "vault-kb"
    (vault / ".git").mkdir(parents=True)
    if config is not None:
        (vault / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return vault


def _project(tmp_path, config: dict | None = None) -> Path:
    project = tmp_path / "work"
    project.mkdir(exist_ok=True)
    if config is not None:
        (project / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return project


# --- resolve_capture (#91 §2 캡처 4단) --------------------------------------


def test_rule2_project_block_wins(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"study": {"capture": "review"}})
    scope = study_scope.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (
        str(project),
        "review",
        "project",
    )


def test_rule2_capture_off_silences_vault_too(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"study": {"capture": "off"}})
    scope = study_scope.resolve_capture(project)
    assert scope["scope"] == "project"
    assert scope["capture"] == "off"  # 명시가 이긴다 — vault이 auto여도 침묵


def test_rule1_scope_vault_delegates(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "off"}})  # 레벨은 블록 값임을 증명
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "vault"}})
    scope = study_scope.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (str(vault), "review", "vault")


def test_rule1_legacy_scope_home_alias_delegates(monkeypatch, tmp_path):
    # #152 R1-1 — 커밋된 구 값 scope:"home"도 위임으로 해석(하위호환), 출력 scope는 "vault"
    vault = _vault(tmp_path, {"study": {"capture": "off"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "home"}})
    scope = study_scope.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (str(vault), "review", "vault")


def test_rule1_scope_vault_without_capture_is_off(monkeypatch, tmp_path):
    # #18 — scope는 목적지 위임 키일 뿐 활성화 키가 아니다
    vault = _vault(tmp_path, {"study": {"capture": "auto"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"study": {"scope": "vault"}})
    scope = study_scope.resolve_capture(project)
    assert scope["capture"] == "off"
    assert scope["scope"] == "vault"  # 목적지는 vault이되 비활성


def test_rule1_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "nowhere"))
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "vault"}})
    scope = study_scope.resolve_capture(project)
    assert scope["target"] is None
    assert "무효" in scope["warning"] and "doctor" in scope["warning"]


def test_rule1_no_pointer_is_silent_noop(tmp_path):
    project = _project(tmp_path, {"study": {"capture": "review", "scope": "vault"}})
    scope = study_scope.resolve_capture(project)
    assert scope["target"] is None
    assert scope["warning"] is None  # 옵트인 안 한 협업자 — 무음


def test_rule3_fallback_to_vault(monkeypatch, tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path)  # 설정 없음
    scope = study_scope.resolve_capture(project)
    assert (scope["target"], scope["capture"], scope["scope"]) == (str(vault), "review", "vault")


def test_rule3_inject_only_project_falls_vault(monkeypatch, tmp_path):
    # #5 — 주입 전용 얇은 설정(study 블록 없음)은 캡처를 vault으로 폴백
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path, {"bundlePath": ".okf"})
    scope = study_scope.resolve_capture(project)
    assert scope["target"] == str(vault)


def test_rule3_half_state_vault_is_normal_silence(monkeypatch, tmp_path):
    # #13 — vault에 study 블록 없음 = 주입 전용 vault(정상, 무경고)
    vault = _vault(tmp_path, {"bundlePath": ".okf"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    scope = study_scope.resolve_capture(_project(tmp_path))
    assert scope["target"] is None
    assert scope["warning"] is None


def test_rule3_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "nowhere"))
    scope = study_scope.resolve_capture(_project(tmp_path))
    assert scope["target"] is None
    assert scope["warning"] is not None


def test_self_delegation_is_harmless(monkeypatch, tmp_path):
    # #14 — vault repo 자신의 scope:"vault"은 자기 자신으로의 위임
    vault = _vault(tmp_path, {"study": {"capture": "review", "scope": "vault"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    scope = study_scope.resolve_capture(vault)
    assert (scope["target"], scope["scope"]) == (str(vault), "vault")


def test_runtime_root_project_scope_is_in_repo(tmp_path):
    # #114 U1 무회귀 — 자기 study 블록이 있는 제3 프로젝트는 런타임이 in-repo 유지
    project = _project(tmp_path, {"study": {"capture": "review"}})
    scope = study_scope.resolve_capture(project)
    assert (scope["scope"], scope["runtime_root"]) == ("project", str(project / ".okf-study"))


def test_runtime_root_fallback_is_user_scope(monkeypatch, tmp_path):
    # #114 U1 — 폴백(자기 파이프라인 없는 곳)의 런타임은 유저 스코프(vault repo에 안 씀)
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    scope = study_scope.resolve_capture(_project(tmp_path))
    assert scope["scope"] == "vault"
    assert scope["runtime_root"] == str(study_scope.user_scope_runtime())


def test_vault_equals_project_routes_runtime_to_user_scope(monkeypatch, tmp_path):
    # #114 U1 — vault=현재 프로젝트여도 런타임은 유저 스코프(vault에 in-repo 런타임 미생성).
    # 승격 대상은 vault, 런타임은 ~/.claude/okf/study — vault은 순수 목적지로 유지된다.
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    scope = study_scope.resolve_capture(vault)
    assert scope["target"] == str(vault) and scope["scope"] == "vault"
    assert scope["runtime_root"] == str(study_scope.user_scope_runtime())


# --- 캡처 입구 판정 (#15~#17) ----------------------------------------------


def test_entrance_legacy_default_path(tmp_path):
    path = "/home/u/.claude/projects/proj/memory/MEMORY.md"
    assert study_scope.is_memory_path(path, {}, tmp_path)


def test_entrance_claude_config_dir(monkeypatch, tmp_path):
    # #16 — 리터럴 `.claude`가 아닌 config dir에서도 캡처 성립
    cfg = tmp_path / "custom-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    path = str(cfg / "projects" / "proj" / "memory" / "MEMORY.md")
    assert study_scope.is_memory_path(path, {}, tmp_path)


def test_entrance_auto_memory_directory_user_settings(monkeypatch, tmp_path):
    # #17 — 공식 autoMemoryDirectory 설정 반영
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    memdir = tmp_path / "my-memory"
    (cfg / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(memdir)}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert study_scope.is_memory_path(str(memdir / "MEMORY.md"), {}, tmp_path)
    assert study_scope.is_memory_path(str(memdir / "sub" / "notes.md"), {}, tmp_path)
    assert not study_scope.is_memory_path(str(tmp_path / "elsewhere" / "x.md"), {}, tmp_path)


def test_entrance_project_settings(monkeypatch, tmp_path):
    project = _project(tmp_path)
    memdir = tmp_path / "proj-memory"
    settings = project / ".claude"
    settings.mkdir()
    (settings / "settings.json").write_text(
        json.dumps({"autoMemoryDirectory": str(memdir)}), encoding="utf-8"
    )
    assert study_scope.is_memory_path(str(memdir / "MEMORY.md"), {}, project)


def test_entrance_transcript_sibling(tmp_path):
    payload = {"transcript_path": str(tmp_path / "projdir" / "session.jsonl")}
    path = str(tmp_path / "projdir" / "memory" / "MEMORY.md")
    assert study_scope.is_memory_path(path, payload, tmp_path)


def test_entrance_vault_pattern_override(monkeypatch, tmp_path):
    vault = _vault(
        tmp_path, {"study": {"capture": "review", "memoryPathPattern": r"/weird-mem/.*\.md$"}}
    )
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    assert study_scope.is_memory_path("/opt/weird-mem/x.md", {}, tmp_path)


def test_entrance_invalid_pattern_tolerated(monkeypatch, tmp_path, capsys):
    vault = _vault(tmp_path, {"study": {"capture": "review", "memoryPathPattern": "("}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    assert not study_scope.is_memory_path("/opt/anything/x.md", {}, tmp_path)
    assert "memoryPathPattern" in capsys.readouterr().err


def test_entrance_non_md_rejected(tmp_path):
    assert not study_scope.is_memory_path("/home/u/.claude/projects/p/memory/m.txt", {}, tmp_path)


# --- vault_capture_state / enable_vault_capture (마법사 캡처 활성, 0.3.0) -------


def test_capture_state_absent(tmp_path):
    vault = _vault(tmp_path, {})  # study 블록 없음
    assert study_scope.vault_capture_state(vault) == "absent"


def test_capture_state_off(tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "off"}})
    assert study_scope.vault_capture_state(vault) == "off"


@pytest.mark.parametrize("level", ["review", "auto"])
def test_capture_state_active(tmp_path, level):
    vault = _vault(tmp_path, {"study": {"capture": level}})
    assert study_scope.vault_capture_state(vault) == "active"


def test_enable_capture_from_absent_activates_without_vault_runtime(tmp_path):
    vault = _vault(tmp_path, {"bundlePath": ".okf"})  # study 블록 없음
    result = study_scope.enable_vault_capture(vault)
    assert result["before"] == "absent"
    assert result["capture"] == "review" and result["changed"] is True
    data = json.loads((vault / ".okf-wiki.json").read_text(encoding="utf-8"))
    assert data["study"]["capture"] == "review"
    assert data["bundlePath"] == ".okf"  # 기존 키 보존
    # #114 U2 — vault엔 런타임(.okf-study)을 만들지 않는다; 런타임은 유저 스코프
    assert not (vault / ".okf-study").exists()
    assert study_scope.user_scope_runtime().is_dir()
    assert result["runtime_root"] == str(study_scope.user_scope_runtime())
    assert study_scope.vault_capture_state(vault) == "active"


def test_enable_capture_from_off_bumps_preserving_handlers(tmp_path):
    vault = _vault(
        tmp_path,
        {"study": {"capture": "off", "handlers": [{"name": "x", "command": "h.py"}]}},
    )
    result = study_scope.enable_vault_capture(vault)
    assert result["before"] == "off" and result["changed"] is True
    data = json.loads((vault / ".okf-wiki.json").read_text(encoding="utf-8"))
    assert data["study"]["capture"] == "review"
    assert data["study"]["handlers"] == [{"name": "x", "command": "h.py"}]  # 보존


def test_enable_capture_does_not_downgrade_auto(tmp_path):
    vault = _vault(tmp_path, {"study": {"capture": "auto"}})
    result = study_scope.enable_vault_capture(vault)  # 기본 level=review
    assert result["changed"] is False
    assert result["capture"] == "auto"  # 격하 금지
    data = json.loads((vault / ".okf-wiki.json").read_text(encoding="utf-8"))
    assert data["study"]["capture"] == "auto"


def test_enable_capture_level_auto(tmp_path):
    vault = _vault(tmp_path, {})
    result = study_scope.enable_vault_capture(vault, level="auto")
    assert result["capture"] == "auto" and result["changed"] is True


def test_enable_capture_idempotent(tmp_path):
    vault = _vault(tmp_path, {})
    study_scope.enable_vault_capture(vault)
    before = (vault / ".okf-wiki.json").read_text(encoding="utf-8")
    second = study_scope.enable_vault_capture(vault)
    assert second["changed"] is False  # 이미 active
    assert (vault / ".okf-wiki.json").read_text(encoding="utf-8") == before


# --- CLI: status의 capture 키 · set의 capture_ready · enable-capture 동사 -----


def test_cli_status_includes_capture(monkeypatch, tmp_path, capsys):
    # 캡처 해소가 필요한 소비처(/study 스코프 해소)는 study_scope status를 쓴다(#145 U3)
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert study_scope.main(["status", str(scratch)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["vault"] == str(vault)
    assert result["capture"]["target"] == str(vault)
    assert result["inject"]["target"] == str(vault)


def test_cli_set_reports_capture_ready(monkeypatch, tmp_path, capsys):
    vault = _vault(tmp_path, {})  # 주입 전용(캡처 absent)
    assert study_scope.main(["set", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] is True and out["capture_ready"] == "absent"


def test_cli_set_capture_ready_active(monkeypatch, tmp_path, capsys):
    vault = _vault(tmp_path, {"study": {"capture": "review"}})
    assert study_scope.main(["set", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["capture_ready"] == "active"


def test_cli_enable_capture_activates(tmp_path, capsys):
    vault = _vault(tmp_path, {})
    assert study_scope.main(["enable-capture", str(vault)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enabled"] is True and out["capture"] == "review"


def test_cli_enable_capture_refuses_non_git(tmp_path, capsys):
    # 유효 vault이 아니면(비-git) 편집 없이 사유 반환 — 반쪽 파이프라인 방지
    bad = tmp_path / "not-git"
    bad.mkdir()
    (bad / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    assert study_scope.main(["enable-capture", str(bad)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enabled"] is False and out["reason"] == okf_vault.INVALID_NOT_GIT
    assert not (bad / ".okf-study").exists()  # 편집 없음


# --- URL 모드 CLI (#153) ------------------------------------------------------


def _managed_clone(url: str, config: dict) -> Path:
    clone = okf_vault.managed_clone_path(okf_vault.canonicalize_url(url))
    (clone / ".git").mkdir(parents=True)
    (clone / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return clone


def test_cli_set_url_defers_capture_ready_when_clone_missing(tmp_path, capsys):
    # clone 미생성이면 설정을 못 읽으므로 capture_ready를 판정하지 않는다(마법사가 clone 후 재조회)
    assert study_scope.main(["set", "git@example.com:o/r.git"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] is True and out["mode"] == "url" and out["clone_exists"] is False
    assert "capture_ready" not in out


def test_cli_set_url_reports_capture_ready_when_clone_present(tmp_path, capsys):
    url = "git@example.com:o/r.git"
    _managed_clone(url, {"study": {"capture": "review"}})
    assert study_scope.main(["set", url]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "url" and out["clone_exists"] is True and out["capture_ready"] == "active"


def test_cli_enable_capture_refuses_managed_clone(tmp_path, capsys):
    # #153 U2-6: URL vault(관리형 clone)에 캡처를 켜면 origin과 diverge → 거부·안내
    url = "git@example.com:o/r.git"
    clone = _managed_clone(url, {})
    assert study_scope.main(["enable-capture", url]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["enabled"] is False and out["reason"] == "managed-clone" and out["guidance"]
    # 관리형 clone의 커밋된 설정은 건드리지 않는다(diverge 방지)
    assert json.loads((clone / ".okf-wiki.json").read_text(encoding="utf-8")) == {}
