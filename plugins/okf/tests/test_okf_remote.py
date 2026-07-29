"""관리형 clone 계층 테스트 (#153) — file:// 로컬 원격으로 무네트워크 실증.

clone/fetch/refresh/doctor 표시를 실제 git으로 돌리되, 원격은 로컬 file:// repo라
CI에서 네트워크·인증 없이 성립한다(C2-1 헤르메틱 시임). 원칙 검증: resolver 무네트워크·
SessionStart fetch-only·clean-gate ff·오프라인 저하·미생성 옵트인.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import time
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
        assert result["refreshed"] is False and result["code"] == okf_remote.CODE_LOCKED
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


def test_refresh_advances_despite_residue_when_no_conflict(monkeypatch, tmp_path):
    """선판정 폐기(#225) — dirty여도 **경로 충돌이 없으면** git이 ff를 허용하고 잔재는 보존된다.

    구계약("dirty면 무조건 생략")을 대체한다. `_is_dirty` bool 게이트가 git 자신보다
    엄격해 안전한 ff까지 거부한 것이 #216 정체의 wedge 기제였다.
    """
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    # 승격 잔재 시뮬레이션 — 추적 파일 수정(index.md는 승격마다 재생성됨)
    (clone_path / ".okf" / "index.md").write_text("# dirty\n", encoding="utf-8")
    # upstream은 **다른 경로**로 전진 — 충돌 없음
    (src / "unrelated.md").write_text("y\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "advance elsewhere")
    result = okf_remote.refresh()
    assert result["refreshed"] is True
    _ahead, behind = okf_remote._ahead_behind(clone_path)
    assert behind == 0
    # 미푸시 잔재는 그대로 살아 있어야 한다(주입 연속성)
    assert (clone_path / ".okf" / "index.md").read_text(encoding="utf-8") == "# dirty\n"


def test_refresh_offline_env_degrades(monkeypatch, tmp_path):
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    okf_remote.clone()
    monkeypatch.setenv("OKF_REMOTE_OFFLINE", "1")
    result = okf_remote.refresh()
    assert result["refreshed"] is False and result["reason"] == "offline env"


# --- 잔재 회수 (#225 V1 — 봉인 판정 기반 자가 회복) ----------------------------


def _merge_into_origin(src, rel: str, body: str) -> None:
    """origin에 <rel>을 <body>로 커밋한다 — PR 리뷰가 머지된 시점의 재현."""
    target = Path(src) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", f"merge {rel}")


def test_refresh_recovers_sealed_untracked_residue(monkeypatch, tmp_path):
    """#216 사고 형태 — 미추적 잔재가 origin에 **같은 내용**으로 머지되면 ff가 영구 차단된다.

    바이트 동일해도 git은 'untracked working tree files would be overwritten'로 거부하므로
    선판정 폐기만으로는 풀리지 않는다. 봉인(원격추적 ref 트리에 blob 존재)을 증명해 폐기한다.
    """
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# promoted\n"
    (clone_path / ".okf" / "new.md").write_text(body, encoding="utf-8")  # 승격 잔재(미추적)
    _merge_into_origin(src, ".okf/new.md", body)  # 같은 내용이 머지됨 → 봉인
    result = okf_remote.refresh()
    assert result["refreshed"] is True
    assert result["discarded"] == [".okf/new.md"]
    _ahead, behind = okf_remote._ahead_behind(clone_path)
    assert behind == 0
    assert okf_remote._is_dirty(clone_path) is False


def test_refresh_recovers_sealed_staged_residue(monkeypatch, tmp_path):
    """staged 잔재도 회수된다 — `checkout --`는 index를 못 되돌려 wedge가 남는다(회귀 게이트)."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# staged\n"
    (clone_path / ".okf" / "new.md").write_text(body, encoding="utf-8")
    _git(clone_path, "add", ".okf/new.md")  # index에 A 엔트리로 올림
    _merge_into_origin(src, ".okf/new.md", body)
    result = okf_remote.refresh()
    assert result["refreshed"] is True
    assert okf_remote._is_dirty(clone_path) is False  # index까지 clean


def test_refresh_recovers_sealed_modified_residue(monkeypatch, tmp_path):
    """추적 파일 수정도 같은 내용이 머지됐으면 봉인이므로 폐기된다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# index v2\n"
    (clone_path / ".okf" / "index.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/index.md", body)
    result = okf_remote.refresh()
    assert result["refreshed"] is True
    assert okf_remote._is_dirty(clone_path) is False


def test_refresh_preserves_unsealed_conflicting_residue(monkeypatch, tmp_path):
    """미봉인 잔재는 폐기하지 않는다 — 어디에도 push되지 않은 지식의 유실 금지(fail-safe)."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    mine = "# mine — unpushed\n"
    (clone_path / ".okf" / "c.md").write_text(mine, encoding="utf-8")
    _merge_into_origin(src, ".okf/c.md", "# theirs\n")  # 같은 경로, **다른** 내용
    result = okf_remote.refresh()
    assert result["refreshed"] is False
    assert result["reason"] == "미봉인 잔재"
    assert result["warning"]
    assert (clone_path / ".okf" / "c.md").read_text(encoding="utf-8") == mine  # 보존


def test_refresh_discards_only_sealed_paths(monkeypatch, tmp_path):
    """봉인·미봉인이 섞이면 봉인된 것만 폐기하고 미봉인은 남긴다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    sealed_body = "# sealed\n"
    (clone_path / ".okf" / "sealed.md").write_text(sealed_body, encoding="utf-8")
    (clone_path / ".okf" / "unsealed.md").write_text("# unsealed\n", encoding="utf-8")
    _merge_into_origin(src, ".okf/sealed.md", sealed_body)
    result = okf_remote.refresh()
    assert result["refreshed"] is True
    assert result["discarded"] == [".okf/sealed.md"]
    # 폐기는 삭제가 아니라 **원격 버전으로의 대체**다 — ff가 origin 사본을 추적 파일로 되살린다
    assert (clone_path / ".okf" / "sealed.md").read_text(encoding="utf-8") == sealed_body
    residue = {rel for _xy, rel in okf_remote.list_residue(clone_path)}
    assert ".okf/sealed.md" not in residue  # 더는 잔재가 아니다
    assert ".okf/unsealed.md" in residue  # 미봉인은 잔재로 남아 주입에 계속 보인다


def test_list_residue_expands_untracked_directory(monkeypatch, tmp_path):
    """미추적 **디렉터리**가 개별 파일로 펼쳐진다 — `?? dir/` 접힘은 폐기를 무동작으로 만든다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    topic = clone_path / ".okf" / "topic"
    topic.mkdir(parents=True)
    (topic / "a.md").write_text("a\n", encoding="utf-8")
    (topic / "b.md").write_text("b\n", encoding="utf-8")
    rels = {rel for _xy, rel in okf_remote.list_residue(clone_path)}
    assert ".okf/topic/a.md" in rels
    assert ".okf/topic/b.md" in rels
    assert ".okf/topic/" not in rels


def test_list_residue_handles_non_ascii_paths(monkeypatch, tmp_path):
    """비ASCII 경로가 인용부호 없이 그대로 나온다(`-z`) — 인용된 경로는 폐기가 빗나간다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "한글.md").write_text("x\n", encoding="utf-8")
    rels = {rel for _xy, rel in okf_remote.list_residue(clone_path)}
    assert ".okf/한글.md" in rels


def test_sealed_paths_matches_per_path_blob_not_whole_tree(monkeypatch, tmp_path):
    """봉인은 **경로별 blob 동치** — 무관한 upstream 커밋이 더 있어도 성립한다.

    전체 tree 동치로 잡으면 다른 머신의 승격 하나만 끼어도 판정이 깨져 발화하지 않는다.
    """
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# sealed\n"
    (clone_path / ".okf" / "s.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/s.md", body)
    _merge_into_origin(src, "noise.md", "noise\n")  # 무관한 후속 커밋
    okf_remote.session_fetch()  # 원격추적 ref 갱신(무네트워크 판정의 전제)
    assert okf_remote.sealed_paths(clone_path, [".okf/s.md"]) == {".okf/s.md"}


def test_sealed_paths_empty_without_remote_match(monkeypatch, tmp_path):
    """어떤 원격추적 ref에도 없는 내용은 봉인되지 않는다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "u.md").write_text("# never pushed\n", encoding="utf-8")
    assert okf_remote.sealed_paths(clone_path, [".okf/u.md"]) == set()


def test_refresh_keeps_diverged_clone_untouched(monkeypatch, tmp_path):
    """로컬 커밋으로 ff 불가(diverged)면 잔재 폐기 경로에 진입하지 않는다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    _git(clone_path, "config", "user.email", "t@example.com")
    _git(clone_path, "config", "user.name", "t")
    (clone_path / "local.md").write_text("local\n", encoding="utf-8")
    _git(clone_path, "add", "-A")
    _git(clone_path, "commit", "-m", "local commit")
    _merge_into_origin(src, "remote.md", "remote\n")
    result = okf_remote.refresh()
    assert result["refreshed"] is False
    assert result["code"] == okf_remote.CODE_DIVERGED
    assert (clone_path / "local.md").exists()


# --- upstream 부재·detached는 회수에 들어가지 않는다 (#298) ---------------------
#
# ff 실패 후 회수 진입은 `_ahead_behind`의 `ahead`가 falsy인지로 갈렸다. 그런데
# `_ahead_behind`는 rc≠0을 `(None, None)`으로 흡수하므로 **upstream 부재·detached HEAD가
# `ahead == 0`과 동치**가 되어 회수 경로로 들어갔다. 실측(변경 전): detached clone에서
# `_recover_and_ff`가 봉인 잔재를 실제로 지우고(`discarded: [...]`) `미봉인 잔재`를
# 반환한다 — `study.md`는 그 사유에 "**폐기하지 않았다**"를 안내하라고 지시하므로,
# 사용자가 받는 안내가 사실과 정반대였다.


def _detach(clone_path):
    rc, out, _err = okf_remote._run_git(["rev-parse", "HEAD"], cwd=str(clone_path))
    assert rc == 0
    _git(clone_path, "checkout", "--detach", out.strip())


def test_refresh_does_not_recover_on_detached_head(monkeypatch, tmp_path):
    """detached HEAD면 회수에 진입하지 않는다 — 봉인 잔재가 있어도 지우지 않는다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# promoted\n"
    (clone_path / ".okf" / "new.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/new.md", body)  # 봉인 잔재 — 회수 대상이 되면 지워진다
    _detach(clone_path)

    result = okf_remote.refresh()
    assert result["refreshed"] is False
    assert result["code"] == okf_remote.CODE_DETACHED, result
    assert not result.get("discarded"), "detached인데 폐기했다 — 회수에 진입하면 안 된다"
    assert (clone_path / ".okf" / "new.md").exists()  # 보존


def test_refresh_does_not_recover_without_upstream(monkeypatch, tmp_path):
    """upstream 미설정 브랜치면 회수에 진입하지 않는다."""
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# promoted\n"
    (clone_path / ".okf" / "new.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/new.md", body)
    _git(clone_path, "branch", "--unset-upstream", okf_remote._current_branch(clone_path))

    result = okf_remote.refresh()
    assert result["refreshed"] is False
    assert result["code"] == okf_remote.CODE_NO_UPSTREAM, result
    assert not result.get("discarded")
    assert (clone_path / ".okf" / "new.md").exists()


def test_recover_and_ff_splits_discarded_from_untouched(monkeypatch, tmp_path):
    """폐기한 실패와 아무것도 못 지운 실패는 **다른 코드**다.

    둘 다 `unsealed_residue`이면 "폐기하지 않았다" 안내가 폐기한 경우에도 나간다.
    """
    src = _origin(tmp_path)
    monkeypatch.setenv(okf_vault.VAULT_ENV, _url(src))
    clone_path = Path(okf_remote.clone()["clone_path"])

    # 봉인 0건 — 아무것도 지우지 못한다
    monkeypatch.setattr(okf_remote, "reclaim_sealed", lambda _p: [])
    untouched = okf_remote._recover_and_ff(clone_path, 5.0)
    assert untouched["code"] == okf_remote.CODE_UNSEALED_RESIDUE
    assert not untouched.get("discarded")

    # 지웠는데도 ff 재시도 실패 — 남은 잔재는 미봉인이지만 **폐기는 일어났다**
    monkeypatch.setattr(okf_remote, "reclaim_sealed", lambda _p: [".okf/gone.md"])
    monkeypatch.setattr(okf_remote, "_ff", lambda _p, _t: 1)
    retried = okf_remote._recover_and_ff(clone_path, 5.0)
    assert retried["code"] == okf_remote.CODE_FF_RETRY_FAILED, retried
    assert retried["discarded"] == [".okf/gone.md"]


def test_study_md_guides_no_discard_only_when_nothing_discarded():
    """ "폐기하지 않았다" 안내는 `unsealed_residue`에만 붙는다 — `ff_retry_failed`에는 없다.

    같은 사유를 공유하던 두 상황을 코드로 가른 이유가 이것이다. 문서가 다시 합치면 red.
    """
    body = (_SOURCE.parents[2] / "commands" / "study.md").read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if "`ff_retry_failed`" in ln]
    assert lines, "study.md에 ff_retry_failed 분기가 없다"
    for line in lines:
        assert "폐기하지 않았다" not in line, (
            "ff_retry_failed 분기가 '폐기하지 않았다'를 안내한다 — 실제로는 폐기했다"
        )


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


# --- doctor 잔재 진단 (#227 V3 — 봉인 여부로 구분) ------------------------------


def test_doctor_marks_sealed_residue_as_auto_recoverable(monkeypatch, tmp_path):
    """원격에 반영된 잔재는 자동 정리 대상이라고 알린다 — 사용자가 손댈 일이 아니다."""
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# sealed\n"
    (clone_path / ".okf" / "s.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/s.md", body)
    okf_remote.session_fetch()  # 원격추적 ref 갱신 + last_fetch 스탬프
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "원격에 반영됨" in joined
    assert "유실" not in joined  # 봉인된 것에 유실 경고를 붙이면 안 된다


def test_doctor_warns_unsealed_residue_against_discard(monkeypatch, tmp_path):
    """원격 미반영 잔재는 폐기하면 유실이라고 명시한다.

    구 문구 '디스패치(커밋) 또는 폐기 필요'는 정반대 결과를 낳는 둘을 뭉뚱그렸다.
    """
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "u.md").write_text("# never pushed\n", encoding="utf-8")
    okf_remote.session_fetch()
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "미반영" in joined and "유실" in joined


def test_doctor_withholds_seal_verdict_when_fetch_is_stale(monkeypatch, tmp_path):
    """fetch가 오래됐으면 봉인 판정을 확정으로 제시하지 않는다.

    판정 근거가 **이미 fetch된** ref뿐이라, 오래된 clone에서는 실제로 미푸시인 작업을
    '반영됨'으로 오판할 수 있다. 그 안내를 따라 폐기하면 비가역 손실이다.
    """
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    body = "# sealed\n"
    (clone_path / ".okf" / "s.md").write_text(body, encoding="utf-8")
    _merge_into_origin(src, ".okf/s.md", body)
    okf_remote.session_fetch()
    okf_remote._stamp(clone_path, last_fetch=time.time() - 10 * 86400)  # 10일 전으로 되돌림
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "판정 보류" in joined
    assert "자동 정리" not in joined


def test_doctor_points_to_wiring_when_no_handler(monkeypatch, tmp_path):
    """핸들러 미배선 vault에서 '디스패치로 반영하라'는 **실행 불가능한 지시**다(#228).

    `/okf-promote`나 미배선 vault의 승격이 딱 이 상태를 만든다 — 원격 반영 경로 자체가
    없으므로 배선을 안내해야 사용자가 실제로 할 수 있는 일이 생긴다.
    """
    src = _origin(tmp_path)  # 기본 config엔 study.handlers 없음
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "u.md").write_text("# never pushed\n", encoding="utf-8")
    okf_remote.session_fetch()
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "핸들러 미배선" in joined
    assert "디스패치(PR)로 반영" not in joined  # 못 하는 일을 시키지 않는다
    assert "유실" in joined  # 폐기 위험 경고는 유지


def test_doctor_points_to_dispatch_when_handler_wired(monkeypatch, tmp_path):
    """핸들러가 배선돼 있으면 디스패치가 실제 경로이므로 그대로 안내한다."""
    src = _origin(
        tmp_path,
        config={
            "bundlePath": ".okf",
            "study": {"handlers": [{"name": "h", "command": "scripts/h.py"}]},
        },
    )
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "u.md").write_text("# never pushed\n", encoding="utf-8")
    okf_remote.session_fetch()
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "디스패치(PR)로 반영" in joined
    assert "핸들러 미배선" not in joined


def test_doctor_says_nothing_about_residue_when_clean(monkeypatch, tmp_path):
    """잔재가 없으면 잔재 줄을 아예 내지 않는다(침묵 정책)."""
    src = _origin(tmp_path)
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    okf_remote.clone()
    joined = "\n".join(okf_remote.doctor_vault_notes(url))
    assert "잔재" not in joined


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


def test_recovery_route_is_wiring_aware(tmp_path):
    """회복 라우트가 배선 여부로 갈린다 — 단일원천(#275, Epic #266 U3).

    미배선 vault에 "디스패치로 반영하라"는 실행 불가능한 지시다(#216 V4). doctor 경로는
    이미 그렇게 갈리는데(#239) ff 정체 경고는 그 분기를 못 받았다. 두 소비처가 같은
    헬퍼를 쓰게 해서 한쪽만 고쳐지는 드리프트를 없앤다.
    """
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    assert "배선" in okf_remote._recovery_route(vault)
    assert "디스패치" not in okf_remote._recovery_route(vault)

    (vault / ".okf-wiki.json").write_text(
        '{"study": {"handlers": [{"name": "h", "command": "x"}]}}', encoding="utf-8"
    )
    assert "디스패치" in okf_remote._recovery_route(vault)


def test_ff_stall_warning_routes_by_wiring(tmp_path, monkeypatch):
    """미배선 clone의 ff 정체 경고가 못 하는 일을 시키지 않는다.

    현행은 배선 여부와 무관하게 "디스패치(PR)로 반영하라"를 낸다 — 반영 경로가 없는
    사용자에게는 막다른 안내다.
    """
    src = _origin(tmp_path)  # 기본 config엔 study.handlers 없음
    url = _url(src)
    monkeypatch.setenv(okf_vault.VAULT_ENV, url)
    clone_path = Path(okf_remote.clone()["clone_path"])
    (clone_path / ".okf" / "stale.md").write_text("# never pushed\n", encoding="utf-8")
    result = okf_remote._recover_and_ff(clone_path, 5.0)
    assert result["refreshed"] is False and result["reason"] == "미봉인 잔재"  # 기계 축 불변
    assert "디스패치" not in result["warning"]
    assert "배선" in result["warning"]


def _residue_repo(tmp_path):
    """번들 안팎에 잔재가 있는 repo — pathspec 범위 한정 검증용."""
    okf_remote._run_git(["init"], cwd=str(tmp_path))
    (tmp_path / ".okf" / "sub").mkdir(parents=True)
    (tmp_path / ".okf" / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / ".okf" / "sub" / "b.md").write_text("y", encoding="utf-8")
    (tmp_path / "src.txt").write_text("사용자 실작업", encoding="utf-8")
    return tmp_path


def test_list_residue_default_is_unchanged(tmp_path):
    """pathspec 미지정은 **현행 그대로** — 기본값이 동작을 바꾸지 않는다."""
    repo = _residue_repo(tmp_path)
    rels = {rel for _xy, rel in okf_remote.list_residue(repo)}
    assert rels == {".okf/a.md", ".okf/sub/b.md", "src.txt"}


def test_list_residue_pathspec_scopes_to_bundle(tmp_path):
    """pathspec을 주면 그 하위만 — 번들 밖 사용자 실작업이 잔재 목록에 오르지 않는다.

    #277(U5)이 로컬 경로 vault로 회계를 넓힐 때 이 범위 한정이 없으면 repo 전체가
    열거되고, 그 목록이 폐기 후보로 흐른다. 잘못된 폐기는 비가역이다.
    """
    repo = _residue_repo(tmp_path)
    rels = {rel for _xy, rel in okf_remote.list_residue(repo, pathspec=".okf")}
    assert rels == {".okf/a.md", ".okf/sub/b.md"}
    assert "src.txt" not in rels


def test_list_residue_pathspec_is_literal(tmp_path):
    """glob 문자가 든 디렉터리명도 **문자 그대로** 매칭한다(`:(literal)` 매직).

    번들 경로는 소비처가 정하므로 `[`·`*`가 들어갈 수 있다. glob으로 해석되면 엉뚱한
    범위가 잡히고, 그 목록이 폐기로 흐른다.
    """
    repo = tmp_path
    okf_remote._run_git(["init"], cwd=str(repo))
    (repo / "b[1]").mkdir()
    (repo / "b[1]" / "x.md").write_text("x", encoding="utf-8")
    (repo / "b1").mkdir()
    (repo / "b1" / "y.md").write_text("y", encoding="utf-8")
    rels = {rel for _xy, rel in okf_remote.list_residue(repo, pathspec="b[1]")}
    assert rels == {"b[1]/x.md"}  # glob이면 b1/y.md가 잡힌다


def test_discard_paths_keeps_managed_clone_guard(tmp_path):
    """폐기 경로의 관리형 clone 가드는 **손대지 않는다** — 일반화는 열거에서만.

    #216이 배운 비대칭: 잘못된 폐기는 비가역이고 잘못된 보존은 재현 가능한 소음이다.
    """
    import inspect

    src = inspect.getsource(okf_remote.reclaim_sealed)
    assert "pathspec" not in src, "reclaim_sealed가 pathspec을 받으면 폐기 범위가 넓어진다"


def _local_vault_with_residue(tmp_path):
    """로컬 경로 vault — 번들 안 잔재 1건 + 번들 밖 사용자 실작업 1건."""
    vault = tmp_path / "kb"
    (vault / ".okf").mkdir(parents=True)
    _git(vault, "init")
    _git(vault, "config", "user.email", "t@e.com")
    _git(vault, "config", "user.name", "t")
    (vault / ".okf-wiki.json").write_text("{}", encoding="utf-8")
    (vault / ".okf" / "residue.md").write_text("# 미반영\n", encoding="utf-8")
    (vault / "draft.txt").write_text("사용자 실작업", encoding="utf-8")
    return vault


def test_local_residue_notes_scopes_to_bundle(tmp_path):
    """로컬 경로 vault도 잔재 회계를 받는다 — 단 **번들 범위 안에서만**(#277, Epic #266 U5).

    pathspec 없이 켜면 repo 전체가 열거돼 사용자의 번들 밖 실작업이 잔재로 보고된다.
    """
    vault = _local_vault_with_residue(tmp_path)
    joined = "\n".join(okf_remote.local_residue_notes(vault, pathspec=".okf"))
    assert "잔재 1건" in joined
    assert "draft.txt" not in joined


def test_local_residue_notes_does_not_claim_stale_fetch(tmp_path):
    """로컬 vault에 "fetch가 오래됨"은 **거짓**이다 — 동기화 이력 자체가 없다.

    `.git/okf-sync.json`은 okf 관리형 clone만 스탬프한다. 로컬 vault는 `last_fetch`가
    없어 stale이 항상 참이 되는데, 그 사유를 '노후'로 말하면 사용자가 fetch를 시도하게
    된다 — Epic #266이 없애려는 '실행 불가능한 지시'와 같은 부류다.
    """
    vault = _local_vault_with_residue(tmp_path)
    joined = "\n".join(okf_remote.local_residue_notes(vault, pathspec=".okf"))
    assert "fetch가 오래됨" not in joined


def test_local_residue_notes_does_not_promise_auto_cleanup(tmp_path):
    """봉인 분기를 **실제로 태워** 자동 정리를 약속하지 않음을 확인한다.

    이전 판은 sealed가 0이라 notes가 비어 통과했다 — 잘못된 이유로 녹색이었다(DA 지적).
    여기서는 원격이 앞선 뒤 fetch하고, 그 경로를 로컬에 **같은 내용**으로 미리 둬서
    `sealed_paths`가 실제로 잡게 만든다(경로별 blob 일치가 판정 단위다).

    그때 나와야 하는 것은 "okf에 fetch 이력이 없어 판정 보류"다. 로컬 vault는 okf가
    스탬프하지 않으므로 '노후'가 아니라 '이력 부재'이고, 자동 정리는 관리형 clone의
    성질이라 약속할 수 없다.
    """
    origin = _origin(tmp_path)
    vault = tmp_path / "kb"
    _git(tmp_path, "clone", str(origin), str(vault))
    (origin / ".okf" / "shared.md").write_text("# shared\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "advance")
    _git(vault, "fetch")
    (vault / ".okf" / "shared.md").write_text("# shared\n", encoding="utf-8")

    rels = [rel for _xy, rel in okf_remote.list_residue(vault, pathspec=".okf")]
    assert okf_remote.sealed_paths(vault, rels), "봉인 분기를 타지 못했다 — 테스트가 무의미하다"

    joined = "\n".join(okf_remote.local_residue_notes(vault, pathspec=".okf"))
    assert "자동 정리" not in joined
    assert "fetch가 오래됨" not in joined  # 이력이 없는 것이지 낡은 게 아니다
    assert "fetch 이력이 없어" in joined


def test_local_pointer_to_managed_clone_uses_clone_wording(tmp_path):
    """로컬 경로 포인터가 **관리형 clone**을 가리켜도 문구가 사실과 맞는다(#266 U5).

    DA 리뷰가 제기한 경로다 — 라우팅은 포인터 형식으로 갈리는데 판정은 vault 성질에
    달려 있다. 그 vault엔 fetch 스탬프가 있으므로 '이력 없음'이 아니라 정상 판정이
    나와야 하고, 자동 정리 안내도 실제로 맞다(`/study`가 그 clone을 회수한다).
    """
    origin = _origin(tmp_path)
    vault = tmp_path / "clone-like"
    _git(tmp_path, "clone", str(origin), str(vault))
    okf_remote._stamp(vault, last_fetch=time.time())  # 관리형 clone처럼 스탬프
    (origin / ".okf" / "shared.md").write_text("# shared\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "advance")
    _git(vault, "fetch")
    (vault / ".okf" / "shared.md").write_text("# shared\n", encoding="utf-8")

    joined = "\n".join(okf_remote.local_residue_notes(vault, pathspec=".okf"))
    assert "fetch 이력이 없어" not in joined  # 스탬프가 있으므로 거짓이 아니어야 한다
    assert "자동 정리" in joined  # 실제로 회수되므로 이 안내가 맞다


# --- refresh 사유는 닫힌 코드 집합이다 (#297) -----------------------------------
#
# `study.md` 0a가 한국어 `reason` 리터럴로 분기하는데, 그 값 집합이 코드에서 닫혀 있지
# 않았다. 문서 분기는 3종인데 코드 반환은 8종이라 `locked`·`clone 미생성`·미지원
# transport는 모델에 지정된 행동이 없었다. #274가 dispatch에 한 전환의 잔여 축이다.


def test_refresh_reasons_have_recovery():
    """모든 refresh 코드에 실행 가능한 복구 지시가 있다(BLOCKERS와 같은 형태)."""
    assert okf_remote.REFRESH_REASONS, "REFRESH_REASONS 상수가 없다"
    for code, recovery in okf_remote.REFRESH_REASONS.items():
        assert recovery.strip(), f"{code}에 복구 지시가 없다"


def test_study_md_branches_on_every_refresh_code():
    """`study.md`가 **모든** refresh 코드에 분기를 갖는다 — 코드가 늘면 문서 미갱신이 red.

    `test_dispatch_verdict.py`의 `test_commands_branch_on_every_blocker_code`와 동형이다.
    코드값 표기(백틱)를 요구해 산문에 우연히 걸리는 것을 막는다.
    """
    body = (Path(okf_remote.__file__).resolve().parents[2] / "commands" / "study.md").read_text(
        encoding="utf-8"
    )
    missing = [c for c in okf_remote.REFRESH_REASONS if f"`{c}`" not in body]
    assert not missing, f"study.md에 분기 없는 refresh 코드: {missing}"


def test_non_url_pointer_returns_not_url_code(monkeypatch):
    """URL 포인터가 아니면 `not_url` — 한국어 `reason`은 사람용 표시로만 남는다."""
    monkeypatch.setattr(okf_vault, "read_pointer", lambda: None)
    out = okf_remote.refresh()
    assert out["code"] == okf_remote.CODE_NOT_URL, out
    assert out["refreshed"] is False
    assert out["reason"] == okf_remote.NOT_URL_POINTER  # 사람용 표시는 남는다


# --- 코드 축의 구조 게이트 (#297 DA리뷰) ---------------------------------------
#
# 위 세 테스트는 **문서 → 코드** 방향만 잠근다(REFRESH_REASONS에 있는 것이 study.md에
# 있는가). 반대 방향 — 새 반환 경로가 `code` 없이 들어오거나, 새 `CODE_*`가
# REFRESH_REASONS를 거치지 않고 반환되는 것 — 은 비어 있었다. 그러면 소비자가 받는
# `code`는 `None`이고 study.md에 대응 분기가 없어 **조용히 흘러간다**. 이 유닛이
# 없애려는 무음 스킵이 판정 축 안에 다시 생기는 꼴이라 구조로 잠근다.

_SOURCE = Path(okf_remote.__file__).resolve()
# 반환 dict에 `code`가 실려야 하는 함수. 서로에게 위임하는 것은 허용한다.
_CODE_GATED = ("refresh", "_recover_and_ff")


def _module_ast() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _toplevel_func(name: str) -> ast.FunctionDef:
    for node in _module_ast().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}: 최상위 함수를 찾지 못했다")


def _returns(func: ast.FunctionDef) -> list[ast.Return]:
    return [n for n in ast.walk(func) if isinstance(n, ast.Return) and n.value is not None]


@pytest.mark.parametrize("func_name", _CODE_GATED)
def test_every_return_carries_code(func_name: str):
    """`refresh`/`_recover_and_ff`의 **모든** dict 반환에 `code` 키가 있다.

    dict 리터럴이 아닌 반환은 게이트 대상 함수로의 위임만 허용한다 — 게이트를 우회하는
    간접 반환이 생기지 않게.
    """
    for ret in _returns(_toplevel_func(func_name)):
        if isinstance(ret.value, ast.Dict):
            keys = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
            assert "code" in keys, (
                f"{func_name} L{ret.lineno}: `code` 없는 dict 반환 — "
                f"소비자가 분기할 기계 축이 사라진다(키: {sorted(keys)})"
            )
            continue
        delegated = (
            isinstance(ret.value, ast.Call) and getattr(ret.value.func, "id", None) in _CODE_GATED
        )
        assert delegated, (
            f"{func_name} L{ret.lineno}: dict 리터럴도 게이트 함수 위임도 아닌 반환 — "
            "`code`가 실리는지 정적으로 확인할 수 없다"
        )


def test_returned_code_constants_are_registered():
    """반환에 직접 쓰인 `CODE_*` 상수가 전부 REFRESH_REASONS에 등록돼 있다."""
    for func_name in _CODE_GATED:
        for ret in _returns(_toplevel_func(func_name)):
            if not isinstance(ret.value, ast.Dict):
                continue
            for key, value in zip(ret.value.keys, ret.value.values):
                if not (isinstance(key, ast.Constant) and key.value == "code"):
                    continue
                if not (isinstance(value, ast.Name) and value.id.startswith("CODE_")):
                    continue  # 변수·조회식은 아래 닫힘 테스트가 대신 잠근다
                assert getattr(okf_remote, value.id) in okf_remote.REFRESH_REASONS, (
                    f"{func_name} L{ret.lineno}: {value.id}가 REFRESH_REASONS에 없다 — "
                    "복구 지시도 문서 분기 게이트도 이 코드를 보지 못한다"
                )


def test_code_constants_are_closed_over_refresh_reasons():
    """모듈의 `CODE_*` 상수 집합 == REFRESH_REASONS 키 집합.

    상수만 늘리고 REFRESH_REASONS를 안 고치면 문서 게이트가 그 코드를 **보지 못한다** —
    "코드가 늘면 문서 미갱신이 red"라는 사슬의 첫 고리가 여기다.
    """
    constants = {getattr(okf_remote, name) for name in dir(okf_remote) if name.startswith("CODE_")}
    assert constants == set(okf_remote.REFRESH_REASONS), (
        f"CODE_* 상수 - REFRESH_REASONS = {sorted(constants - set(okf_remote.REFRESH_REASONS))} / "
        f"REFRESH_REASONS - CODE_* = {sorted(set(okf_remote.REFRESH_REASONS) - constants)}"
    )


def test_pointer_reasons_are_totally_mapped():
    """`_resolve_pointer`가 낼 수 있는 **모든** 사유 문자열이 `_POINTER_CODES`에 있다.

    이 사상이 `else` 폴백이면 새 사유가 조용히 `not_url`("갱신 대상 아님 — 그냥 진행")로
    흡수된다. 실제 오설정을 정상으로 보고하는 것이라, 코드 전환의 의미가 사라진다.
    """
    reasons = set()
    for ret in _returns(_toplevel_func("_resolve_pointer")):
        value = ret.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            reasons.add(value.value)
        elif isinstance(value, ast.Name):
            reasons.add(getattr(okf_remote, value.id))
        elif isinstance(value, ast.Attribute) and getattr(value.value, "id", None) == "okf_vault":
            reasons.add(getattr(okf_vault, value.attr))
    assert reasons, "_resolve_pointer의 사유 반환을 하나도 찾지 못했다 — 게이트가 헛돈다"
    unmapped = reasons - set(okf_remote._POINTER_CODES)
    assert not unmapped, f"_POINTER_CODES에 없는 사유: {sorted(unmapped)}"
    assert set(okf_remote._POINTER_CODES.values()) <= set(okf_remote.REFRESH_REASONS)


def test_study_md_does_not_match_refresh_reason_strings():
    """`study.md`가 한국어 `reason` 리터럴을 판정 키로 쓰지 않는다.

    `test_dispatch_verdict.py`의 `test_commands_do_not_match_note_strings`와 동형 —
    코드로 옮긴 축이 문구 매칭으로 되돌아오지 않게 한다.
    """
    body = (_SOURCE.parents[2] / "commands" / "study.md").read_text(encoding="utf-8")
    reasons = set()
    for func_name in _CODE_GATED:
        for ret in _returns(_toplevel_func(func_name)):
            if not isinstance(ret.value, ast.Dict):
                continue
            for key, value in zip(ret.value.keys, ret.value.values):
                if isinstance(key, ast.Constant) and key.value == "reason":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        reasons.add(value.value)
    assert reasons, "reason 리터럴을 하나도 찾지 못했다 — 게이트가 헛돈다"
    # **값 표기**(백틱·따옴표)만 잡는다. 코드 분기 게이트가 `` `code` `` 표기를 요구하는 것과
    # 같은 판별이다 — 산문으로 상황을 설명하는 것("남은 미봉인 잔재는…")은 매칭이 아니고,
    # 그것까지 막으면 문서가 상황을 설명할 수 없어진다.
    for literal in sorted(reasons):
        for quoted in (f"`{literal}`", f'"{literal}"'):
            assert quoted not in body, (
                f"study.md가 reason 값 {quoted}을 판정 키로 쓴다 — 분기는 `code`로 한다"
            )
