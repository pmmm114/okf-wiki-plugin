"""중복 판정 실수요 수집 테스트 (#393).

의미 기반(임베딩) 도입 여부를 결정할 데이터를 모은다. 성공 축은 승격 때 "이미 있다"를
알아채고 `mode: update`로 흡수한 건수, 실패 축은 나중에 "이 두 개념이 같은 말이었다"를
발견해 병합한 건수다. **건수만 센다** — 기록 시점에 지표 순위를 계산하지 않는다.
쌓인 실패 사례 자체가 골든셋이 되어 그때 오프라인으로 평가한다.
"""

from __future__ import annotations

import json

import pytest
import study
import study_inbox
import study_store


def _events(runtime, action):
    return [e for e in study_inbox.read_journal(runtime) if e["action"] == action]


# --- 성공 축: mode 병기 -------------------------------------------------------


def test_resolve_records_promotion_mode(tmp_path):
    """`--mode update`가 저널에 남는다 — "이미 있어서 흡수했다"의 기계 신호(#393).

    apply 시점에 남길 수 없다. `okf_promote`는 캡처 런타임 무-import이기 때문이다
    (도메인 게이트 강화 조항) — 커맨드가 이미 아는 값을 resolve로 넘긴다.
    """
    ident = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    study_inbox.record(tmp_path, ident, "promoted", ref="dev/x.md", mode="update")

    promoted = _events(tmp_path, "promoted")
    assert [e.get("mode") for e in promoted] == ["update"]


def test_resolve_mode_is_optional_and_absent_when_unstated(tmp_path):
    """미지정은 **미상**이다 — path 실존으로 암묵 판별하지 않는다(#351)."""
    ident = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    study_inbox.record(tmp_path, ident, "promoted", ref="dev/x.md")

    assert "mode" not in _events(tmp_path, "promoted")[0]


def test_resolve_cli_passes_mode(tmp_path, capsys):
    ident = study_inbox.append(tmp_path / ".okf-study", "alpha beta gamma", "M.md")

    code = study.main(
        ["resolve", str(tmp_path), "--id", ident, "--status", "promoted", "--mode", "update"]
    )
    capsys.readouterr()

    assert code == 0
    assert _events(tmp_path / ".okf-study", "promoted")[0]["mode"] == "update"


def test_resolve_rejects_unknown_mode(tmp_path):
    with pytest.raises(SystemExit) as exc:
        study.main(
            ["resolve", str(tmp_path), "--id", "x", "--status", "promoted", "--mode", "머지"]
        )
    assert exc.value.code == 2  # argparse 사용법 오류


# --- 실패 축: dedup-miss ------------------------------------------------------


def test_dedup_miss_is_journaled(tmp_path):
    """뒤늦게 발견한 중복을 저널에 남긴다 — 임베딩 도입 판단의 분자."""
    study_inbox.dedup_miss(tmp_path, ["dev/a.md", "dev/b.md"], captured="abc123")

    events = _events(tmp_path, "dedup_miss")
    assert len(events) == 1
    assert events[0]["concepts"] == ["dev/a.md", "dev/b.md"]
    assert events[0]["id"] == "abc123"


def test_dedup_miss_does_not_touch_ledger_or_candidates(tmp_path):
    """관측 기록이다 — 원장·후보를 건드리지 않는다(판정과 집행의 분리)."""
    ident = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")

    study_inbox.dedup_miss(tmp_path, ["dev/a.md", "dev/b.md"])

    assert study_inbox.is_resolved(tmp_path, ident) is False
    assert len(study_inbox.list_candidates(tmp_path)) == 1


def test_dedup_miss_cli(tmp_path, capsys):
    code = study.main(
        ["dedup-miss", str(tmp_path), "--concepts", "dev/a.md", "dev/b.md", "--captured", "abc123"]
    )
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["recorded"]["concepts"] == ["dev/a.md", "dev/b.md"]
    assert _events(tmp_path / ".okf-study", "dedup_miss")


def test_dedup_miss_needs_at_least_two_concepts(tmp_path, capsys):
    """한 개념만으로는 중복이 아니다 — 입구에서 가시적으로 막는다."""
    code = study.main(["dedup-miss", str(tmp_path), "--concepts", "dev/a.md"])
    out = json.loads(capsys.readouterr().out)

    assert code == 1
    assert "error" in out
    assert not _events(tmp_path / ".okf-study", "dedup_miss")


# --- 집계 --------------------------------------------------------------------


def test_dedup_report_counts_both_axes(tmp_path):
    runtime = tmp_path / ".okf-study"
    for snippet, mode in (("alpha beta", "update"), ("gamma delta", "create"), ("eta theta", None)):
        ident = study_inbox.append(runtime, snippet, "M.md")
        study_inbox.record(runtime, ident, "promoted", ref="dev/x.md", mode=mode)
    study_inbox.dedup_miss(runtime, ["dev/a.md", "dev/b.md"])
    study_inbox.dedup_miss(runtime, ["dev/c.md", "dev/d.md"], captured="zzz")

    stats = study_inbox.dedup_stats(runtime)

    assert stats["promoted"] == {"update": 1, "create": 1, "unstated": 1}
    assert stats["misses"] == 2
    assert [m["concepts"] for m in stats["cases"]] == [
        ["dev/a.md", "dev/b.md"],
        ["dev/c.md", "dev/d.md"],
    ]


def test_dedup_report_cli(tmp_path, capsys):
    runtime = tmp_path / ".okf-study"
    ident = study_inbox.append(runtime, "alpha beta", "M.md")
    study_inbox.record(runtime, ident, "promoted", ref="dev/x.md", mode="update")
    study_inbox.dedup_miss(runtime, ["dev/a.md", "dev/b.md"])

    code = study.main(["dedup-report", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["promoted"]["update"] == 1
    assert out["misses"] == 1


def test_dedup_report_on_empty_runtime(tmp_path, capsys):
    """수집 전에도 계약대로 낸다 — 빈 값과 오류를 구분한다."""
    code = study.main(["dedup-report", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out == {"promoted": {"update": 0, "create": 0, "unstated": 0}, "misses": 0, "cases": []}


def test_dedup_stats_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(study_store, "sqlite3", None)
    assert study_inbox.dedup_stats(tmp_path)["misses"] == 0
