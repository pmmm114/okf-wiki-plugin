"""study_dispatch — 경로·git추적 검사, env var, 실패 격리, trust 게이트 테스트 (S3, #75)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
import study_dispatch


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _make_repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _write_exec(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _yes(_name, _path):
    return True


def test_resolve_inside_ok(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "h.sh").write_text("x", encoding="utf-8")
    assert (
        study_dispatch.resolve_command(tmp_path, "scripts/h.sh")
        == (tmp_path / "scripts" / "h.sh").resolve()
    )


def test_resolve_outside_rejected(tmp_path):
    with pytest.raises(study_dispatch.CommandError):
        study_dispatch.resolve_command(tmp_path, "../evil.sh")
    with pytest.raises(study_dispatch.CommandError):
        study_dispatch.resolve_command(tmp_path, "/tmp/evil.sh")


def test_dispatch_runs_tracked_isolates_failure_and_sets_env(tmp_path):
    repo = _make_repo(tmp_path)
    _write_exec(
        repo / "scripts" / "ok.sh",
        "#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\n"
        'echo "$OKF_TRIGGER $OKF_CONCEPT_TYPE $OKF_CONCEPT_TOPIC $OKF_CONCEPT_LAYER"'
        ' > "$OKF_CONCEPT_PATH.env"\n',
    )
    _write_exec(
        repo / "scripts" / "fail.sh", "#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\nexit 1\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "handlers")

    item = {
        "source": "manual",
        "concept": {
            "type": "concept",
            "topic": "engine",
            "layer": "wisdom",
            "path": str(repo / "out"),
        },
    }
    handlers = [
        {"name": "ok", "command": "scripts/ok.sh"},
        {"name": "fail", "command": "scripts/fail.sh"},
    ]
    res = study_dispatch.dispatch(repo, item, handlers, trust_check=_yes)

    assert res["ran"] == ["ok"]
    assert [f["name"] for f in res["failed"]] == ["fail"]  # 실패 격리
    assert (repo / "out.env").read_text(encoding="utf-8").strip() == "manual concept engine wisdom"


def test_dispatch_skips_untracked(tmp_path):
    repo = _make_repo(tmp_path)
    _write_exec(repo / "scripts" / "u.sh", "#!/usr/bin/env bash\n")  # 미커밋
    res = study_dispatch.dispatch(
        repo, {"source": "manual", "concept": {}}, [{"name": "u", "command": "scripts/u.sh"}], _yes
    )
    assert res["ran"] == []
    assert res["skipped"][0]["name"] == "u"
    assert "미추적" in res["skipped"][0]["reason"]


def test_dispatch_skips_untrusted(tmp_path):
    repo = _make_repo(tmp_path)
    _write_exec(repo / "scripts" / "h.sh", "#!/usr/bin/env bash\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "h")
    res = study_dispatch.dispatch(
        repo,
        {"source": "manual", "concept": {}},
        [{"name": "h", "command": "scripts/h.sh"}],
        trust_check=lambda _n, _p: False,
    )
    assert res["ran"] == []
    assert res["skipped"][0]["reason"] == "trust 미승인"


def test_dispatch_skips_path_escape(tmp_path):
    repo = _make_repo(tmp_path)
    res = study_dispatch.dispatch(
        repo, {"source": "manual", "concept": {}}, [{"name": "e", "command": "../evil.sh"}], _yes
    )
    assert res["ran"] == []
    assert "repo 트리 밖" in res["skipped"][0]["reason"]


def test_dispatch_runs_handler_with_repo_cwd(tmp_path, monkeypatch):
    # #153 U2-4: 핸들러 cwd = 승격 대상 repo 루트여야 URL 모드(cwd≠vault)에서 PR 플로우가
    # 성립한다. 핸들러가 pwd·$OKF_PROJECT를 기록해 검증한다.
    repo = _make_repo(tmp_path)
    _write_exec(
        repo / "scripts" / "cwd.sh",
        '#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\npwd > "$OKF_PROJECT/cwd.out"\n'
        'echo "$OKF_PROJECT" >> "$OKF_PROJECT/cwd.out"\n',
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "h")
    # 호출자 cwd를 repo 밖으로 두어 cwd 미지정이면 어긋나게 만든다
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    item = {"source": "manual", "project": str(repo), "concept": {"path": str(repo / "c")}}
    res = study_dispatch.dispatch(repo, item, [{"name": "cwd", "command": "scripts/cwd.sh"}], _yes)
    assert res["ran"] == ["cwd"]
    lines = (repo / "cwd.out").read_text(encoding="utf-8").splitlines()
    assert Path(lines[0]).resolve() == repo.resolve()  # 핸들러가 repo에서 실행됨
    assert lines[1] == str(repo)  # OKF_PROJECT env 전달
