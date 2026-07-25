"""study_dispatch — 경로·git추적 검사, env var, 실패 격리, trust 게이트 테스트 (S3, #75)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import okf_vault
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


# --- 봉인 잔재 회수 (#226 V2 — 내구성 증명, exit code 불관여) -------------------

_NOOP_HANDLER = "#!/usr/bin/env python3\nimport sys\n\nsys.stdin.read()\n"
_FAILING_HANDLER = "#!/usr/bin/env python3\nimport sys\n\nsys.stdin.read()\nsys.exit(1)\n"


def _managed_clone(tmp_path, monkeypatch, handler_body=_NOOP_HANDLER):
    """관리형 clone 경로에 선 clone과 그 origin을 만든다(핸들러는 커밋됨)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    (origin / ".okf").mkdir()
    (origin / ".okf" / "index.md").write_text("# index\n", encoding="utf-8")
    _write_exec(origin / "scripts" / "h.py", handler_body)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "seed")
    clone = okf_vault.managed_root() / "slug-test"
    clone.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone


def _seal(origin, clone, rel, body):
    """origin에 <rel>을 커밋하고 clone이 fetch하게 한다 — 핸들러 push와 동치인 봉인."""
    target = Path(origin) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", f"add {rel}")
    _git(clone, "fetch", "-q")


def _dispatch(clone, rel, trust=_yes):
    item = {
        "source": "manual",
        "project": str(clone),
        "concept": {"type": "concept", "topic": "t", "path": str(Path(clone) / rel)},
    }
    return study_dispatch.dispatch(clone, item, [{"name": "h", "command": "scripts/h.py"}], trust)


def test_dispatch_reclaims_sealed_residue(tmp_path, monkeypatch):
    """원격에 담긴(=회수 가능한) 잔재만 회수해 clone을 clean으로 되돌린다."""
    origin, clone = _managed_clone(tmp_path, monkeypatch)
    body = "# promoted\n"
    (clone / ".okf" / "new.md").write_text(body, encoding="utf-8")
    _seal(origin, clone, ".okf/new.md", body)
    result = _dispatch(clone, ".okf/new.md")
    assert result["ran"] == ["h"]
    assert result["reclaimed"] == [".okf/new.md"]
    assert not (clone / ".okf" / "new.md").exists()


def test_dispatch_keeps_unsealed_residue(tmp_path, monkeypatch):
    """어디에도 push되지 않은 승격은 절대 지우지 않는다 — 원장이 재부상을 막아 영구 유실이 된다."""
    _origin, clone = _managed_clone(tmp_path, monkeypatch)
    (clone / ".okf" / "local.md").write_text("# unpushed\n", encoding="utf-8")
    result = _dispatch(clone, ".okf/local.md")
    assert result["ran"] == ["h"]
    assert result["reclaimed"] == []
    assert (clone / ".okf" / "local.md").exists()


def test_dispatch_reclaim_ignores_exit_code(tmp_path, monkeypatch):
    """핸들러가 비-0으로 끝나도 봉인됐으면 회수한다 — push 성공 후 PR 생성 실패가 흔하다."""
    origin, clone = _managed_clone(tmp_path, monkeypatch, handler_body=_FAILING_HANDLER)
    body = "# pushed then failed\n"
    (clone / ".okf" / "x.md").write_text(body, encoding="utf-8")
    _seal(origin, clone, ".okf/x.md", body)
    result = _dispatch(clone, ".okf/x.md")
    assert result["failed"] and result["ran"] == []  # 핸들러는 실패로 기록되지만
    assert result["reclaimed"] == [".okf/x.md"]  # 내구성은 증명됐으므로 회수


def test_dispatch_keeps_residue_when_trust_denied(tmp_path, monkeypatch):
    """trust 미승인(신규 머신의 정상 상태)에서 아무것도 지우지 않는다.

    핸들러가 실행되지 않았으니 봉인될 리 없다 — exit code가 아니라 봉인을 보기 때문에
    `skipped`가 `failed`에 안 잡히는 함정(#216 §3-1)에 걸리지 않는다.
    """
    _origin, clone = _managed_clone(tmp_path, monkeypatch)
    (clone / ".okf" / "t.md").write_text("# promoted\n", encoding="utf-8")
    result = _dispatch(clone, ".okf/t.md", trust=lambda _n, _p: False)
    assert result["skipped"] and not result["failed"]  # '실패 없음'으로 보이는 상태
    assert result["reclaimed"] == []
    assert (clone / ".okf" / "t.md").exists()


def test_dispatch_preserves_scaffold_artifacts(tmp_path, monkeypatch):
    """스캐폴드가 clone에 **일부러 미커밋으로** 남긴 핸들러·배선을 지우지 않는다."""
    _origin, clone = _managed_clone(tmp_path, monkeypatch)
    _write_exec(clone / "scripts" / "okf-open-pr.py", _NOOP_HANDLER)  # 미추적
    (clone / ".okf-wiki.json").write_text('{"study": {}}\n', encoding="utf-8")  # 미추적
    result = _dispatch(clone, ".okf/none.md")
    assert result["reclaimed"] == []
    assert (clone / "scripts" / "okf-open-pr.py").exists()
    assert (clone / ".okf-wiki.json").exists()


def test_dispatch_skips_reclaim_outside_managed_clone(tmp_path, monkeypatch):
    """관리형 clone이 아니면 회수에 진입하지 않는다 — 사용자 작업 repo를 건드리면 파괴다."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plain = tmp_path / "plain"
    plain.mkdir()
    repo = _make_repo(plain)
    _write_exec(repo / "scripts" / "h.py", _NOOP_HANDLER)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "h")
    (repo / "wip.md").write_text("# 사용자 미커밋 작업\n", encoding="utf-8")
    result = _dispatch(repo, "wip.md")
    assert "reclaimed" not in result  # 판정 자체를 하지 않는다
    assert (repo / "wip.md").exists()
