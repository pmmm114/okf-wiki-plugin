"""관리형 clone 계층 테스트 (#153) — file:// 로컬 원격으로 무네트워크 실증.

clone/fetch/refresh/doctor 표시를 실제 git으로 돌리되, 원격은 로컬 file:// repo라
CI에서 네트워크·인증 없이 성립한다(C2-1 헤르메틱 시임). 원칙 검증: resolver 무네트워크·
SessionStart fetch-only·clean-gate ff·오프라인 저하·미생성 옵트인.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import okf_remote
import okf_vault
import pytest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _origin(tmp_path, config: dict | None = None):
    """기본 브랜치 main + 커밋된 .okf-wiki.json·번들을 담은 로컬 원격을 만든다."""
    src = tmp_path / "origin-src"
    src.mkdir()
    _git(src, "init")
    _git(src, "symbolic-ref", "HEAD", "refs/heads/main")  # -b 대신 버전-무관(git<2.28 호환)
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / ".okf-wiki.json").write_text(
        json.dumps(config or {"bundlePath": ".okf"}), encoding="utf-8"
    )
    (src / ".okf").mkdir()
    (src / ".okf" / "index.md").write_text("# index\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "seed")
    return src


def _url(src) -> str:
    return f"file://{src}"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("OKF_REMOTE_OFFLINE", raising=False)
    # ambient GIT_ALLOW_PROTOCOL이 file을 배제하면 file:// 테스트가 깨진다 — _git_env의
    # setdefault 기본값(file 포함)이 적용되도록 제거해 결정론화(D6).
    monkeypatch.delenv("GIT_ALLOW_PROTOCOL", raising=False)
    # 신선도 dedup(ttl·backoff)을 기본 꺼 실제 fetch를 강제 — 각 창은 명시 테스트에서 켠다.
    monkeypatch.setenv("OKF_REMOTE_FETCH_TTL", "0")
    monkeypatch.setenv("OKF_REMOTE_FETCH_BACKOFF", "0")


# --- clone (옵트인·멱등·원자성) ----------------------------------------------


def test_clone_materializes_and_vault_state_resolves(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    # set 전: home_state는 순수 판정으로 '미생성'
    assert okf_vault.vault_state() == (None, okf_vault.INVALID_CLONE_MISSING)
    result = okf_remote.clone()
    assert result["cloned"] is True and result["valid"] is True
    clone_path = result["clone_path"]
    # clone 후: home_state가 관리형 clone 로컬 경로로 해소(하류 무변경 전제)
    assert okf_vault.vault_state() == (clone_path, None)
    assert okf_vault.managed_clone_path(okf_vault.canonicalize_url(_url(src))) == Path(clone_path)


def test_clone_is_idempotent(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    okf_remote.clone()
    again = okf_remote.clone()
    assert again["cloned"] is False and again["valid"] is True and "재사용" in again["reason"]


def test_clone_replaces_torn_clone(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    # 반쪽 clone: 디렉토리만 있고 .git 없음
    dest = okf_vault.managed_clone_path(okf_vault.canonicalize_url(url))
    (dest / "junk").mkdir(parents=True)
    assert not okf_vault.valid_vault(dest)
    result = okf_remote.clone()
    assert result["cloned"] is True and okf_vault.valid_vault(dest)


def test_clone_bad_transport_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, "svn://example.com/o/r")
    result = okf_remote.clone()
    assert result["cloned"] is False and result["reason"] == okf_vault.INVALID_URL_TRANSPORT


def test_clone_noop_for_local_path_pointer(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "local"))
    result = okf_remote.clone()
    assert result["cloned"] is False and "URL 포인터 아님" in result["reason"]


# --- session_fetch (fetch-only·TTL·오프라인) ---------------------------------


def test_session_fetch_skips_when_clone_missing(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    # clone 미생성 — SessionStart는 만들지 않는다(옵트인)
    assert okf_remote.session_fetch()["skipped"] == "clone 미생성"


def test_session_fetch_pulls_new_refs_without_touching_worktree(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = okf_remote.clone()["clone_path"]
    # 원격 전진
    (src / "new.md").write_text("x\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "advance")
    assert okf_remote.session_fetch()["fetched"] is True
    # fetch-only: worktree는 그대로(behind 1), ff는 하지 않는다(U3-2)
    _ahead, behind = okf_remote._ahead_behind(clone_path)
    assert behind == 1


def test_session_fetch_ttl_dedup(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    okf_remote.clone()  # clone이 last_fetch를 스탬프한다
    monkeypatch.setenv("OKF_REMOTE_FETCH_TTL", "9999")
    # 방금 clone 스탬프가 TTL 안이므로 SessionStart 재발화는 fetch를 dedup한다
    assert okf_remote.session_fetch().get("skipped") == "ttl"


def test_session_fetch_offline_env_skips(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    okf_remote.clone()
    monkeypatch.setenv("OKF_REMOTE_OFFLINE", "1")
    assert okf_remote.session_fetch()["skipped"] == "offline env"


def test_session_fetch_noop_for_local_pointer(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, str(tmp_path / "local"))
    assert okf_remote.session_fetch()["skipped"] == "URL 포인터 아님"


def test_session_fetch_failure_backs_off(monkeypatch, tmp_path):
    # D3: 오프라인/실패 fetch는 last_attempt를 스탬프해 다음 SessionStart를 backoff로 skip
    # → 매 시작마다 재시도 스톨하지 않는다.
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = okf_remote.clone()["clone_path"]
    okf_remote._sync_meta_path(clone_path).write_text("{}", encoding="utf-8")  # 스탬프 초기화
    import shutil

    shutil.rmtree(src)  # 원격 소멸 → fetch 실패(오프라인 재현)
    monkeypatch.setenv("OKF_REMOTE_FETCH_BACKOFF", "9999")
    # 첫 시도: 실패하지만 last_attempt를 남긴다
    assert okf_remote.session_fetch()["fetched"] is False
    # 둘째 시도: backoff 창 안이라 네트워크를 다시 타지 않고 skip
    assert okf_remote.session_fetch().get("skipped") == "backoff"


@pytest.mark.skipif(okf_remote.fcntl is None, reason="POSIX flock 필요")
def test_refresh_skips_when_clone_locked(monkeypatch, tmp_path):
    # D4: 다른 세션이 clone 갱신 중(락 점유)이면 refresh는 'locked'로 저하한다(worktree 경합 방지).
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = okf_remote.clone()["clone_path"]
    lock_fd = os.open(str(Path(clone_path) / ".git" / "okf-remote.lock"), os.O_CREAT | os.O_RDWR)
    okf_remote.fcntl.flock(lock_fd, okf_remote.fcntl.LOCK_EX)  # 다른 세션 점유 재현
    try:
        result = okf_remote.refresh()
        assert result["refreshed"] is False and result["reason"] == "locked"
    finally:
        os.close(lock_fd)


# --- refresh (clean-gate ff-only) --------------------------------------------


def test_refresh_ff_advances_clean_clone(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = okf_remote.clone()["clone_path"]
    (src / "new.md").write_text("x\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "advance")
    assert okf_remote.refresh()["refreshed"] is True
    _ahead, behind = okf_remote._ahead_behind(clone_path)
    assert behind == 0  # ff로 최신 base


def test_refresh_skips_dirty_clone(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = okf_remote.clone()["clone_path"]
    # 승격 잔재 시뮬레이션 — 추적 파일 수정(index.md는 승격마다 재생성됨)
    (Path(clone_path) / ".okf" / "index.md").write_text("# dirty\n", encoding="utf-8")
    result = okf_remote.refresh()
    assert result["refreshed"] is False and result["reason"] == "dirty" and result["warning"]


def test_refresh_offline_env_degrades(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    okf_remote.clone()
    monkeypatch.setenv("OKF_REMOTE_OFFLINE", "1")
    result = okf_remote.refresh()
    assert result["refreshed"] is False and result["reason"] == "offline env"


# --- doctor (무네트워크 표시) --------------------------------------------------


def test_doctor_notes_missing_clone(monkeypatch, tmp_path):
    monkeypatch.setenv(okf_vault.VAULT_ENV, "git@example.com:o/r.git")
    notes = okf_remote.doctor_vault_notes("git@example.com:o/r.git")
    joined = "\n".join(notes)
    assert "URL(관리형 clone)" in joined and "미생성" in joined


def test_doctor_notes_valid_clone_shows_freshness(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    okf_remote.clone()
    notes = okf_remote.doctor_vault_notes(url)
    joined = "\n".join(notes)
    assert "마지막 fetch" in joined and "clone:" in joined


def test_doctor_notes_bad_transport(monkeypatch, tmp_path):
    notes = okf_remote.doctor_vault_notes("ext::sh -c evil")
    assert "미지원 transport" in "\n".join(notes)


def test_dualization_detected_for_local_twin(monkeypatch, tmp_path):
    # 로컬 경로 vault이 같은 origin의 관리형 clone과 이원화된 상황 감지
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    okf_remote.clone()  # 관리형 clone 물질화
    # 같은 origin(URL)을 로컬 경로로도 clone(별도 위치) → canonical 일치로 이원화
    local = tmp_path / "local-clone"
    _git(tmp_path, "clone", url, str(local))
    note = okf_remote.dualization_note(str(local), str(local))
    assert note is not None and "이원화" in note
