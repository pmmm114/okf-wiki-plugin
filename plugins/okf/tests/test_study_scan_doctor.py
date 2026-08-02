"""study scan + okf_doctor 테스트 (#91 V6, #20).

매트릭스 대응: #20(미큐잉 회복 — 결정론 탐지·멱등 재적재·discard 영구 제외·
조건부 회복 안내), #12·#13·#15·#18(doctor 진단 표면).
"""

from __future__ import annotations

import json

import okf_doctor
import okf_vault
import pytest
import study as study_cli
import study_hook
import study_inbox
import study_scope


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
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
    study_inbox.record(_rt(project), study_inbox.content_hash("alpha fact")[:12], "promoted")
    result = study_cli.scan_memory(project)
    assert [c["snippet"] for c in result["unqueued"]] == ["beta fact"]  # 원장 차집합


def test_scan_enqueue_idempotent(tmp_path):
    _memory_file(tmp_path, ["gamma fact"])
    project = _project(tmp_path)
    first = study_cli.scan_memory(project, enqueue=True)
    assert first["enqueued"] and len(study_inbox.list_candidates(_rt(project))) == 1
    second = study_cli.scan_memory(project, enqueue=True)
    assert second["unqueued"] == []  # inbox 차집합 — 재실행 무변화
    assert len(study_inbox.list_candidates(_rt(project))) == 1


def test_scan_discarded_never_returns(tmp_path):
    _memory_file(tmp_path, ["delta fact"])
    project = _project(tmp_path)
    ident = study_inbox.content_hash("delta fact")[:12]
    study_inbox.record(_rt(project), ident, "discarded")
    result = study_cli.scan_memory(project, enqueue=True)
    assert result["unqueued"] == [] and study_inbox.list_candidates(_rt(project)) == []


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


def test_scan_enqueue_updates_file_snapshot(tmp_path):
    # #369 — scan --enqueue는 파일 스냅샷을 동시 갱신한다: 직후 같은 내용의 훅 저장이
    # 완전 무동작이어야 전이가 이중 계수되지 않는다
    path = _memory_file(tmp_path, ["eta fact"])
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    study_cli.scan_memory(project, enqueue=True)
    payload = {"tool_input": {"file_path": str(path), "content": path.read_text(encoding="utf-8")}}
    assert study_hook.run(payload, project) is None
    assert [c["recurrence"] for c in study_inbox.list_candidates(_rt(project))] == [1]


def test_audit_cli_outputs_json(tmp_path, capsys):
    # #371 — 캡처 감사 CLI(관측 전용): 미포착 줄을 코드 분류로 보고하고 상태를 바꾸지 않는다
    _memory_file(tmp_path, ["감사 사실"])
    project = _project(tmp_path)
    assert study_cli.main(["audit", str(project)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scanned_files"] == 1
    assert any(d["code"] == "heading" for d in out["dropped"])  # "# Memory" 헤딩 텍스트 유실 관측
    assert study_inbox.list_candidates(_rt(project)) == []  # 관측은 적재하지 않는다


def test_scan_cli_outputs_json(tmp_path, capsys):
    _memory_file(tmp_path, ["zeta fact"])
    project = _project(tmp_path)
    assert study_cli.main(["scan", str(project)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scanned_files"] == 1 and len(out["unqueued"]) == 1


# --- doctor -----------------------------------------------------------------


def _valid_vault(tmp_path, study=None):
    vault = tmp_path / "vault-kb"
    (vault / ".git").mkdir(parents=True)
    config = {"study": study} if study is not None else {}
    (vault / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return vault


def test_doctor_shows_effective_noise_labels(tmp_path):
    # #370 — 유효 라벨 필터(내장 ∪ 선언)를 doctor가 가시화한다(무음 캡처 억제 방지)
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review", "noiseLabels": ["근거"]}}), encoding="utf-8"
    )
    out = okf_doctor.run(str(project))
    assert "라벨 필터" in out and "근거" in out and "why" in out


def test_doctor_fallback_trace(monkeypatch, tmp_path):
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "vault 폴백" in out and str(vault) in out
    assert "(유효)" in out


def test_doctor_invalid_pointer_recovery_hint(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "nowhere"))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "무효" in out and "[회복]" in out and "study scan" in out


def test_doctor_half_state_note(monkeypatch, tmp_path):
    vault = _valid_vault(tmp_path)  # study 블록 없음
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "주입 전용 vault" in out
    assert "캡처 활성 제안" in out  # 회복 안내 — /okf-init --vault 재실행


def test_doctor_capture_off_note(monkeypatch, tmp_path):
    vault = _valid_vault(tmp_path, {"capture": "off"})  # 블록은 있으나 off
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "vault 캡처 off" in out and "review로" in out


def test_doctor_meaningless_scope_combo(monkeypatch, tmp_path):
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"scope": "vault", "handlers": [{"name": "x", "command": "y"}]}}),
        encoding="utf-8",
    )
    out = okf_doctor.run(str(project))
    assert "무의미 조합" in out and "handlers는 무시" in out


def test_doctor_auto_memory_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "자동 메모리: 비활성" in out


def test_doctor_vault_conformance_bundle_present(monkeypatch, tmp_path):
    # #114 U3 — vault 부합: 번들 존재를 진단
    vault = _valid_vault(tmp_path, {"capture": "review"})
    (vault / ".okf").mkdir()
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "부합: 번들 .okf 있음" in out


def test_doctor_vault_conformance_flags_leaked_runtime(monkeypatch, tmp_path):
    # #114 U3 — vault에 런타임(.okf-study)이 잔존하면 마이그레이션 경고(순수 목적지 위반)
    vault = _valid_vault(tmp_path, {"capture": "review"})
    (vault / ".okf-study").mkdir()
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "런타임 잔존" in out and "migrate" in out


def test_doctor_shows_recent_journal(monkeypatch, tmp_path):
    # #114 U5 — doctor가 이벤트 저널 최근 이력을 보인다
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    study_inbox.append(study_scope.user_scope_runtime(), "저널 한 줄", "MEMORY.md")
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "최근 이력" in out and "capture" in out


def test_doctor_unqueued_recovery_hint(monkeypatch, tmp_path):
    # 미큐잉 집계는 후보(블록/줄) 수가 아니라 **파일 수** — 한 파일의 두 후보도 "파일 1개"
    _memory_file(tmp_path, ["eta fact", "theta fact"])
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "[회복]" in out and "미큐잉 후보가 있는 파일 1개" in out and "--enqueue" in out


def test_doctor_pending_shows_file_count(monkeypatch, tmp_path):
    # U3(#257) — 대기 요약도 파일 단위로 집계한다(선례: 미큐잉 회복 힌트의 파일 수)
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    rt = study_scope.user_scope_runtime()
    study_inbox.append(rt, "fact a", "/mem/one.md")
    study_inbox.append(rt, "fact b", "/mem/two.md")
    assert "2 (파일 2)" in okf_doctor.run(str(_project(tmp_path)))


def test_doctor_shows_recurrence(monkeypatch, tmp_path):
    # U3 #132 — 재등장(recurrence>1) 후보를 doctor 대기 요약에 표시
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    rt = study_scope.user_scope_runtime()
    study_inbox.append(rt, "recurring concept", "MEMORY.md")
    study_inbox.append(rt, "recurring concept", "MEMORY.md")  # 재캡처 → recurrence 2
    assert "재등장" in okf_doctor.run(str(_project(tmp_path)))


def test_doctor_flags_noise_candidates(monkeypatch, tmp_path):
    # #263 — 대기 후보 중 기적재 노이즈(is_noise_snippet)가 있으면 prune 안내 1줄.
    # 모르고 discard하면 원장(공유 원장 write-through 포함)이 노이즈 id로 비가역 오염된다.
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    rt = study_scope.user_scope_runtime()
    study_inbox.append(rt, "fact a", "/mem/one.md")
    study_inbox.append(rt, "--- name: x description: d ---", "/mem/two.md")
    study_inbox.append(rt, "**How to apply:**", "/mem/two.md")
    out = okf_doctor.run(str(_project(tmp_path)))
    line = next(ln for ln in out.splitlines() if "노이즈" in ln)
    # 도달 가능 배치라 실행 명령을 인용한다 — 1차 인용은 안전형(--dry-run 포함)
    assert "노이즈 2건" in line and "prune" in line and "--dry-run" in line
    # 현 위치 인자가 유저 스코프로 해소(vault 폴백)되므로 그대로, 따옴표로 인용(공백 경로)
    assert f'prune "{_project(tmp_path)}"' in line


def test_doctor_no_noise_no_advisory(monkeypatch, tmp_path):
    # 노이즈 0건이면 안내 무출력 — 자문 소음 금지(기존 대기 요약 계약도 그대로)
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    study_inbox.append(study_scope.user_scope_runtime(), "fact a", "/mem/one.md")
    assert "노이즈" not in okf_doctor.run(str(_project(tmp_path)))


def test_doctor_noise_unreachable_scope_count_only(monkeypatch, tmp_path):
    # #263 스코프 함정 — 주입 전용 vault(study 블록 없음)는 어떤 project 인자로도 prune이
    # 유저 스코프에 닿지 않는다(resolve_capture 규칙 3 → runtime_root None). 실행 불가
    # 명령을 인용하면 오도이므로 카운트-온리로 강등한다.
    vault = _valid_vault(tmp_path)  # study 블록 없음
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    study_inbox.append(study_scope.user_scope_runtime(), "--- a/f.py", "/mem/one.md")
    out = okf_doctor.run(str(_project(tmp_path)))
    line = next(ln for ln in out.splitlines() if "노이즈" in ln)
    assert "노이즈 1건" in line and "okf-py" not in line


def test_doctor_noise_delegated_scope_quotes_project_arg(monkeypatch, tmp_path):
    # #263 DA — 위임(scope:"vault") 프로젝트는 vault가 주입 전용이라도 `prune <현위치>`가
    # 규칙 1로 유저 스코프에 닿는다: 카운트-온리 강등이 아니라 실행 명령을 인용해야 한다.
    vault = _valid_vault(tmp_path)  # study 블록 없음(주입 전용)
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"scope": "vault", "capture": "review"}}), encoding="utf-8"
    )
    study_inbox.append(study_scope.user_scope_runtime(), "--- a/f.py", "/mem/one.md")
    out = okf_doctor.run(str(project))
    line = next(ln for ln in out.splitlines() if "노이즈" in ln)
    assert f'prune "{project}"' in line and "--dry-run" in line


def test_doctor_noise_project_scope_line(monkeypatch, tmp_path):
    # project 스코프(in-repo 런타임)의 노이즈도 같은 안내 — project 인자는 현재 repo
    project = _project(tmp_path)
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    study_inbox.append(_rt(project), "--- a/f.py", "/mem/one.md")
    out = okf_doctor.run(str(project))
    line = next(ln for ln in out.splitlines() if "노이즈" in ln)
    assert "노이즈 1건" in line and "prune" in line


def test_doctor_flags_userscope_legacy_markdown(monkeypatch, tmp_path):
    # U5 #134 — 유저 스코프 레거시 markdown 잔존을 doctor가 감지·안내
    vault = _valid_vault(tmp_path, {"capture": "review"})
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(vault))
    us = study_scope.user_scope_runtime()
    us.mkdir(parents=True, exist_ok=True)
    (us / "inbox.md").write_text("# Study Inbox\n", encoding="utf-8")
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "레거시 markdown" in out and "study migrate" in out


def test_doctor_flags_missing_sqlite(monkeypatch, tmp_path):
    # U5 #134 — _sqlite3 부재 파이썬을 doctor가 감지하고 OKF_PYTHON을 안내
    import study_store

    monkeypatch.setattr(study_store, "sqlite3", None)
    out = okf_doctor.run(str(_project(tmp_path)))
    assert "sqlite3" in out and "OKF_PYTHON" in out


def test_bundle_rel_refuses_paths_outside_vault(tmp_path):
    """`bundlePath` 선언이 vault 밖을 가리키면 쓰지 않는다(#266 U5).

    이 값은 진단 문구뿐 아니라 **잔재 열거의 범위**로도 간다. 절대경로·상위 탈출을 그대로
    넘기면 사용자의 vault 밖 작업이 잔재로 열거된다 — 그 목록이 폐기 안내로 흐르는 경로다.
    """
    vault = tmp_path / "kb"
    vault.mkdir()
    for declared in ("/etc", "../outside", "../../x"):
        (vault / ".okf-wiki.json").write_text(
            json.dumps({"bundlePath": declared}), encoding="utf-8"
        )
        assert okf_doctor._bundle_rel(str(vault)) == ".okf", f"탈출 선언을 통과시킴: {declared}"
    (vault / ".okf-wiki.json").write_text(
        json.dumps({"bundlePath": "bundle/okf"}), encoding="utf-8"
    )
    assert okf_doctor._bundle_rel(str(vault)) == "bundle/okf"  # 정상 선언은 존중


def test_bundle_notes_surface_rejected_declaration(tmp_path):
    """탈출 선언을 **거부했다는 사실**이 진단에 나온다(#288 통합 리뷰).

    거부 자체는 옳지만 조용히 `.okf`로 갈아타면 doctor가 "번들 .okf 없음"이라고만 말한다.
    선언을 `../shared`로 써 둔 사용자에게 이건 사실과 다른 안내다 — 도구가 자기 선언을
    무시한 것을 모른 채 존재하지도 않는 `.okf`를 만들러 간다. 이 파일 도입부가 못박은
    "진단 도구가 자기 절반의 결손을 은폐하면 안 된다"와 같은 원리다.
    """
    vault = tmp_path / "kb"
    vault.mkdir()
    (vault / ".okf-wiki.json").write_text(
        json.dumps({"bundlePath": "../shared-bundle"}), encoding="utf-8"
    )
    joined = "\n".join(okf_doctor._bundle_notes(str(vault)))
    assert "../shared-bundle" in joined, "거부된 선언이 진단에 없다"
    assert ".okf" in joined  # 대신 무엇을 쓰는지도 함께


def test_bundle_notes_stay_quiet_for_valid_declaration(tmp_path):
    """정상 선언에는 거부 문구가 붙지 않는다 — 경고는 실제 거부에만."""
    vault = tmp_path / "kb"
    (vault / "bundle" / "okf").mkdir(parents=True)
    (vault / ".okf-wiki.json").write_text(
        json.dumps({"bundlePath": "bundle/okf"}), encoding="utf-8"
    )
    joined = "\n".join(okf_doctor._bundle_notes(str(vault)))
    assert "무시" not in joined and "vault 밖" not in joined
