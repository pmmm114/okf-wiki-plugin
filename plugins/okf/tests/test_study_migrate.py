"""study migrate + 런타임-in-홈 회귀 게이트 (#114 U4).

기존 홈 ``<home>/.okf-study`` 런타임을 유저 스코프로 멱등 이동하고, 홈/폴백 캡처의
런타임이 절대 홈 repo 안이 아님을 게이트로 고정한다(재평가·co-location 회귀 차단).
"""

from __future__ import annotations

import json

import okf_home
import okf_inbox
import pytest
import study


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
    okf_inbox.append(legacy, "legacy candidate", "MEMORY.md")
    okf_inbox.record(legacy, "aaaa11112222", "promoted", ref=".okf/x.md")
    okf_inbox.record(legacy, "bbbb33334444", "discarded")
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["migrated"] is True
    assert out["moved"]["candidates"] == 1 and out["moved"]["ledger"] == 2

    assert not legacy.exists()  # 홈 런타임 제거 → 순수 목적지
    us = okf_home.user_scope_runtime()
    assert len(okf_inbox.list_candidates(us)) == 1
    assert okf_inbox.is_resolved(us, "aaaa11112222")
    assert okf_inbox.is_resolved(us, "bbbb33334444")


def test_migrate_copies_trust(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    legacy = home / ".okf-study"
    legacy.mkdir()
    (legacy / "trust").write_text("deadbeefcafe\n", encoding="utf-8")

    assert study.main(["migrate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moved"]["trust"] is True
    assert (okf_home.user_scope_runtime() / "trust").read_text(encoding="utf-8") == "deadbeefcafe\n"


def test_migrate_idempotent_second_run_noop(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    okf_inbox.append(home / ".okf-study", "c", "s")
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
    user_scope = str(okf_home.user_scope_runtime())
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for loc in (scratch, home):
        runtime = okf_home.resolve_capture(loc)["runtime_root"]
        assert runtime == user_scope
        assert not runtime.startswith(str(home))  # 홈 repo 안이 아니다
