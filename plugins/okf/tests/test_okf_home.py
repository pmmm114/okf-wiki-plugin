"""okf_home — 포인터·홈 판정·주입 해소 테스트 (#91 V2, generic 층).

매트릭스 대응: #9(침묵), 주입 3단 규칙, 무효 사유 코드. 캡처 스코프·캡처 입구·
캡처 활성(study 층)은 #145 U3 분할로 test_study_scope.py로 이동했다 — 이 파일은
feature 지식이 없는 generic 절반만 고정한다. CLI(set/status)의 py 단독 동작은
test_okf_hooks_home.py가 담당한다.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import okf_home
import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """실환경 포인터·설정이 새어들지 않게 HOME·env를 테스트별로 격리한다."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _home(tmp_path, config: dict | None) -> Path:
    """유효 홈 골격(git + 설정)을 만든다. config=None이면 설정 파일 생략."""
    home = tmp_path / "home-kb"
    (home / ".git").mkdir(parents=True)
    if config is not None:
        (home / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def _project(tmp_path, config: dict | None = None) -> Path:
    project = tmp_path / "work"
    project.mkdir(exist_ok=True)
    if config is not None:
        (project / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return project


# --- 포인터·home_state -----------------------------------------------------


def test_no_pointer_is_silent():
    assert okf_home.home_state() == (None, None)


def test_env_pointer_valid(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert okf_home.home_state() == (str(home), None)


def test_pointer_file_with_whitespace(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    fake_home = tmp_path / "isolated-home"
    pointer = fake_home / ".claude" / "okf" / "home-project"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"  {home}\n", encoding="utf-8")
    assert okf_home.home_state() == (str(home), None)


def test_pointer_tilde_expansion(monkeypatch, tmp_path):
    fake_home = tmp_path / "isolated-home"
    home = fake_home / "kb"
    (home / ".git").mkdir(parents=True)
    (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(okf_home.POINTER_ENV, "~/kb")
    assert okf_home.home_state() == (str(home), None)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("missing", okf_home.INVALID_MISSING),
        ("not-git", okf_home.INVALID_NOT_GIT),
        ("no-config", okf_home.INVALID_NO_CONFIG),
        ("relative", okf_home.INVALID_MISSING),
    ],
)
def test_invalid_pointer_reasons(monkeypatch, tmp_path, setup, reason):
    if setup == "missing":
        target = str(tmp_path / "nowhere")
    elif setup == "relative":
        target = "relative/path"
    else:
        home = tmp_path / "home-kb"
        home.mkdir()
        if setup == "no-config":
            (home / ".git").mkdir()
        else:  # not-git
            (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
        target = str(home)
    monkeypatch.setenv(okf_home.POINTER_ENV, target)
    assert okf_home.home_state() == (None, reason)


# --- resolve_inject (#91 §2 주입 3단) ---------------------------------------


def test_inject_project_config_wins(monkeypatch, tmp_path):
    home = _home(tmp_path, {"study": {"capture": "review"}})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    project = _project(tmp_path, {"bundlePath": ".okf"})
    assert okf_home.resolve_inject(project)["target"] == str(project)


def test_inject_falls_back_to_home(monkeypatch, tmp_path):
    home = _home(tmp_path, {"bundlePath": ".okf"})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    result = okf_home.resolve_inject(_project(tmp_path))
    assert (result["target"], result["scope"]) == (str(home), "home")


def test_inject_false_disables_home(monkeypatch, tmp_path):
    home = _home(tmp_path, {"inject": False})
    monkeypatch.setenv(okf_home.POINTER_ENV, str(home))
    assert okf_home.resolve_inject(_project(tmp_path))["target"] is None


def test_inject_invalid_pointer_warns(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_home.POINTER_ENV, str(tmp_path / "nowhere"))
    result = okf_home.resolve_inject(_project(tmp_path))
    assert result["target"] is None
    assert result["warning"] is not None


# --- 경계 (#145 U3) — generic 층은 feature 지식이 없다 ------------------------


def test_generic_module_has_no_study_symbols():
    # 분할 완료 계약: 캡처 정책·런타임 루트·메모리 경로 판정은 study_scope 소관.
    for moved in (
        "study_block",
        "resolve_capture",
        "home_capture_state",
        "enable_home_capture",
        "user_scope_runtime",
        "memory_dir_candidates",
        "is_memory_path",
    ):
        assert not hasattr(okf_home, moved), f"okf_home에 study 심볼 잔존: {moved}"


# --- URL 모드 순수 헬퍼 (#153) ----------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/o/r", True),
        ("git@example.com:o/r.git", True),
        ("ssh://git@example.com/o/r", True),
        ("file:///tmp/x/bare.git", True),
        ("/home/user/local-kb", False),
        ("relative/path", False),
        ("ext::sh -c evil", False),
        ("", False),
        (None, False),
    ],
)
def test_is_url(value, expected):
    assert okf_home.is_url(value) is expected


def test_canonicalize_collapses_notations_and_strips_credentials():
    # 같은 ssh repo의 scp-like·ssh:// 표기는 동일 canonical/slug로 수렴한다.
    scp = okf_home.canonicalize_url("git@Example.com:Owner/repo.git")
    ssh = okf_home.canonicalize_url("ssh://git@example.com/Owner/repo")
    assert scp == ssh == "ssh://example.com/Owner/repo"  # host 소문자, 경로 대소문자·포트 보존
    assert okf_home.remote_slug(scp) == okf_home.remote_slug(ssh)
    # http(s) 유저정보(토큰)는 canonical에서 제거되고 포트는 보존된다.
    assert (
        okf_home.canonicalize_url("https://u:tok@Host.example:8443/o/r.git/")
        == "https://host.example:8443/o/r"
    )


def test_canonicalize_rejects_disallowed_transport():
    for bad in ("ext::sh -c evil", "svn://example.com/o/r", "/abs/path", "not a url"):
        assert okf_home.canonicalize_url(bad) is None


def test_clone_url_preserves_transport_strips_http_credentials():
    # ssh user·.git·대소문자는 clone 성립에 필요 → 보존.
    assert okf_home.clone_url("git@Example.com:o/r.git") == "git@Example.com:o/r.git"
    assert okf_home.clone_url("ssh://git@example.com/o/r") == "ssh://git@example.com/o/r"
    # http(s) 토큰만 제거(평문 포인터 크레덴셜 적재 방지) — 나머지는 보존.
    assert okf_home.clone_url("https://u:tok@example.com/o/r.git") == "https://example.com/o/r.git"
    assert okf_home.clone_url("ext::sh -c evil") is None
    # 퇴화 입력(호스트 없는 스킴)도 크래시 없이 관용 처리 — clone은 나중에 실패 저하.
    assert okf_home.clone_url("https://") == "https://"
    assert okf_home.canonicalize_url("https://") == "https://"


def test_slug_is_filesystem_safe_and_case_folded():
    slug = okf_home.remote_slug("ssh://Example.com/Owner/Repo")
    assert slug == slug.lower()  # APFS 대소문자 무시 충돌 차단
    assert "/" not in slug and " " not in slug
    # 해시 접미로 새니타이즈 충돌(다른 URL이 같은 safe 본문)을 구조적으로 분리.
    a = okf_home.remote_slug("ssh://example.com/o/r")
    b = okf_home.remote_slug("ssh://example.com/o-r")
    assert a != b


def test_managed_clone_path_under_user_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    canonical = okf_home.canonicalize_url("git@example.com:o/r.git")
    path = okf_home.managed_clone_path(canonical)
    assert str(path).startswith(str(tmp_path / "isolated-home" / ".claude" / "okf" / "remotes"))
    assert okf_home.is_managed_clone(path)
    assert not okf_home.is_managed_clone(tmp_path / "elsewhere")


# --- URL 모드 home_state·set_pointer (#153) ---------------------------------


def _managed_clone(tmp_path, url: str, config: dict | None = None) -> Path:
    """URL의 관리형 clone 로컬 경로에 유효 홈 골격을 물질화한다(네트워크 없이)."""
    config = {} if config is None else config
    canonical = okf_home.canonicalize_url(url)
    clone = okf_home.managed_clone_path(canonical)
    (clone / ".git").mkdir(parents=True)
    if config is not None:
        (clone / ".okf-wiki.json").write_text(json.dumps(config), encoding="utf-8")
    return clone


def test_url_pointer_missing_clone_is_distinct_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv(okf_home.POINTER_ENV, "git@example.com:o/r.git")
    # clone 미생성 → 로컬 오탈자(대상 없음)와 구분되는 사유
    assert okf_home.home_state() == (None, okf_home.INVALID_CLONE_MISSING)


def test_url_pointer_resolves_to_managed_clone(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    url = "git@example.com:o/r.git"
    clone = _managed_clone(tmp_path, url, {"bundlePath": ".okf"})
    monkeypatch.setenv(okf_home.POINTER_ENV, url)
    assert okf_home.home_state() == (str(clone), None)


def test_url_pointer_bad_transport_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    # svn://는 scheme URL이라 is_url True지만 transport 미허용 → 전용 사유
    monkeypatch.setenv(okf_home.POINTER_ENV, "svn://example.com/o/r")
    assert okf_home.home_state() == (None, okf_home.INVALID_URL_TRANSPORT)


def test_set_pointer_url_writes_url_not_clone(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    result = okf_home.set_pointer("https://u:tok@example.com/o/r.git")
    assert result["written"] is True
    assert result["mode"] == "url"
    assert result["url"] == "https://example.com/o/r.git"  # 토큰 제거본 저장
    assert result["clone_exists"] is False  # 옵트인 — set은 clone하지 않는다
    pointer = tmp_path / "isolated-home" / ".claude" / "okf" / "home-project"
    assert pointer.read_text(encoding="utf-8").strip() == "https://example.com/o/r.git"


def test_set_pointer_url_reports_existing_clone(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    url = "git@example.com:o/r.git"
    _managed_clone(tmp_path, url, {"bundlePath": ".okf"})
    result = okf_home.set_pointer(url)
    assert result["mode"] == "url" and result["clone_exists"] is True


def test_set_pointer_url_bad_transport_not_written(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    result = okf_home.set_pointer("svn://example.com/o/r")
    assert result["written"] is False and result["reason"] == okf_home.INVALID_URL_TRANSPORT
    pointer = tmp_path / "isolated-home" / ".claude" / "okf" / "home-project"
    assert not pointer.exists()


def test_set_pointer_local_path_keeps_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    home = _home(tmp_path, {"bundlePath": ".okf"})
    result = okf_home.set_pointer(str(home))
    assert result["written"] is True and result["mode"] == "path" and result["home"] == str(home)


# --- 순수성 게이트 (#153 C6-1) — home_state는 무네트워크 분류기 -----------------


def test_okf_home_is_subprocess_free():
    """okf_home은 git I/O를 하지 않는 순수 분류기다 — clone/fetch는 okf_remote 소관.

    home_state가 매 .md Write 훅 핫패스에서 호출되므로(U1-1), 이 모듈에 subprocess·
    소켓이 새어들면 저장마다 네트워크 블록 위험이 생긴다. import 계층에서 고정한다.
    """
    source = (Path(okf_home.__file__)).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"subprocess", "socket", "urllib", "http", "asyncio"}
    assert not (imported & forbidden), f"okf_home 순수성 위반 — I/O 의존 {imported & forbidden}"
