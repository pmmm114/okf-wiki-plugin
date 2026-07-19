"""study_trust — 내용 해시 승인·재승인 강제·fail-closed·디스패처 게이트 테스트 (S4, #76)."""

from __future__ import annotations

import stat
import subprocess

import pytest
import study_dispatch
import study_trust

HANDLERS = [{"name": "h", "command": "scripts/h.sh"}]


def _exec(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def test_untrusted_until_approved(tmp_path):
    _exec(tmp_path / "scripts" / "h.sh", "#!/usr/bin/env bash\n")
    assert not study_trust.is_trusted(tmp_path, HANDLERS, "review")
    study_trust.approve(tmp_path, HANDLERS, "review")
    assert study_trust.is_trusted(tmp_path, HANDLERS, "review")


def test_capture_change_invalidates(tmp_path):
    _exec(tmp_path / "scripts" / "h.sh", "#!/usr/bin/env bash\n")
    study_trust.approve(tmp_path, HANDLERS, "review")
    assert not study_trust.is_trusted(tmp_path, HANDLERS, "auto")  # capture 바뀜 → 재승인


def test_content_swap_forces_reapproval(tmp_path):
    script = tmp_path / "scripts" / "h.sh"
    _exec(script, "#!/usr/bin/env bash\necho ok\n")
    study_trust.approve(tmp_path, HANDLERS, "review")
    assert study_trust.is_trusted(tmp_path, HANDLERS, "review")

    _exec(script, "#!/usr/bin/env bash\necho EVIL\n")  # 같은 경로, 내용만 교체
    assert not study_trust.is_trusted(tmp_path, HANDLERS, "review")  # 내용 해시 불일치


def test_missing_script_is_untrusted(tmp_path):
    assert not study_trust.is_trusted(tmp_path, HANDLERS, "review")


def test_path_escape_rejected_on_approve(tmp_path):
    with pytest.raises(study_dispatch.CommandError):
        study_trust.approve(tmp_path, [{"name": "e", "command": "../evil.sh"}], "review")


def test_make_trust_check_gates_dispatch(tmp_path):
    repo = tmp_path
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _exec(repo / "scripts" / "h.sh", "#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "handler")

    item = {"source": "manual", "concept": {}}

    # 미승인 → 보류
    check = study_trust.make_trust_check(repo, HANDLERS, "review")
    res = study_dispatch.dispatch(repo, item, HANDLERS, check)
    assert res["ran"] == []
    assert res["skipped"][0]["reason"] == "trust 미승인"

    # 승인 후 → 실행
    study_trust.approve(repo, HANDLERS, "review")
    check2 = study_trust.make_trust_check(repo, HANDLERS, "review")
    res2 = study_dispatch.dispatch(repo, item, HANDLERS, check2)
    assert res2["ran"] == ["h"]
