"""관리형 clone 계층 (#153) — URL 포인터의 git I/O를 담는 generic 모듈.

``okf_vault``이 순수(무네트워크) 분류기로 남는 대가로, clone/fetch/ff-갱신 같은
**모든 네트워크·worktree 조작을 이 모듈이 소유**한다(#153 C6-1 — 배치/순수성 경계).
호출은 전부 **명시 지점**에서만 한다:

- ``clone``     : ``/okf-init --vault`` 마법사가 사용자 동의 후 1회(옵트인, #91 #153 AC5).
- ``session_fetch`` : SessionStart 훅 **단일 지점**에서 fetch-only + TTL dedup(U1-5·U3-2).
- ``refresh``   : ``/study`` 진입(step 0)에서 ff-only 갱신 + 봉인 잔재 회수(U3-2·U3-6, #216 V1).
- ``doctor_vault_notes`` : doctor의 **무네트워크** 신선도 표시(로컬 git 메타만, U1-8).

resolver(``vault_state``/``resolve_capture``/``resolve_inject``)에는 **절대 들어가지
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

import okf_vault

_DEFAULT_FETCH_TTL = 900.0  # 초 — 마지막 성공 fetch 후 재fetch 억제(신선 캐시 dedup)
_DEFAULT_FAIL_BACKOFF = 60.0  # 초 — 마지막 실패 attempt 후 재시도 억제(오프라인 반복 스톨 방지)
_DEFAULT_SEAL_STALE = 86400.0  # 초 — 이보다 오래된 fetch 기준 봉인 판정은 확정으로 제시하지 않는다
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
    return (okf_vault.read_json(path) or {}) if path is not None else {}


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
def clone_lock(clone_path: str | Path):
    """관리형 clone의 worktree 조작 구간 advisory lock(비차단) — 다중 세션 경합 직렬화(D4).

    yield는 획득 여부(bool). fcntl 부재(비-POSIX)·``.git`` 부재·락 파일 생성 불가면
    무락으로 진행(True). 획득 실패(다른 세션 점유)면 False — 호출자가 '생략'으로 저하한다.
    파일 디스크립터 닫기가 flock을 해제한다.

    공개 API다(#216 V1) — 잔재 회수는 refresh와 **다른 프로세스**(디스패치)에서도
    일어나므로, 같은 락을 잡지 못하면 한쪽의 폐기가 다른 쪽의 ff와 경합한다.
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


# --- 잔재 열거·봉인 판정·폐기 (#216 V1 — 정체 자가 회복의 원시) ----------------


def _parse_status_z(out: str) -> list[tuple[str, str]]:
    """``status --porcelain -z`` 출력을 (XY, 경로) 목록으로 파싱한다.

    rename·copy(R·C)는 **원본 경로가 다음 토큰**에 오므로 하나 더 건너뛴다 — 안 그러면
    원본 경로가 독립 엔트리로 잘못 잡힌다.
    """
    entries: list[tuple[str, str]] = []
    tokens = out.split("\0")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if len(token) < 4:  # "XY <경로>" 최소 형태 미만(꼬리 빈 토큰 포함)은 버린다
            i += 1
            continue
        entries.append((token[:2], token[3:]))
        i += 2 if token[0] in ("R", "C") else 1
    return entries


def list_residue(clone_path: str | Path) -> list[tuple[str, str]] | None:
    """워킹트리 잔재를 (XY, 경로) 목록으로 연다. 판정 불가는 None.

    두 플래그가 정확성의 전제다.

    - ``--untracked-files=all`` — 기본값은 미추적 **디렉터리**를 ``?? dir/``로 접어
      내보낸다. 그 경로를 지우려 하면 OSError라 폐기가 **조용히 무동작**한다.
    - ``-z`` — 기본 출력은 비ASCII·공백 경로를 따옴표로 감싸고 이스케이프한다. 인용된
      문자열을 그대로 경로로 쓰면 폐기가 엉뚱한 곳을 향한다.
    """
    rc, out, _err = _run_git(
        ["status", "--porcelain", "-z", "--untracked-files=all"], cwd=str(clone_path)
    )
    if rc != 0:
        return None
    return _parse_status_z(out)


def _remote_refs(clone_path: str | Path) -> list[str]:
    """원격추적 ref 전체(무네트워크 — 이미 fetch된 것만)."""
    rc, out, _err = _run_git(
        ["for-each-ref", "--format=%(refname)", "refs/remotes"], cwd=str(clone_path)
    )
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _worktree_blob(clone_path: str | Path, rel: str) -> str | None:
    """워킹트리 파일의 blob 해시. 일반 파일이 아니면(디렉터리·깨진 심링크) None."""
    target = Path(clone_path) / rel
    if not target.is_file():
        return None
    rc, out, _err = _run_git(["hash-object", "--", str(target)], cwd=str(clone_path))
    return out.strip() if rc == 0 and out.strip() else None


def _blob_at(clone_path: str | Path, ref: str, rel: str) -> str | None:
    """``<ref>:<경로>``의 blob 해시. 그 ref에 그 경로가 없으면 None."""
    rc, out, _err = _run_git(
        ["rev-parse", "--quiet", "--verify", f"{ref}:{rel}"], cwd=str(clone_path)
    )
    return out.strip() if rc == 0 and out.strip() else None


def sealed_paths(clone_path: str | Path, rels) -> set[str]:
    """내용이 **원격추적 ref에 담긴**(=봉인된) 경로 집합 — "지워도 회수 가능"의 증명.

    판정 단위는 전체 tree가 아니라 **경로별 blob**이다. 전체 tree 동치로 잡으면 무관한
    upstream 커밋 하나만 더 있어도 깨져서, 다중 머신 vault에서는 사실상 발화하지 않는다.

    무네트워크다 — 이미 fetch된 ref만 본다. 따라서 판정은 "마지막 fetch 시점 기준"이고,
    호출자는 그 신선도를 함께 제시해야 한다.
    """
    refs = _remote_refs(clone_path)
    if not refs:
        return set()
    sealed: set[str] = set()
    for rel in rels:
        local = _worktree_blob(clone_path, rel)
        if local is None:
            continue
        if any(_blob_at(clone_path, ref, rel) == local for ref in refs):
            sealed.add(rel)
    return sealed


def discard_paths(clone_path: str | Path, entries) -> bool:
    """봉인된 잔재를 경로별로 폐기하고 **사후 검증**한다. 전부 사라졌으면 True.

    index를 먼저 되돌리는 것이 핵심이다 — ``checkout --``는 인덱스에서 워킹트리로
    복원하므로 staged 엔트리(``A ``·``M ``)에는 무동작이고, 그러면 clone이 dirty로 남아
    정체가 그대로 재발한다.

    호출자는 **봉인이 증명된 경로만** 넘겨야 한다. 이 함수는 판정하지 않는다.
    """
    root = Path(clone_path)
    for xy, rel in entries:
        if xy[0] not in (" ", "?"):  # index에 올라간 변경 — 먼저 HEAD로 되돌린다
            _run_git(["reset", "--quiet", "HEAD", "--", rel], cwd=str(clone_path))
        if _blob_at(clone_path, "HEAD", rel) is not None:
            _run_git(["checkout", "--", rel], cwd=str(clone_path))
        else:
            try:
                (root / rel).unlink()
            except OSError:  # 이미 없거나 지울 수 없음 — 사후 검증이 잡는다
                pass
    remaining = list_residue(clone_path)
    if remaining is None:
        return False
    left = {rel for _xy, rel in remaining}
    return not any(rel in left for _xy, rel in entries)


def reclaim_sealed(clone_path: str | Path) -> list[str]:
    """봉인된 잔재를 회수하고 폐기된 경로를 정렬해 반환한다(없으면 빈 목록).

    열거 → 후보 선별 → 봉인 판정 → 폐기를 한 벌로 묶는다. ff가 거부됐을 때(refresh)와
    핸들러 실행 뒤(디스패치)가 **같은 판정**을 쓰게 하는 단일 지점이다 — 두 곳이 각자
    판정하면 한쪽만 고쳐지는 드리프트가 생긴다.

    삭제·rename·copy는 후보에서 뺀다. 워킹트리에 대조할 내용이 없거나 경로 쌍이라
    봉인 판정이 성립하지 않는다(보수적 제외 — 판정 불가는 보존).
    """
    entries = list_residue(clone_path)
    if not entries:
        return []
    candidates = [(xy, rel) for xy, rel in entries if xy[0] not in ("R", "C") and "D" not in xy]
    sealed = sealed_paths(clone_path, [rel for _xy, rel in candidates])
    if not sealed:
        return []
    discard_paths(clone_path, [(xy, rel) for xy, rel in candidates if rel in sealed])
    return sorted(sealed)


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
    return okf_vault.canonicalize_url(out.strip())


# --- URL 포인터 해소 (순수 — okf_vault 위임) -----------------------------------


def _resolve_pointer(url: str | None = None):
    """(stored_url, canonical, clone_path) 또는 사유 문자열을 반환한다(무네트워크)."""
    value = url if url is not None else okf_vault.read_pointer()
    if not value or not okf_vault.is_url(value):
        return "URL 포인터 아님"
    stored = okf_vault.clone_url(value)
    canonical = okf_vault.canonicalize_url(value)
    if stored is None or canonical is None:
        return okf_vault.INVALID_URL_TRANSPORT
    return stored, canonical, okf_vault.managed_clone_path(canonical)


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
    if okf_vault.valid_vault(dest):
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
        if okf_vault.valid_vault(dest):
            return {
                "cloned": False,
                "reason": "이미 존재(경합 — 재사용)",
                "clone_path": str(dest),
                "valid": True,
            }
        return {"cloned": False, "reason": "clone rename 실패", "clone_path": str(dest)}
    valid = okf_vault.valid_vault(dest)
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
    if not okf_vault.valid_vault(clone_path):
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


def _ff(clone_path: str | Path, timeout: float) -> int | None:
    rc, _out, _err = _run_git(
        ["merge", "--ff-only", "@{upstream}"], cwd=str(clone_path), timeout=timeout
    )
    return rc


def _recover_and_ff(clone_path: str | Path, timeout: float) -> dict:
    """ff가 **경로 충돌**로 거부됐을 때 — 봉인된 잔재만 폐기하고 재시도한다(#216 V1).

    git은 덮어쓸 로컬 변경이 있을 때만 거부하므로, 거부는 곧 "잔재가 upstream이 가져올
    경로와 겹친다"는 뜻이다. 그 잔재가 원격에 이미 담겨 있으면(봉인) 폐기해도 회수
    가능하므로 정체를 푼다. 봉인되지 않았으면 **아무것도 지우지 않는다** — 오탐 폐기는
    비가역 지식 유실이지만, 미폐기는 알려진 정체를 재현할 뿐이고 진단 경로가 남는다.
    """
    unsealed_warning = "원격에 없는 잔재가 ff를 막고 있다 — 디스패치(PR)로 반영하거나 직접 정리하라"
    discarded = reclaim_sealed(clone_path)
    if not discarded:
        return {"refreshed": False, "reason": "미봉인 잔재", "warning": unsealed_warning}
    if _ff(clone_path, timeout) == 0:
        return {"refreshed": True, "discarded": discarded}
    return {
        "refreshed": False,
        "reason": "미봉인 잔재",
        "warning": unsealed_warning,
        "discarded": discarded,
    }


def refresh(timeout: float = _FETCH_TIMEOUT) -> dict:
    """/study 진입용 신선도 갱신 — fetch + ff-only, 거부되면 봉인된 잔재만 폐기 후 재시도.

    **dirty 선판정은 두지 않는다(#216 V1).** git은 덮어쓸 경로가 실제로 충돌할 때만 ff를
    거부하고 워킹트리·HEAD를 원자적으로 보존한다. 그보다 엄격한 bool 게이트는 안전한 ff
    까지 거부해 clone을 영구 정체시켰다 — 그 정체가 #216이다. 미푸시 잔재는 충돌하지
    않는 한 그대로 살아남아 주입이 계속된다(§7).

    폐기는 **봉인**(내용이 원격추적 ref에 담김)이 증명된 경로에 한정한다. ``#153``이 금지한
    것은 stash 자동회복이지 ff 위임이 아니다. diverged(로컬 커밋)면 회수에 진입하지 않는다.
    """
    resolved = _resolve_pointer()
    if isinstance(resolved, str):
        return {"refreshed": False, "reason": resolved}
    _stored, _canonical, clone_path = resolved
    if not okf_vault.valid_vault(clone_path):
        return {"refreshed": False, "reason": "clone 미생성"}
    # worktree 조작 구간은 다른 세션의 refresh와 직렬화한다(D4) — 획득 실패면 저하.
    with clone_lock(clone_path) as acquired:
        if not acquired:
            return {
                "refreshed": False,
                "reason": "locked",
                "warning": "다른 세션이 clone을 갱신 중 — 생략(캐시로 진행)",
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
        if _ff(clone_path, timeout) == 0:
            return {"refreshed": True}
        ahead, _behind = _ahead_behind(clone_path)
        if ahead:  # 로컬 커밋이 있으면 잔재 문제가 아니다 — 회수에 진입하지 않는다
            return {
                "refreshed": False,
                "reason": "diverged",
                "warning": "로컬 커밋으로 ff 불가 — 관리형 clone 수동 정리 필요",
            }
        return _recover_and_ff(clone_path, timeout)


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


def _has_handlers(clone_path: str | Path) -> bool:
    """소비처가 원격 반영 핸들러를 배선했는지(``.okf-wiki.json``의 ``study.handlers``).

    설정 **파일만** 읽는다 — study 모듈은 import하지 않으므로 core⊥study 경계와 무관하다.
    핸들러 자체는 소비처 소유라 여기서 알아야 할 것은 "반영 경로가 존재하는가"뿐이다.
    """
    cfg = okf_vault.read_json(Path(clone_path) / ".okf-wiki.json") or {}
    study = cfg.get("study")
    return bool(isinstance(study, dict) and study.get("handlers"))


def _residue_notes(clone_path: str | Path) -> list[str]:
    """잔재를 **봉인 여부로 갈라** 안내한다(#216 V3). 잔재가 없으면 빈 목록(침묵).

    "디스패치"와 "폐기"는 정반대 결과를 낳으므로 한 줄로 뭉뚱그리면 안 된다 — 사용자가
    자기 지식이 원격에 있는지 모르는 채 파괴적 명령을 고르게 된다.

    다만 판정 근거가 **이미 fetch된 ref뿐**이다(doctor는 능동 fetch 금지). fetch가
    오래된 clone에서는 실제로 미푸시인 작업을 '반영됨'으로 오판할 수 있고, 그 안내를
    따른 폐기는 비가역이다. 그래서 오래되면 판정을 **보류**하고 정리를 권하지 않는다.
    """
    entries = list_residue(clone_path)
    if not entries:
        return []
    candidates = [(xy, rel) for xy, rel in entries if xy[0] not in ("R", "C") and "D" not in xy]
    rels = [rel for _xy, rel in candidates]
    sealed = sealed_paths(clone_path, rels)
    unsealed = [rel for rel in rels if rel not in sealed]
    undecidable = len(entries) - len(candidates)  # 삭제·rename·copy — 대조할 내용이 없다
    last_fetch = _read_sync(clone_path).get("last_fetch")
    stale = not isinstance(last_fetch, (int, float)) or (time.time() - last_fetch) > _env_float(
        "OKF_REMOTE_SEAL_STALE", _DEFAULT_SEAL_STALE
    )
    notes: list[str] = []
    if sealed:
        if stale:
            notes.append(
                f"  ⚠ 잔재 {len(sealed)}건: 원격 반영된 것으로 보이나 fetch가 오래됨 — 판정 보류"
            )
        else:
            notes.append(f"  잔재 {len(sealed)}건: 원격에 반영됨 — /study 진입 시 자동 정리")
    if unsealed:
        # 원격 반영 경로가 없는 vault에 "디스패치하라"는 실행 불가능한 지시다(#216 V4).
        route = (
            "디스패치(PR)로 반영하라"
            if _has_handlers(clone_path)
            else "원격 반영 경로 없음(핸들러 미배선) — /okf-init --vault로 배선하라"
        )
        notes.append(f"  ⚠ 잔재 {len(unsealed)}건: 원격 미반영 — {route}(폐기하면 유실)")
    if undecidable:
        notes.append(f"  ⚠ 잔재 {undecidable}건: 삭제·이름변경 — 판정 불가, 직접 확인 필요")
    return notes


def doctor_vault_notes(pointer: str) -> list[str]:
    """URL 포인터의 무네트워크 진단 — 모드·clone 상태·마지막 fetch·behind·dirty(U1-8·U4-7).

    능동 fetch는 하지 않는다(로컬 git 메타만). 미생성·미허용 transport도 여기서 표기한다.
    """
    lines = ["  모드: URL(관리형 clone)"]
    stored = okf_vault.clone_url(pointer)
    canonical = okf_vault.canonicalize_url(pointer)
    if stored is None or canonical is None:
        lines.append(f"  URL: {pointer} — ⚠ 미지원 transport(https/ssh/git/file만)")
        return lines
    clone_path = okf_vault.managed_clone_path(canonical)
    lines.append(f"  URL: {stored}")
    lines.append(f"  clone: {clone_path}")
    if not okf_vault.valid_vault(clone_path):
        if clone_path.exists():
            lines.append("  상태: ⚠ 반쪽 clone — okf_remote clone으로 재생성")
        else:
            lines.append("  상태: ⚠ 미생성 — /okf-init --vault로 옵트인 생성")
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
    lines.extend(_residue_notes(clone_path))
    return lines


def dualization_note(pointer: str, vault: str) -> str | None:
    """로컬 경로 vault가 같은 origin의 관리형 clone과 이원화됐는지 1줄 경고(U4-7·무네트워크).

    포인터가 로컬 경로인데 그 repo의 origin과 같은 canonical의 관리형 clone이 이미
    있으면 지식이 두 clone으로 갈린다 — doctor가 감지·안내한다.
    """
    if okf_vault.is_url(pointer) or okf_vault.is_managed_clone(vault):
        return None
    canonical = origin_canonical(vault)
    if canonical is None:
        return None
    twin = okf_vault.managed_clone_path(canonical)
    if okf_vault.valid_vault(twin):
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
        pointer = okf_vault.read_pointer()
        result = {
            "pointer": pointer,
            "is_url": okf_vault.is_url(pointer),
            "notes": doctor_vault_notes(pointer) if okf_vault.is_url(pointer) else [],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
