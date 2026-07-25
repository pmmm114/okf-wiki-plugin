"""epic_completeness 완결성 판정 테스트 — gh api는 monkeypatch로 대체한다."""

from __future__ import annotations

import subprocess

import epic_completeness as ec
import pytest


def _summary(monkeypatch, **kw):
    monkeypatch.setattr(ec, "_fetch_with_retry", lambda n, **_: kw)


def test_non_integration_pr_passes_without_api(monkeypatch):
    """대상(base=main·head=epic/<n>)이 아니면 API를 부르지 않고 통과."""

    def boom(*a, **k):
        raise AssertionError("대상 아닌데 API를 불렀다")

    monkeypatch.setattr(ec, "_fetch_with_retry", boom)
    assert ec.check("epic/189-study", "feat/u1")[0] == 0  # 유닛 → 통합 브랜치
    assert ec.check("main", "feat/u1")[0] == 0  # 일반 → main


def test_integration_complete_passes(monkeypatch):
    _summary(monkeypatch, total=5, completed=5)
    code, msg = ec.check("main", "epic/189-study")
    assert code == 0 and "완결" in msg


def test_integration_incomplete_blocks(monkeypatch):
    _summary(monkeypatch, total=5, completed=3)
    code, msg = ec.check("main", "epic/189-study")
    assert code == 1 and "미완결" in msg


def test_integration_no_subissues_blocks(monkeypatch):
    _summary(monkeypatch, total=0, completed=0)
    assert ec.check("main", "epic/189-study")[0] == 1


def test_bad_epic_ref_is_error(monkeypatch):
    _summary(monkeypatch, total=1, completed=1)
    assert ec.check("main", "epic/study")[0] == 2  # 번호 없음


def test_api_failure_fails_closed(monkeypatch):
    def boom(n, **_):
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(ec, "_fetch_with_retry", boom)
    code, msg = ec.check("main", "epic/189-study")
    assert code == 2 and "재실행" in msg


def test_retry_then_success(monkeypatch):
    calls = {"n": 0}

    def flaky(issue):
        calls["n"] += 1
        if calls["n"] < 2:
            raise subprocess.CalledProcessError(1, "gh")
        return {"total": 2, "completed": 2}

    monkeypatch.setattr(ec, "sub_issue_summary", flaky)
    got = ec._fetch_with_retry(189, sleep=lambda _s: None)
    assert got == {"total": 2, "completed": 2} and calls["n"] == 2


def test_retry_exhausts_and_raises(monkeypatch):
    def always_fail(issue):
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(ec, "sub_issue_summary", always_fail)
    with pytest.raises(subprocess.CalledProcessError):
        ec._fetch_with_retry(189, sleep=lambda _s: None)


def test_cli_reads_env(monkeypatch):
    monkeypatch.setenv("PR_BASE_REF", "main")
    monkeypatch.setenv("PR_HEAD_REF", "epic/189-study")
    _summary(monkeypatch, total=2, completed=1)
    assert ec.main(["--check-pr"]) == 1
