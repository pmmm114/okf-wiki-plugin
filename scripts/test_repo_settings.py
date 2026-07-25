"""repo 설정 점검 로직 테스트 — 네트워크 없이 판정 함수만 검증한다.

`gh` 호출은 인증이 필요해 CI에서 돌릴 수 없다. 그래서 드리프트 판정을 순수 함수로
떼어 두고 여기서 그 계약만 고정한다. 실제로 이 repo에서 발견됐던 상태(merge·rebase가
열려 있고 required status check 규칙 자체가 없던 것)를 회귀 사례로 남긴다.
"""

from __future__ import annotations

import pytest
import repo_settings as rs

# --- 리모트 파싱 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:owner/name.git", "owner/name"),
        ("git@github.com:owner/name", "owner/name"),
        ("https://github.com/owner/name.git", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("https://github.com/owner/name/", "owner/name"),
        ("  git@github.com:owner/name.git\n", "owner/name"),
    ],
)
def test_parse_remote(url, expected):
    assert rs.parse_remote(url) == expected


def test_parse_remote_rejects_unknown_shape():
    assert rs.parse_remote("not-a-remote") is None


# --- 머지 설정 드리프트 -------------------------------------------------------


def _desired_settings() -> dict:
    return {k: want for k, (want, _why) in rs.DESIRED_SETTINGS.items()}


def test_no_drift_when_settings_match():
    assert rs.settings_drift(_desired_settings()) == []


def test_drift_reports_key_current_want_and_reason():
    current = _desired_settings() | {"allow_merge_commit": True}
    drift = rs.settings_drift(current)
    assert len(drift) == 1
    key, got, want, why = drift[0]
    assert (key, got, want) == ("allow_merge_commit", True, False)
    assert why, "이유 문구가 비어 있으면 설정이 왜 그런지 모른 채 되돌려진다"


def test_drift_catches_the_state_this_repo_was_actually_in():
    """실제 발견 상태 — merge·rebase가 열려 있고 스쿼시 제목이 비결정적이었다."""
    current = _desired_settings() | {
        "allow_merge_commit": True,
        "allow_rebase_merge": True,
        "delete_branch_on_merge": False,
        "squash_merge_commit_title": "COMMIT_OR_PR_TITLE",
    }
    keys = {k for k, *_ in rs.settings_drift(current)}
    assert keys == {
        "allow_merge_commit",
        "allow_rebase_merge",
        "delete_branch_on_merge",
        "squash_merge_commit_title",
    }


def test_missing_key_counts_as_drift():
    """응답에 키가 없으면 일치로 보지 않는다 — 모르는 것을 통과시키면 게이트가 아니다."""
    current = _desired_settings()
    del current["delete_branch_on_merge"]
    assert any(k == "delete_branch_on_merge" for k, *_ in rs.settings_drift(current))


# --- 브랜치 룰셋 드리프트 -----------------------------------------------------


def _healthy_rules() -> list[dict]:
    return [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {"type": "pull_request", "parameters": {"allowed_merge_methods": ["squash"]}},
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "core"}]},
        },
    ]


def test_no_ruleset_drift_when_healthy():
    assert rs.ruleset_drift(_healthy_rules()) == []


def test_ruleset_drift_catches_missing_required_check():
    """실제 발견 상태 — 문서는 'core 잡이 녹색이어야 머지'라는데 규칙 자체가 없었다."""
    rules = [r for r in _healthy_rules() if r["type"] != "required_status_checks"]
    problems = rs.ruleset_drift(rules)
    assert any("required status check" in p for p in problems), problems
    assert any("CI가 red여도 머지" in p for p in problems), problems


def test_ruleset_drift_catches_open_merge_methods():
    """실제 발견 상태 — 스쿼시 전용이라면서 merge·rebase가 모두 열려 있었다."""
    rules = _healthy_rules()
    for r in rules:
        if r["type"] == "pull_request":
            r["parameters"]["allowed_merge_methods"] = ["merge", "squash", "rebase"]
    assert any("허용 머지 방식" in p for p in rs.ruleset_drift(rules))


def test_ruleset_drift_catches_missing_linear_history():
    rules = [r for r in _healthy_rules() if r["type"] != "required_linear_history"]
    assert any("required_linear_history" in p for p in rs.ruleset_drift(rules))


def test_ruleset_drift_catches_wrong_check_context():
    """`core`가 아닌 이름이면 잡는다 — 잡 이름을 바꾸면 게이트가 조용히 풀린다."""
    rules = _healthy_rules()
    for r in rules:
        if r["type"] == "required_status_checks":
            r["parameters"]["required_status_checks"] = [{"context": "build"}]
    assert any("required status check" in p for p in rs.ruleset_drift(rules))


def test_ruleset_drift_on_empty_ruleset():
    problems = rs.ruleset_drift([])
    assert problems, "규칙이 하나도 없으면 전부 어긋난 것이다"
