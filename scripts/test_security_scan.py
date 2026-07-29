"""보안 게이트 검증 — 게이트가 **실제로 잡는지**까지 확인한다.

통과만 확인하는 게이트 테스트는 위험하다. 명령 하나가 조용히 실패해도 "위반 0건"과
구분되지 않아, 아무것도 안 잡는 게이트가 영원히 녹색으로 남는다. 그래서 아래는 실제
repo가 깨끗한지와 **일부러 만든 위반을 잡는지**를 함께 본다.

`/Users/` 리터럴을 문자열 조합으로 쓰는 곳이 있다. 이 파일도 추적 대상이라 게이트의
머신 경로 검사가 자기 테스트를 위반으로 잡기 때문이다 — 회피가 아니라, 검사가 소스
전체에 고르게 적용된다는 증거다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import security_scan as scan

_ROOT = Path(__file__).resolve().parent.parent
_USERS = "/Users" + "/"  # 게이트 자기 탐지 회피 — 위 docstring 참고


# --- 실제 repo 상태 -----------------------------------------------------------


def test_repo_passes_the_gate():
    violations = {label: items for label, items in scan.collect() if items}
    assert not violations, f"보안 게이트 위반이 있습니다: {violations}"


def test_every_workflow_declares_permissions():
    """워크플로가 늘어날 때 permissions 선언을 빠뜨리면 여기서 걸린다."""
    assert not scan.workflows_without_permissions()


def test_scanner_reads_real_repo():
    """실 repo에서 추적 파일을 실제로 읽는지 — 빈 목록끼리 비교해 통과하는 공허한
    검사가 되지 않게 한다."""
    assert len(scan.tracked_files()) > 50


# --- 무시 대상 추적 탐지 (진짜 잡는가) -----------------------------------------


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".venv/\nsecrets.json\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "normal.py").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore", "normal.py"], check=True)
    return tmp_path


def test_clean_repo_has_no_ignored_tracked_files(tmp_path):
    assert scan.tracked_but_ignored(_repo(tmp_path)) == []


def test_force_added_ignored_files_are_caught(tmp_path):
    """`git add -f`가 무시 규칙을 뚫은 순간 — 이 게이트의 존재 이유."""
    root = _repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", ".venv/lib.py", "secrets.json"], check=True
    )
    assert scan.tracked_but_ignored(root) == [".venv/lib.py", "secrets.json"]


def test_local_exclude_file_does_not_change_the_verdict(tmp_path):
    """`.git/info/exclude`는 커밋되지 않는 **개인 설정**이다. 판정이 여기 흔들리면
    같은 커밋이 내 머신에서만 붉어진다 — `--exclude-standard`를 쓰지 않는 이유."""
    root = _repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "add", "-f", ".venv/lib.py"], check=True)
    before = scan.tracked_but_ignored(root)
    (root / ".git" / "info" / "exclude").write_text("normal.py\n", encoding="utf-8")
    assert scan.tracked_but_ignored(root) == before


# --- 크리덴셜 파일 -------------------------------------------------------------


def test_credential_files_are_caught():
    caught = [
        "deploy.pem",
        "server.key",
        "cert.p12",
        "keystore.jks",
        "id_rsa",
        ".netrc",
        "config/.env",
        ".env.local",
        "nested/dir/.env.production",
    ]
    assert scan.credential_files(caught) == sorted(caught)


def test_ordinary_files_are_not_credentials():
    """오탐은 게이트를 끄게 만든다 — 멀쩡한 이름이 걸리지 않는지 함께 고정한다."""
    ok = [
        "credentials.py",  # 이름 정확 일치가 아니다
        "docs/keystore-guide.md",
        ".env.example",  # 예제는 값이 아니라 키 목록이라 커밋되어야 한다
        ".env.sample",
        ".env.template",
        "scripts/security_scan.py",
    ]
    assert scan.credential_files(ok) == []


# --- 워크플로 permissions ------------------------------------------------------


def test_top_level_declaration_covers_every_job():
    text = (
        "name: ci\npermissions:\n  contents: read\n"
        "jobs:\n  core:\n    runs-on: x\n  second:\n    runs-on: y\n"
    )
    assert scan.jobs_without_permissions(text) == []


def test_missing_declaration_is_caught():
    text = "name: ci\non: [push]\njobs:\n  core:\n    runs-on: x\n"
    assert scan.jobs_without_permissions(text) == ["core"]


def test_job_level_declaration_is_accepted():
    """잡 레벨 선언은 유효하다 — 처음엔 최상위만 인정했는데 실물이 그 규칙을 반증했다.

    `epic-subissue.yml`은 잡 레벨에 `issues: write`·`actions: write`를 **정확히 필요한
    만큼** 선언하고 있었다. 최상위를 강요하면 그 권한이 다른 잡까지 퍼지므로, 최소권한
    원칙을 지키는 쪽이 게이트에 걸리는 뒤집힌 상황이 된다.
    """
    text = "name: x\njobs:\n  core:\n    permissions:\n      issues: write\n    runs-on: x\n"
    assert scan.jobs_without_permissions(text) == []


def test_only_the_undeclared_job_is_caught():
    """잡별 선언을 인정하는 대가 — 선언을 빠뜨린 잡이 섞이면 그 잡만 짚어야 한다."""
    text = (
        "name: x\njobs:\n"
        "  covered:\n    permissions:\n      contents: read\n    runs-on: x\n"
        "  forgotten:\n    runs-on: y\n"
    )
    assert scan.jobs_without_permissions(text) == ["forgotten"]


def test_job_scanner_ignores_comments_and_step_level_keys():
    """스캐너 자체 검증 — 잡 이름 층을 잘못 잡으면 위 검사가 통째로 무의미해진다."""
    text = (
        "name: x\njobs:\n\n  # 주석\n  core:\n    runs-on: x\n"
        "    steps:\n      - name: 스텝\n        run: echo hi\n"
    )
    assert [name for name, _ in scan._job_blocks(text)] == ["core"]


def test_job_scanner_returns_empty_without_jobs_block():
    assert scan._job_blocks("name: x\non: [push]\n") == []


# --- 개발 머신 경로 -------------------------------------------------------------


def test_machine_path_is_caught():
    found = scan.machine_paths_in_text(f"vault = '{_USERS}kim/notes'\n")
    assert found == [(1, f"{_USERS}kim")]


def test_placeholder_user_is_allowed():
    """문서가 경로 예시를 들 수 있어야 한다 — 자리표시자는 실제 경로가 아니다."""
    for user in sorted(scan.PLACEHOLDER_USERS):
        assert scan.machine_paths_in_text(f"{_USERS}{user}/vault") == []


def test_machine_path_reports_line_numbers():
    text = "ok\n" * 4 + f"path = {_USERS}alice/kb\n"
    assert scan.machine_paths_in_text(text) == [(5, f"{_USERS}alice")]


def test_vendor_is_exempt_from_machine_path_check(tmp_path):
    """벤더는 업스트림 바이트 그대로여야 한다(CLAUDE.md). 고칠 수 없는 파일을 막으면
    벤더 갱신 자체가 막힌다 — 그쪽은 vendor_sync_check가 본다."""
    rel = "okf-core/vendor/spec/upstream.md"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(f"{_USERS}someone-real/x\n", encoding="utf-8")
    assert scan.machine_paths([rel], tmp_path) == []
    assert scan.machine_paths(["okf-core/src/x.md"], tmp_path) == []  # 없는 파일은 건너뛴다


# --- git 실패는 판정이 아니라 실행 오류다 (#303) --------------------------------


def test_git_failure_raises_instead_of_empty_list(tmp_path, monkeypatch):
    """git이 죽으면 빈 목록이 아니라 `GitError` — 고장을 "위반 없음"으로 흡수하지 않는다.

    변경 전 실측: 가짜 `git`(exit 128)에서
    `"보안 게이트 통과: 추적 파일 0개, 검사 4종"` + exit 0.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 128\n", encoding="utf-8")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    with pytest.raises(scan.GitError):
        scan.tracked_files(tmp_path)


def test_main_reports_execution_error_on_git_failure(tmp_path, monkeypatch, capsys):
    """실행 오류는 exit 2 — 위반(1)·통과(0)와 구분된다."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 128\n", encoding="utf-8")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    assert scan.main() == 2
    assert "실행 오류" in capsys.readouterr().err
