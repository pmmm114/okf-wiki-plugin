"""study migrate + 런타임-in-홈 회귀 게이트 (#114 U4).

기존 홈 ``<home>/.okf-study`` 런타임을 유저 스코프로 멱등 이동하고, 홈/폴백 캡처의
런타임이 절대 홈 repo 안이 아님을 게이트로 고정한다(재평가·co-location 회귀 차단).
"""

from __future__ import annotations

import json

import okf_home
import pytest
import study
import study_inbox
import study_legacy
import study_scope


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _home(tmp_path, capture="review"):
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture}}), encoding="utf-8"
    )
    return home


# --- 마이그레이션 -----------------------------------------------------------


def test_migrate_moves_runtime_to_user_scope(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    legacy = home / ".okf-study"  # 구 모델: 홈 안 런타임
    # 구 상태는 포인터를 걸기 전에 만든다 — 그래야 record가 유저 스코프로
    # write-through하지 않아 "이관 대상"으로 남는다(업그레이드 전 상태 재현).
    study_inbox.append(legacy, "legacy candidate", "MEMORY.md")
    study_inbox.record(legacy, "aaaa11112222", "promoted", ref=".okf/x.md")
    study_inbox.record(legacy, "bbbb33334444", "discarded")
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["migrated"] is True
    assert out["moved"]["candidates"] == 1 and out["moved"]["ledger"] == 2

    assert not legacy.exists()  # 홈 런타임 제거 → 순수 목적지
    us = study_scope.user_scope_runtime()
    assert len(study_inbox.list_candidates(us)) == 1
    assert study_inbox.is_resolved(us, "aaaa11112222")
    assert study_inbox.is_resolved(us, "bbbb33334444")


def test_migrate_copies_trust(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    legacy = home / ".okf-study"
    legacy.mkdir()
    (legacy / "trust").write_text("deadbeefcafe\n", encoding="utf-8")

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moved"]["trust"] is True
    trust = (study_scope.user_scope_runtime() / "trust").read_text(encoding="utf-8")
    assert trust == "deadbeefcafe\n"


def test_migrate_idempotent_second_run_noop(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    study_inbox.append(home / ".okf-study", "c", "s")
    study.main(["migrate"])
    capsys.readouterr()

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["migrated"] is False and "없음" in out["reason"]


def test_migrate_no_pointer_is_noop(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    assert study.main(["migrate"]) == 0
    assert json.loads(capsys.readouterr().out)["migrated"] is False


# --- 게이트: 홈/폴백 런타임은 절대 홈 안이 아니다 -----------------------------


def test_gate_home_fallback_runtime_never_in_home(monkeypatch, tmp_path):
    # 파괴 감지 게이트(#114) — 이 불변식이 깨지면(홈에 런타임 생성) 재평가·인박스
    # co-location 문제가 재발한다. 무설정 위치와 홈 자신 양쪽에서 검증한다.
    home = _home(tmp_path, "review")
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    user_scope = str(study_scope.user_scope_runtime())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for loc in (scratch, home):
        runtime = study_scope.resolve_capture(loc)["runtime_root"]
        assert runtime == user_scope
        assert not runtime.startswith(str(home))  # 홈 repo 안이 아니다


# --- 레거시 markdown 2원천 이관 (U5 #134) -----------------------------------


def _write_legacy_markdown(directory, cands, resolutions):
    """옛 3종 포맷을 쓴다. cands: [(snippet, source, date)], resolutions: [(id, status, ref)]."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["# Study Inbox", ""]
    for snippet, source, date in cands:
        ident = study_inbox.content_hash(snippet)[:12]
        lines.append(f"## {date}")
        lines.append(f"* **memory**: {snippet} — {source} <!-- id:{ident} -->")
    (directory / study_legacy.INBOX_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if resolutions:
        led = "".join(f"{i} {s}" + (f" {r}" if r else "") + "\n" for i, s, r in resolutions)
        (directory / study_legacy.LEDGER_NAME).write_text(led, encoding="utf-8")


def test_migrate_imports_home_legacy_markdown(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    legacy = home / ".okf-study"
    _write_legacy_markdown(
        legacy,
        [("legacy fact", "MEMORY.md", "2026-07-01")],
        [("aaaa11112222", "promoted", ".okf/x.md")],
    )
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["migrated"] is True and "home" in out["moved"]["sources"]
    assert out["moved"]["candidates"] == 1 and out["moved"]["ledger"] == 1

    us = study_scope.user_scope_runtime()
    assert len(study_inbox.list_candidates(us)) == 1
    assert study_inbox.is_resolved(us, "aaaa11112222")
    assert not legacy.exists()  # 홈은 순수 목적지로


def test_migrate_imports_userscope_legacy_markdown(tmp_path, capsys):
    us = study_scope.user_scope_runtime()
    _write_legacy_markdown(us, [("userscope fact", "MEMORY.md", "2026-07-02")], [])

    assert study.main(["migrate"]) == 0  # 홈 포인터 없이도 (b) 원천 처리
    out = json.loads(capsys.readouterr().out)
    assert out["migrated"] is True and "user-scope-markdown" in out["moved"]["sources"]
    assert len(study_inbox.list_candidates(us)) == 1
    assert not study_legacy.has_legacy(us)  # 옛 markdown 소모됨


def test_migrate_both_sources_together(monkeypatch, tmp_path, capsys):
    us = study_scope.user_scope_runtime()
    _write_legacy_markdown(us, [("from userscope", "M", "2026-07-02")], [])
    home = _home(tmp_path)
    _write_legacy_markdown(home / ".okf-study", [("from home", "M", "2026-07-01")], [])
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["moved"]["sources"]) == {"user-scope-markdown", "home"}
    assert len(study_inbox.list_candidates(us)) == 2  # 양쪽 이관


def test_migrate_legacy_markdown_ledger_continuity(monkeypatch, tmp_path):
    # 레거시 promoted 줄-id → 이관 후 그 줄만의 블록은 재부상하지 않는다(A2′)
    home = _home(tmp_path)
    snippet = "already promoted line"
    ident = study_inbox.content_hash(snippet)[:12]
    _write_legacy_markdown(home / ".okf-study", [], [(ident, "promoted", ".okf/x.md")])
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    study.main(["migrate"])

    us = str(study_scope.user_scope_runtime())
    assert study_inbox.block_resolved(us, ident, [ident]) is True  # 자식=옛 id, resolved
