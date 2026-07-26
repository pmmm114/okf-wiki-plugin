"""study.py 오케스트레이션 CLI + study_session 나즈 테스트 (S5, #77)."""

from __future__ import annotations

import json
import subprocess

import okf_vault
import pytest
import study
import study_dispatch
import study_inbox
import study_session


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # vault 포인터·설정이 새어들지 않게 격리 → 바 프로젝트는 in-repo 런타임으로 해소
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _rt(project):
    """CLI가 쓰는 런타임 루트 — 바 프로젝트(설정·vault 없음)는 in-repo <repo>/.okf-study(#114)."""
    return project / ".okf-study"


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def _cfg(project, capture, handlers):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": handlers}}), encoding="utf-8"
    )


def test_list_outputs_candidates(tmp_path, capsys):
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    study.main(["list", str(tmp_path)])
    out = _out(capsys)
    assert len(out) == 1
    assert out[0]["snippet"] == "a"


def test_resolve_records_and_drops(tmp_path, capsys):
    ident = study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    study.main(
        ["resolve", str(tmp_path), "--id", ident, "--status", "promoted", "--ref", ".okf/x.md"]
    )
    out = _out(capsys)
    assert out["dropped"] == [ident]
    assert out["id"] == ident  # 단일-호출 출력의 "id" 키는 하위호환 계약(DA #262)
    assert study_inbox.is_resolved(_rt(tmp_path), ident)
    assert study_inbox.list_candidates(_rt(tmp_path)) == []


def test_resolve_by_source_batch(tmp_path, capsys):
    # U4(#258): 파일 그룹 일괄 드레인 — 파일 하나 처리에 후보 수만큼 resolve를
    # 반복 호출하던 격차(실증 A5: 중앙값 5~8회) 해소. 원장·저널은 id별 계약 유지.
    i1 = study_inbox.append(_rt(tmp_path), "a", "/mem/one.md")
    i2 = study_inbox.append(_rt(tmp_path), "b", "/mem/one.md")
    i3 = study_inbox.append(_rt(tmp_path), "c", "/mem/two.md")
    study.main(["resolve", str(tmp_path), "--source", "/mem/one.md", "--status", "discarded"])
    out = _out(capsys)
    assert set(out["ids"]) == {i1, i2} and set(out["dropped"]) == {i1, i2}
    assert out["id"] is None  # 배치 출력의 "id"는 null — 단일 호출만 값(하위호환)
    assert study_inbox.is_resolved(_rt(tmp_path), i1)
    assert study_inbox.is_resolved(_rt(tmp_path), i2)
    assert [c["id"] for c in study_inbox.list_candidates(_rt(tmp_path))] == [i3]


def test_resolve_by_source_applies_same_sanitize(tmp_path, capsys):
    # 저장 source는 _sanitize 통과본 — CLI 인자도 동일 정규화 후 정확 일치로 매칭
    ident = study_inbox.append(_rt(tmp_path), "a", "/mem/my  notes.md")
    study.main(["resolve", str(tmp_path), "--source", "/mem/my  notes.md", "--status", "discarded"])
    assert _out(capsys)["dropped"] == [ident]


def test_resolve_by_source_no_match_fails_visibly(tmp_path, capsys):
    # 매칭 0건은 무음 성공이 아니라 가시적 실패 — 현존 source 목록을 보여 오타를 드러낸다
    study_inbox.append(_rt(tmp_path), "a", "/mem/one.md")
    rc = study.main(["resolve", str(tmp_path), "--source", "/mem/gone.md", "--status", "discarded"])
    out = _out(capsys)
    assert rc == 1 and out["sources"] == ["/mem/one.md"]
    assert study_inbox.list_candidates(_rt(tmp_path))  # 아무것도 드레인되지 않았다


def test_resolve_multiple_ids_merge_promotion(tmp_path, capsys):
    # 다중 --id + 단일 --ref = "N후보 → 1개념 병합 승격" — id별 원장·저널 기록은 유지
    i1 = study_inbox.append(_rt(tmp_path), "a", "/mem/one.md")
    i2 = study_inbox.append(_rt(tmp_path), "b", "/mem/one.md")
    study.main(
        ["resolve", str(tmp_path), "--id", i1, "--id", i2, "--status", "promoted"]
        + ["--ref", ".okf/x.md", "--layer", "information"]
    )
    out = _out(capsys)
    assert set(out["ids"]) == {i1, i2}
    assert study_inbox.is_resolved(_rt(tmp_path), i1)
    assert study_inbox.is_resolved(_rt(tmp_path), i2)
    promoted = [e for e in study_inbox.read_journal(_rt(tmp_path)) if e["action"] == "promoted"]
    assert len(promoted) == 2 and all(e.get("ref") == ".okf/x.md" for e in promoted)


def test_resolve_id_and_source_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        study.main(
            ["resolve", str(tmp_path), "--id", "x", "--source", "/m.md", "--status", "promoted"]
        )


def test_clear_discards_all(tmp_path, capsys):
    i1 = study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    i2 = study_inbox.append(_rt(tmp_path), "b", "s", date="2026-07-19")
    study.main(["clear", str(tmp_path)])
    assert set(_out(capsys)["discarded"]) == {i1, i2}
    assert study_inbox.is_resolved(_rt(tmp_path), i1)
    assert study_inbox.is_resolved(_rt(tmp_path), i2)
    assert study_inbox.list_candidates(_rt(tmp_path)) == []


def test_prune_drops_noise_without_ledger(tmp_path, capsys):
    # U2(#256): 기적재 노이즈는 원장 기록 없는 drop으로 정리 — discard는 노이즈 id로
    # 원장(공유 원장 포함)을 비가역 오염시키고, 재유입은 추출 필터가 이미 차단한다.
    noise = study_inbox.append(_rt(tmp_path), "--- name: x description: d ---", "M.md")
    label = study_inbox.append(_rt(tmp_path), "**How to apply:**", "M.md")
    real = study_inbox.append(_rt(tmp_path), "real fact", "M.md")
    study.main(["prune", str(tmp_path)])
    out = _out(capsys)
    assert set(out["pruned"]) == {noise, label}
    assert [c["id"] for c in study_inbox.list_candidates(_rt(tmp_path))] == [real]
    assert not study_inbox.is_resolved(_rt(tmp_path), noise)  # 원장 무기록
    events = study_inbox.read_journal(_rt(tmp_path))
    assert events[-1]["action"] == "prune" and events[-1]["count"] == 2  # 집계 1건


def test_prune_dry_run_lists_matches_without_drop(tmp_path, capsys):
    # #263: --dry-run은 매치 원문만 보인다 — drop·저널 무기록. is_noise_snippet은 텍스트
    # 근사라 `--- ` 접두 실사실·diff 헤더 인용이 섞일 수 있어, 삭제 전 검토가 1차 시민이다.
    # 출력 키는 실행 모드("pruned")와 분리 — 매치 0건 실행({"pruned": []})과 혼동 방지.
    noise = study_inbox.append(_rt(tmp_path), "--- a/src/main.py", "M.md")
    real = study_inbox.append(_rt(tmp_path), "real fact", "M.md")
    study.main(["prune", str(tmp_path), "--dry-run"])
    out = _out(capsys)
    assert out["dry_run"] is True and "pruned" not in out
    assert [m["id"] for m in out["matches"]] == [noise]
    assert out["matches"][0]["snippet"] == "--- a/src/main.py"  # 오폭 검토용 원문
    assert {c["id"] for c in study_inbox.list_candidates(_rt(tmp_path))} == {noise, real}
    assert all(e["action"] != "prune" for e in study_inbox.read_journal(_rt(tmp_path)))


def test_dispatch_no_handlers(tmp_path, capsys):
    """미배선 판결 — 계약은 기계 필드다(#266 U2).

    이전에는 `note == "핸들러 없음"` 정확일치였다. 그 단언은 문구를 잠글 뿐 "사용자가
    무엇을 해야 하는지"는 검사하지 못했고, 실제로 그 자리에 복구 지시가 없었다.
    계약이 `blockers[].code`로 옮겨갔으므로 검사도 그쪽을 본다 — 문구는 자유롭게 다듬되
    코드와 복구 지시는 잠긴다.
    """
    _cfg(tmp_path, "off", [])
    study.main(["dispatch", str(tmp_path), "--source", "manual"])
    out = _out(capsys)
    assert out["reflected"] is False
    assert [b["code"] for b in out["blockers"]] == [study_dispatch.CODE_UNWIRED]
    assert out["blockers"][0]["recovery"].strip()


def test_dispatch_untrusted_reports_note(tmp_path, capsys):
    def _git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    _git("init")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "t")
    script = tmp_path / "scripts" / "h.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    script.chmod(0o755)
    _git("add", "-A")
    _git("commit", "-m", "handler")
    _cfg(tmp_path, "review", [{"name": "h", "command": "scripts/h.sh"}])

    study.main(["dispatch", str(tmp_path), "--source", "manual", "--concept-path", ".okf/x.md"])
    res = _out(capsys)
    assert any(s["reason"] == "trust 미승인" for s in res["skipped"])
    assert "미승인" in res["note"]


def test_session_nudges_when_auto_and_pending(tmp_path):
    _cfg(tmp_path, "auto", [])
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    message = study_session.run(tmp_path)
    assert message and "1개" in message


def test_session_nudge_includes_file_count(tmp_path):
    # U3(#257): 넛지에 파일 수 부가 — 기존 "승격 대기 후보 N개" 접두는 보존
    _cfg(tmp_path, "auto", [])
    study_inbox.append(_rt(tmp_path), "a", "/mem/one.md", date="2026-07-19")
    study_inbox.append(_rt(tmp_path), "b", "/mem/two.md", date="2026-07-19")
    message = study_session.run(tmp_path)
    assert message and "승격 대기 후보 2개(파일 2개" in message


def test_list_by_file_groups_preserving_candidate_fields(tmp_path, capsys):
    # U3(#257): 파일 그룹 뷰 — 리뷰 단위(파일)로 묶되 후보 전 필드를 보존해
    # provenance(5단계)·후보별 resolve(6단계) 소비를 유지한다. 평탄 기본값은 불변.
    i1 = study_inbox.append(_rt(tmp_path), "a", "/mem/one.md", date="2026-07-19")
    i2 = study_inbox.append(_rt(tmp_path), "b", "/mem/one.md", date="2026-07-19")
    i3 = study_inbox.append(_rt(tmp_path), "c", "/mem/two.md", date="2026-07-20")
    study.main(["list", str(tmp_path), "--by-file"])
    out = _out(capsys)
    assert [g["source"] for g in out] == ["/mem/two.md", "/mem/one.md"]  # 최신 날짜 우선
    assert [g["count"] for g in out] == [1, 2]
    assert [c["id"] for c in out[0]["candidates"]] == [i3]
    one = out[1]["candidates"]
    assert {c["id"] for c in one} == {i1, i2}
    assert set(one[0]) == {"id", "date", "snippet", "source", "recurrence"}  # 전 필드 보존


def test_session_silent_when_review(tmp_path):
    _cfg(tmp_path, "review", [])
    study_inbox.append(_rt(tmp_path), "a", "s", date="2026-07-19")
    assert study_session.run(tmp_path) is None


def test_session_silent_when_no_candidates(tmp_path):
    _cfg(tmp_path, "auto", [])
    assert study_session.run(tmp_path) is None


def test_log_outputs_journal(tmp_path, capsys):
    ident = study_inbox.append(_rt(tmp_path), "a", "s")
    study.main(["log", str(tmp_path)])
    out = _out(capsys)
    assert len(out) == 1
    assert out[0]["action"] == "capture" and out[0]["id"] == ident


# --- 같은 층 번들 근사중복 (Epic #189 U3) -----------------------------------


def test_line_path_gist_parses_context_line():
    assert study._line_path_gist("a/b.md [Fact] — orders table schema") == (
        "a/b.md",
        "orders table schema",
    )
    assert study._line_path_gist("a/b.md [Note]") == ("a/b.md", "")


def test_same_layer_near_is_advisory():
    # 같은 층 개념 중 토큰이 겹치는 것만 자문 표면화(자동병합·게이팅 없음)
    snippet = "orders table customer_id column"
    lines = [
        "existing.md [Fact] — orders table customer_id column",  # 동일 토큰 → dist 0
        "unrelated.md [Fact] — zzzqqq alpha beta gamma delta",  # 다른 토큰
    ]
    hits = study.same_layer_near(snippet, lines, 0)
    assert [h["path"] for h in hits] == ["existing.md"]
    assert hits[0]["distance"] == 0
    assert hits[0]["gist"] == "orders table customer_id column"


def test_same_layer_near_empty_when_no_same_layer_concepts():
    # 그 층에 개념이 없으면 근사중복 없음(다른 층은 애초에 대조 대상이 아님)
    assert study.same_layer_near("anything ascii tokens", [], 3) == []


# --- 인식층 계약 관통 (Epic #189 U5) ----------------------------------------


def test_resolve_records_layer_in_journal(tmp_path, capsys):
    # 승격 시 --layer가 promote 이벤트에 provenance로 남는다 — 후보 드레인 후에도 저널에 유지
    ident = study_inbox.append(_rt(tmp_path), "concept", "M.md")
    study.main(
        ["resolve", str(tmp_path), "--id", ident, "--status", "promoted", "--layer", "wisdom"]
    )
    assert _out(capsys)["layer"] == "wisdom"
    promoted = [e for e in study_inbox.read_journal(_rt(tmp_path)) if e["action"] == "promoted"]
    assert promoted and promoted[0]["layer"] == "wisdom"
