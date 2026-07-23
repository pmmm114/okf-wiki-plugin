"""관리형 clone 계층 (#153) — URL 포인터의 git I/O를 담는 generic 모듈.

``okf_home``이 순수(무네트워크) 분류기로 남는 대가로, clone/fetch/ff-갱신 같은
**모든 네트워크·worktree 조작을 이 모듈이 소유**한다(#153 C6-1 — 배치/순수성 경계).
호출은 전부 **명시 지점**에서만 한다:

- ``clone``     : ``/okf-init --home`` 마법사가 사용자 동의 후 1회(옵트인, #91 #153 AC5).
- ``session_fetch`` : SessionStart 훅 **단일 지점**에서 fetch-only + TTL dedup(U1-5·U3-2).
- ``refresh``   : ``/study`` 진입(step 0)에서 clean-gate 통과 시에만 ff-only 갱신(U3-2·U3-6).
- ``doctor_home_notes`` : doctor의 **무네트워크** 신선도 표시(로컬 git 메타만, U1-8).

resolver(``home_state``/``resolve_capture``/``resolve_inject``)에는 **절대 들어가지
않는다** — 매 ``.md`` Write 훅이 resolver를 타므로 여기 네트워크가 붙으면 저장마다
블록된다(#153 U1-1·U1-2). 신선도 실패(오프라인·인증)는 fail-closed가 아니라
**캐시로 저하 + 사유 반환**이다 — 주입은 clone 캐시로 계속되고 PR만 보류된다.

core⊥study 경계(#145): 이 파일은 ``okf_*`` core라 ``study_*``를 import하지 않는다.
stdlib(+``git`` 서브프로세스) 전용 — 소비 머신 시스템 python3로 직접 실행된다.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX advisory lock — 다중 세션 clone 경합 직렬화(D4)
except ImportError:  # pragma: no cover - 비-POSIX(윈도우 등)에선 락 없이 best-effort 진행
    fcntl = None

import okf_home

_DEFAULT_FETCH_TTL = 900.0  # 초 — 마지막 성공 fetch 후 재fetch 억제(신선 캐시 dedup)
_DEFAULT_FAIL_BACKOFF = 60.0  # 초 — 마지막 실패 attempt 후 재시도 억제(오프라인 반복 스톨 방지)
_CLONE_TIMEOUT = 120.0
_FETCH_TIMEOUT = 20.0
_LOCAL_GIT_TIMEOUT = 10.0
_SYNC_META = "okf-sync.json"


# --- git 실행 (bounded·하드닝) ------------------------------------------------


def _git_env() -> dict:
    """git 하드닝 env — 크레덴셜 프롬프트 행(hang)·위험 transport 차단(#153 C5-1)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # 인증 프롬프트로 훅이 멈추지 않게(강제)
    env.setdefault("GIT_ALLOW_PROTOCOL", "https:http:ssh:git:file")  # ext:: 등 배제
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")  # ssh 프롬프트 차단
    return env


def _run_git(args: list[str], cwd: str | None = None, timeout: float = _LOCAL_GIT_TIMEOUT):
    """``git <args>``를 bounded 실행하고 (rc, stdout, stderr)를 반환한다.

    타임아웃·OSError는 rc=None(실패 동치)으로 흡수한다. start_new_session으로 손자
    프로세스까지 그룹 회수해 고아를 막는다(okf_hooks._run_okf와 동형 패턴).
    """
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
            start_new_session=True,
        )
    except OSError as exc:
        return None, "", str(exc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait()
        return None, "", f"타임아웃({timeout:g}초)"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# --- 신선도 메타 (.git/okf-sync.json — 추적 트리 밖이라 clone을 dirty로 만들지 않음) --


def _sync_meta_path(clone_path: str | Path) -> Path | None:
    gitdir = Path(clone_path) / ".git"
    return gitdir / _SYNC_META if gitdir.is_dir() else None


def _read_sync(clone_path: str | Path) -> dict:
    path = _sync_meta_path(clone_path)
    return (okf_home.read_json(path) or {}) if path is not None else {}


def _stamp(clone_path: str | Path, **fields) -> None:
    """clone 신선도 메타(.git/okf-sync.json)에 필드를 병합 기록한다(best-effort).

    ``last_fetch``(마지막 **성공** fetch)와 ``last_attempt``(마지막 **시도**, 성공·실패
    무관)를 분리 기록한다 — 실패 attempt도 스탬프해 오프라인 반복 fetch 스톨을 막는다(D3).
    """
    path = _sync_meta_path(clone_path)
    if path is None:
        return
    data = _read_sync(clone_path)
    data.update(fields)
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


@contextmanager
def _clone_lock(clone_path: str | Path):
    """관리형 clone의 worktree 조작 구간 advisory lock(비차단) — 다중 세션 경합 직렬화(D4).

    yield는 획득 여부(bool). fcntl 부재(비-POSIX)·``.git`` 부재·락 파일 생성 불가면
    무락으로 진행(True). 획득 실패(다른 세션 점유)면 False — 호출자가 '생략'으로 저하한다.
    파일 디스크립터 닫기가 flock을 해제한다.
    """
    gitdir = Path(clone_path) / ".git"
    if fcntl is None or not gitdir.is_dir():
        yield True
        return
    try:
        fd = os.open(str(gitdir / "okf-remote.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield True  # 락 파일 생성 불가 — best-effort 무락 진행
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        os.close(fd)


# --- 로컬 git 상태 (무네트워크 — 이미 fetch된 ref만) ---------------------------


def _is_dirty(clone_path: str | Path) -> bool | None:
    """worktree에 미커밋 변경(추적 수정·미추적 신규)이 있는지. 판정 불가는 None."""
    rc, out, _err = _run_git(["status", "--porcelain"], cwd=str(clone_path))
    if rc != 0:
        return None
    return bool(out.strip())


def _ahead_behind(clone_path: str | Path) -> tuple[int | None, int | None]:
    """(ahead, behind) — 로컬 HEAD vs @{upstream}, 이미 fetch된 ref 기준(무네트워크)."""
    rc, out, _err = _run_git(
        ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=str(clone_path)
    )
    if rc != 0:
        return None, None
    parts = out.split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _current_branch(clone_path: str | Path) -> str | None:
    rc, out, _err = _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=str(clone_path))
    return out.strip() if rc == 0 and out.strip() else None


def origin_canonical(path: str | Path) -> str | None:
    """repo의 origin URL을 canonical form으로 — 이원화 감지·식별용(무네트워크)."""
    rc, out, _err = _run_git(["remote", "get-url", "origin"], cwd=str(path))
    if rc != 0 or not out.strip():
        return None
    return okf_home.canonicalize_url(out.strip())


# --- URL 포인터 해소 (순수 — okf_home 위임) -----------------------------------


def _resolve_pointer(url: str | None = None):
    """(stored_url, canonical, clone_path) 또는 사유 문자열을 반환한다(무네트워크)."""
    value = url if url is not None else okf_home.read_pointer()
    if not value or not okf_home.is_url(value):
        return "URL 포인터 아님"
    stored = okf_home.clone_url(value)
    canonical = okf_home.canonicalize_url(value)
    if stored is None or canonical is None:
        return okf_home.INVALID_URL_TRANSPORT
    return stored, canonical, okf_home.managed_clone_path(canonical)


# --- clone (옵트인 — 마법사가 동의 후 호출) ------------------------------------


def clone(url: str | None = None, timeout: float = _CLONE_TIMEOUT) -> dict:
    """관리형 clone을 물질화한다(멱등). 이미 유효하면 재사용, 반쪽(torn)이면 재clone.

    원자성(#153 C3-1): 임시 디렉토리로 clone 후 ``os.replace``로 rename — 중단된 clone이
    반쪽 상태로 유효 경로를 오염시키지 않는다. clone 대상 URL은 크레덴셜 제거본이며
    원문(토큰 포함)은 어디에도 로그하지 않는다(U4-6).
    """
    resolved = _resolve_pointer(url)
    if isinstance(resolved, str):
        return {"cloned": False, "reason": resolved}
    stored, _canonical, dest = resolved
    if okf_home.valid_home(dest):
        return {
            "cloned": False,
            "reason": "이미 존재(재사용)",
            "clone_path": str(dest),
            "valid": True,
        }
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)  # 반쪽 clone 정리
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f"{dest.name}.tmp-clone-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    rc, _out, err = _run_git(["clone", "--quiet", stored, str(tmp)], timeout=timeout)
    if rc != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return {
            "cloned": False,
            "reason": "clone 실패(오프라인/인증/미허용)",
            "clone_path": str(dest),
            "detail": err.strip()[-200:],
        }
    try:
        os.replace(tmp, dest)  # 원자적 rename(같은 파일시스템)
    except OSError:
        # 경합: 다른 세션이 먼저 물질화했으면 그걸 채택하고 임시본은 폐기(이 프로세스는
        # clone 안 했으므로 cloned:False로 정확히 보고 — D5).
        shutil.rmtree(tmp, ignore_errors=True)
        if okf_home.valid_home(dest):
            return {
                "cloned": False,
                "reason": "이미 존재(경합 — 재사용)",
                "clone_path": str(dest),
                "valid": True,
            }
        return {"cloned": False, "reason": "clone rename 실패", "clone_path": str(dest)}
    valid = okf_home.valid_home(dest)
    now = time.time()
    _stamp(dest, last_fetch=now, last_attempt=now)
    warning = None if valid else "clone됨 — .okf-wiki.json 부재(원격에 큐레이션 번들 필요)"
    return {"cloned": True, "clone_path": str(dest), "valid": valid, "warning": warning}


# --- fetch-only (SessionStart) ------------------------------------------------


def _fetch(clone_path: str | Path, timeout: float = _FETCH_TIMEOUT) -> dict:
    now = time.time()
    rc, _out, err = _run_git(["fetch", "--quiet"], cwd=str(clone_path), timeout=timeout)
    if rc == 0:
        _stamp(clone_path, last_fetch=now, last_attempt=now)
        return {"fetched": True}
    _stamp(clone_path, last_attempt=now)  # 실패도 스탬프 — 오프라인 매-SessionStart 스톨 방지(D3)
    return {"fetched": False, "reason": "fetch 실패(오프라인/인증)", "detail": err.strip()[-200:]}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def session_fetch(ttl: float | None = None, timeout: float = _FETCH_TIMEOUT) -> dict:
    """SessionStart용 fetch-only. URL 모드·유효 clone·신선도 창 경과일 때만 네트워크.

    미생성 clone은 여기서 만들지 않는다(옵트인, U1-4) — 미생성/오프라인/미URL은 전부
    무동작(skipped)이다. worktree는 절대 건드리지 않는다(fetch-only, U3-2). dedup은 2단:
    마지막 **성공**이 TTL 안이면 skip(신선 캐시), 아니면 마지막 **시도**가 backoff 안이면
    skip(오프라인 반복 스톨 억제, D3) — 둘 다 아니면 시도한다.
    """
    if os.environ.get("OKF_REMOTE_OFFLINE"):
        return {"skipped": "offline env"}
    resolved = _resolve_pointer()
    if isinstance(resolved, str):
        return {"skipped": resolved}
    _stored, _canonical, clone_path = resolved
    if not okf_home.valid_home(clone_path):
        return {"skipped": "clone 미생성"}
    if ttl is None:
        ttl = _env_float("OKF_REMOTE_FETCH_TTL", _DEFAULT_FETCH_TTL)
    now = time.time()
    meta = _read_sync(clone_path)
    last_fetch = meta.get("last_fetch")
    if ttl > 0 and isinstance(last_fetch, (int, float)) and (now - last_fetch) < ttl:
        return {"skipped": "ttl"}
    backoff = _env_float("OKF_REMOTE_FETCH_BACKOFF", _DEFAULT_FAIL_BACKOFF)
    last_attempt = meta.get("last_attempt")
    if backoff > 0 and isinstance(last_attempt, (int, float)) and (now - last_attempt) < backoff:
        return {"skipped": "backoff"}
    return _fetch(clone_path, timeout=timeout)


# --- refresh (/study 진입 — clean-gate ff-only) -------------------------------


def refresh(timeout: float = _FETCH_TIMEOUT) -> dict:
    """/study 진입용 신선도 갱신 — clean-gate 통과 시에만 fetch + ff-only merge.

    승격은 clone worktree에 쓴다(#153 U3-2). dirty(미커밋 승격 잔재)면 갱신을
    **생략**하고 경고만 낸다 — stash 자동회복은 clone을 wedge시키므로 금지. diverged
    (로컬 커밋으로 ff 불가)도 생략+경고. 갱신은 최신 base 위 승격 불변식을 세운다(U3-6).
    """
    resolved = _resolve_pointer()
    if isinstance(resolved, str):
        return {"refreshed": False, "reason": resolved}
    _stored, _canonical, clone_path = resolved
    if not okf_home.valid_home(clone_path):
        return {"refreshed": False, "reason": "clone 미생성"}
    # worktree 조작 구간은 다른 세션의 refresh와 직렬화한다(D4) — 획득 실패면 저하.
    with _clone_lock(clone_path) as acquired:
        if not acquired:
            return {
                "refreshed": False,
                "reason": "locked",
                "warning": "다른 세션이 clone을 갱신 중 — 생략(캐시로 진행)",
            }
        if _is_dirty(clone_path):
            return {
                "refreshed": False,
                "reason": "dirty",
                "warning": "clone에 미커밋 승격 잔재 — 디스패치(커밋)/폐기 후 동기화하라",
            }
        if os.environ.get("OKF_REMOTE_OFFLINE"):
            return {
                "refreshed": False,
                "reason": "offline env",
                "warning": "오프라인 — 캐시로 진행",
            }
        now = time.time()
        rc, _out, _err = _run_git(["fetch", "--quiet"], cwd=str(clone_path), timeout=timeout)
        if rc != 0:
            _stamp(clone_path, last_attempt=now)
            return {
                "refreshed": False,
                "reason": "fetch 실패",
                "warning": "신선도 갱신 실패 — 캐시로 진행",
            }
        _stamp(clone_path, last_fetch=now, last_attempt=now)
        rc, _out, _err = _run_git(
            ["merge", "--ff-only", "@{upstream}"], cwd=str(clone_path), timeout=timeout
        )
        if rc == 0:
            return {"refreshed": True}
        return {
            "refreshed": False,
            "reason": "diverged",
            "warning": "로컬 커밋으로 ff 불가 — 관리형 clone 수동 정리 필요",
        }


# --- doctor (무네트워크 신선도 표시 — U1-8) -----------------------------------


def _age_str(epoch) -> str:
    if not isinstance(epoch, (int, float)):
        return "기록 없음"
    delta = max(0, int(time.time() - epoch))
    if delta < 3600:
        return f"{delta // 60}분 전"
    if delta < 86400:
        return f"{delta // 3600}시간 전"
    return f"{delta // 86400}일 전"


def doctor_home_notes(pointer: str) -> list[str]:
    """URL 포인터의 무네트워크 진단 — 모드·clone 상태·마지막 fetch·behind·dirty(U1-8·U4-7).

    능동 fetch는 하지 않는다(로컬 git 메타만). 미생성·미허용 transport도 여기서 표기한다.
    """
    lines = ["  모드: URL(관리형 clone)"]
    stored = okf_home.clone_url(pointer)
    canonical = okf_home.canonicalize_url(pointer)
    if stored is None or canonical is None:
        lines.append(f"  URL: {pointer} — ⚠ 미지원 transport(https/ssh/git/file만)")
        return lines
    clone_path = okf_home.managed_clone_path(canonical)
    lines.append(f"  URL: {stored}")
    lines.append(f"  clone: {clone_path}")
    if not okf_home.valid_home(clone_path):
        if clone_path.exists():
            lines.append("  상태: ⚠ 반쪽 clone — okf_remote clone으로 재생성")
        else:
            lines.append("  상태: ⚠ 미생성 — /okf-init --home으로 옵트인 생성")
        return lines
    lines.append(f"  마지막 fetch: {_age_str(_read_sync(clone_path).get('last_fetch'))}")
    branch = _current_branch(clone_path)
    if branch is None:
        lines.append("  브랜치: ⚠ detached HEAD — 관리형 clone 정리 필요")
    ahead, behind = _ahead_behind(clone_path)
    if behind:
        lines.append(f"  신선도: ⚠ origin보다 {behind}커밋 뒤 — /study 진입 시 갱신(refresh)")
    if ahead:
        lines.append(f"  ⚠ 로컬 {ahead}커밋 앞(ff 불가 위험) — 미푸시 승격일 수 있음")
    dirty = _is_dirty(clone_path)
    if dirty:
        lines.append("  ⚠ dirty: 미커밋 승격 잔재 — 핸들러 디스패치(커밋) 또는 폐기 필요")
    return lines


def dualization_note(pointer: str, home: str) -> str | None:
    """로컬 경로 홈이 같은 origin의 관리형 clone과 이원화됐는지 1줄 경고(U4-7·무네트워크).

    포인터가 로컬 경로인데 그 repo의 origin과 같은 canonical의 관리형 clone이 이미
    있으면 지식이 두 clone으로 갈린다 — doctor가 감지·안내한다.
    """
    if okf_home.is_url(pointer) or okf_home.is_managed_clone(home):
        return None
    canonical = origin_canonical(home)
    if canonical is None:
        return None
    twin = okf_home.managed_clone_path(canonical)
    if okf_home.valid_home(twin):
        return f"  ⚠ 이원화: 같은 origin의 관리형 clone 존재({twin}) — URL 모드와 로컬 경로 혼용"
    return None


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="okf_remote", description="관리형 clone 계층(#153)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cl = sub.add_parser("clone", help="포인터 URL을 관리형 clone으로 물질화(옵트인)")
    cl.add_argument("url", nargs="?", default=None, help="미지정 시 포인터에서 읽음")
    sub.add_parser("sync", help="SessionStart용 fetch-only(TTL dedup)")
    sub.add_parser("refresh", help="/study 진입용 clean-gate ff-only 갱신")
    sub.add_parser("status", help="URL 포인터 무네트워크 진단(JSON)")
    args = ap.parse_args(argv)

    if args.cmd == "clone":
        result = clone(args.url)
    elif args.cmd == "sync":
        result = session_fetch()
    elif args.cmd == "refresh":
        result = refresh()
    else:  # status
        pointer = okf_home.read_pointer()
        result = {
            "pointer": pointer,
            "is_url": okf_home.is_url(pointer),
            "notes": doctor_home_notes(pointer) if okf_home.is_url(pointer) else [],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
