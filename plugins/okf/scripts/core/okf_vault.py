"""Vault(지식 저장고) 폴백 공유 모듈 (#91 V2) — 포인터·vault 판정·설정 로드·주입 해소.

주입 훅(okf_hooks)·doctor·상위 feature 층이 재사용하는 generic 단일 해석기다
(훅별 배선은 각자, 해석은 여기 한 곳 — #91 §2 "블랭킷 변경 금지"와 "공유 모듈"
계약의 접점). 캡처(쓰기) 정책·런타임 루트·메모리 경로 판정 같은 feature 정책은
이 모듈이 아니라 feature 층 소관이다(#145 U3 분할) — 이 파일은 feature 층을
모르고, feature 층이 이 파일을 단방향으로 import한다. stdlib 전용, 실패는 전부
"없음/무동작" 동치로 관용한다.

용어(#91 v3.5 · #153 URL 모드 · #152 vault 재명명):
- vault(지식 저장고): 주입이 지식을 **끌어오는 원천**이자 `/study` 승격이 **적재되는
  목적지**. 구 용어 "home"을 대체한다(#152) — 표면은 vault로 정렬하고, 구 표면(env·
  파일·scope 값·``--home``)은 deprecated 폴백으로만 수용한다(무음 증발 방지).
- 포인터: ``~/.claude/okf/vault-project``(env ``OKF_VAULT_PROJECT`` 우선) 한 줄.
  값은 **로컬 clone 절대경로** 또는 **repo URL**(#153) 둘 중 하나다. 구 파일
  ``home-project``·구 env ``OKF_HOME_PROJECT``은 폴백으로만 읽는다(#152).
- 유효 vault: 실재 + git repo + ``.okf-wiki.json`` 존재. URL 모드에선 이 판정을
  관리형 clone(``~/.claude/okf/remotes/<slug>``)의 실재 로컬 경로에 대해 한다 —
  ``vault_state``는 URL→slug→로컬 경로 매핑만 하는 **순수(무네트워크) 분류기**로
  남고, clone/fetch는 별 모듈(``okf_remote``)의 명시 지점에서만 한다(#153 U1·C6).
- 침묵 정책: 포인터 부재=무음, 존재·무효=SessionStart 계열만 1줄 경고(경고 문구는
  이 모듈이 만들고, 방출 여부는 호출자가 결정한다).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

VAULT_ENV = "OKF_VAULT_PROJECT"  # 신 env (정본, #152)
_POINTER_REL = ".claude/okf/vault-project"  # 신 포인터 파일 (정본, #152)
# 구 표면 — deprecated 폴백으로만 읽는다(#152 R1-2·R1-3). 구 env/파일만 있는 기존
# 사용자는 동작 무변경 + doctor 마이그레이션 1줄 안내. 유저 머신 상태(커밋물 아님)라
# 최소 1개 릴리스 이상 유지 후 CHANGELOG 예고로 제거한다.
_LEGACY_ENV = "OKF_HOME_PROJECT"
_LEGACY_POINTER_REL = ".claude/okf/home-project"

# 무효 사유 코드 (doctor·경고 문구 공용)
INVALID_MISSING = "대상 없음"
INVALID_NOT_GIT = "git repo 아님"
INVALID_NO_CONFIG = ".okf-wiki.json 없음"
# URL 모드(#153) 전용 사유 — clone 미생성/미허용 transport를 로컬 오탈자와 구분한다.
INVALID_CLONE_MISSING = "URL 포인터 — 관리형 clone 미생성"
INVALID_URL_TRANSPORT = "URL 포인터 — 미지원 transport"


def read_json(path: Path):
    """JSON 파일을 관용적으로 읽는다 — 부재·깨짐·비객체는 전부 None (공개 헬퍼)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def expand(value: str) -> str:
    """`~` 확장 + 공백 정리 (공개 헬퍼 — feature 층 경로 인자 해석 공용)."""
    return os.path.expanduser(value.strip())


def read_pointer() -> str | None:
    """포인터 원문(확장 후 경로)을 반환한다. 부재·빈 값은 None (옵트인 안 함).

    우선순위(#152 R1-3): 신 env → 구 env → 신 파일 → 구 파일. 구 표면
    (``OKF_HOME_PROJECT``·``home-project``)은 deprecated 폴백으로만 읽어, 기존
    사용자의 vault를 무음 증발 없이 승계한다.
    """
    raw = os.environ.get(VAULT_ENV)
    if raw is None:
        raw = os.environ.get(_LEGACY_ENV)
    if raw is None:
        user_home = Path(os.path.expanduser("~"))  # OS 홈 디렉터리(vault 아님)
        for rel in (_POINTER_REL, _LEGACY_POINTER_REL):
            try:
                raw = (user_home / rel).read_text(encoding="utf-8")
                break
            except OSError:
                continue
    if raw is None:
        return None
    value = expand(raw)
    return value or None


def legacy_surface_notice() -> str | None:
    """구 표면(env·파일)으로 vault가 해소되면 마이그레이션 안내 1줄(#152). 없으면 None.

    doctor의 [Vault] 절이 표시한다 — 구 env/파일만 쓰는 기존 사용자에게 새 표면으로의
    이동을 1회성으로 안내하되, 동작은 무변경(폴백은 계속 읽힌다).
    """
    if os.environ.get(VAULT_ENV) is None and os.environ.get(_LEGACY_ENV) is not None:
        return f"구 env {_LEGACY_ENV} 사용 중 — {VAULT_ENV}로 옮기세요(#152 deprecated)"
    if os.environ.get(VAULT_ENV) is None and os.environ.get(_LEGACY_ENV) is None:
        user_home = Path(os.path.expanduser("~"))
        if not (user_home / _POINTER_REL).is_file() and (user_home / _LEGACY_POINTER_REL).is_file():
            return (
                f"구 포인터 파일 ~/{_LEGACY_POINTER_REL} 사용 중 — "
                f"~/{_POINTER_REL}로 옮기세요(mv, #152 deprecated)"
            )
    return None


def _is_git_repo(path: Path) -> bool:
    # .git은 디렉토리(일반) 또는 파일(worktree/서브모듈) — 존재만 본다.
    return (path / ".git").exists()


def valid_vault(path: str | Path) -> bool:
    """경로가 유효 vault(실재 디렉토리 + git + ``.okf-wiki.json``)인지 — 순수 stat 판정.

    ``vault_state``는 사유를 구분하려 단계별로 검사하지만, clone 성립 여부 같은
    bool 판정만 필요한 곳(``set_pointer``·``okf_remote``)은 이 헬퍼를 재사용한다.
    """
    p = Path(path)
    return p.is_dir() and _is_git_repo(p) and (p / ".okf-wiki.json").is_file()


# --- URL 모드 순수 헬퍼 (#153) — 무네트워크. clone/fetch는 okf_remote 소관 ----
#
# 포인터 값이 repo URL이면 관리형 clone(~/.claude/okf/remotes/<slug>)의 로컬 경로로
# 해소한다. 여기의 함수는 전부 순수(문자열·해시·경로)라 vault_state 핫패스(매 .md
# Write 훅)에서 호출해도 네트워크에 블록되지 않는다(#153 U1-1·C6-1).

_MANAGED_REL = ".claude/okf/remotes"
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://")
# scp-like: user@host:path (콜론 앞에 슬래시 없음) — git의 ssh 단축표기.
_SCP_RE = re.compile(r"^[^/@:]+@[^/@:]+:")
# transport-helper 거부: 선두 `scheme::`(ext::·transport::address 등 — 명령 실행/로컬
# 도달 위험, #153 C5-1). `//` 없는 `::`만 잡아 IPv6 리터럴(`https://[::1]/...`)은 통과.
_HELPER_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*::")
# 허용 transport — 명령 실행(ext::)·미지의 스킴은 배제(#153 C5-1). file은 테스트·
# 로컬 미러용으로 허용(사용자가 소유한 포인터라 로컬 경로 모드와 동급 위험).
_ALLOWED_SCHEMES = frozenset({"https", "http", "ssh", "git", "file"})
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def is_url(value: str | None) -> bool:
    """포인터 값이 repo URL(스킴형 또는 scp-like)인지 — 로컬 경로 모드와 분기(#153)."""
    if not value:
        return False
    return bool(_SCHEME_RE.match(value) or _SCP_RE.match(value))


def _split_scheme(value: str) -> tuple[str, str] | None:
    """(scheme, rest) 분해 — scp-like는 ssh로 승격. 미허용·비URL은 None."""
    value = value.strip().strip('"').strip("'")
    if not value or _HELPER_RE.match(value):  # ext:: 등 transport-helper(명령 실행) 거부
        return None
    m = _SCHEME_RE.match(value)
    if m:
        scheme = m.group(1).lower()
        rest = value[m.end() :]
    elif _SCP_RE.match(value):
        scheme = "ssh"
        host_part, path_part = value.split(":", 1)
        rest = f"{host_part}/{path_part}"
    else:
        return None
    if scheme not in _ALLOWED_SCHEMES:
        return None
    return scheme, rest


def canonicalize_url(value: str) -> str | None:
    """URL을 **정체성(slug·이원화 감지)용** 정규형으로 — 미허용 transport는 None.

    host 소문자 · 유저정보/크레덴셜 제거 · scp-like→ssh 승격 · 트레일링 ``.git``/
    슬래시 제거 · 포트 보존(#153 U4-6). 이 값은 식별 전용이며 clone에 쓰지 않는다
    (clone은 ``clone_url``의 저장 URL을 쓴다 — ssh user·대소문자 보존이 필요).
    """
    parts = _split_scheme(value)
    if parts is None:
        return None
    scheme, rest = parts
    if "/" in rest:
        netloc, path = rest.split("/", 1)
        path = "/" + path
    else:
        netloc, path = rest, ""
    netloc = netloc.rsplit("@", 1)[-1].lower()  # 유저정보 제거 + host 소문자
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def clone_url(value: str) -> str | None:
    """포인터 저장·clone용 URL — http(s) 크레덴셜만 제거, transport·ssh user 보존.

    canonical과 달리 host 대소문자·``.git``·ssh ``git@``를 보존해 실제 clone이
    성립한다. http(s)의 ``user:token@``는 평문 포인터 적재를 막으려 제거하고
    (자격증명은 git credential helper·ssh-agent에 위임), 미허용 transport는 None.
    """
    parts = _split_scheme(value)
    if parts is None:
        return None
    scheme, rest = parts  # rest는 스킴 뒤 netloc[/path](scp-like는 host/path로 정규화됨)
    if scheme in ("http", "https"):
        # http(s)만 userinfo(토큰) 제거 — 대소문자·포트·.git·경로는 보존(clone 성립).
        if "/" in rest:
            netloc, path = rest.split("/", 1)
            path = "/" + path
        else:
            netloc, path = rest, ""
        netloc = netloc.rsplit("@", 1)[-1]
        return f"{scheme}://{netloc}{path.rstrip('/')}"
    # ssh/git/file/scp-like — 원문 보존(ssh user 필요), 트레일링 슬래시·따옴표만 정리.
    return value.strip().strip('"').strip("'").rstrip("/")


def managed_root() -> Path:
    """관리형 clone 루트(~/.claude/okf/remotes) — 포인터·유저 스코프와 co-located."""
    return Path(os.path.expanduser("~")) / _MANAGED_REL


def remote_slug(canonical: str) -> str:
    """canonical URL → 파일시스템 안전 slug + 해시 접미(충돌·대소문자 무시 차단, U4-6)."""
    body = _SCHEME_RE.sub("", canonical)
    safe = _SANITIZE_RE.sub("-", body).strip("-").lower() or "remote"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{safe[:60]}-{digest}"


def managed_clone_path(canonical: str) -> Path:
    """canonical URL의 관리형 clone 로컬 경로(~/.claude/okf/remotes/<slug>)."""
    return managed_root() / remote_slug(canonical)


def is_managed_clone(path: str | Path) -> bool:
    """경로가 관리형 clone 루트 하위인지 — enable-capture 가드·이원화 감지용."""
    try:
        Path(path).resolve().relative_to(managed_root().resolve())
        return True
    except (ValueError, OSError):
        return False


def vault_state() -> tuple[str | None, str | None]:
    """(유효 vault 경로, 무효 사유)를 반환한다.

    - (None, None): 포인터 부재 — 옵트인 안 함(완전 무음)
    - (path, None): 유효 vault
    - (None, 사유): 포인터 존재·무효 — SessionStart 계열에서만 경고
    """
    value = read_pointer()
    if value is None:
        return None, None
    # URL 모드(#153): isabs 게이트 **이전**에 분기 — URL은 관리형 clone 로컬 경로로
    # 해소한다. clone/fetch는 여기서 하지 않는다(순수 분류기, U1-1) — 미생성이면
    # 로컬 오탈자와 구분되는 사유로 무효 처리하고, 생성은 okf_remote 명시 지점에서.
    if is_url(value):
        canonical = canonicalize_url(value)
        if canonical is None:
            return None, INVALID_URL_TRANSPORT
        clone_path = managed_clone_path(canonical)
        if not clone_path.is_dir() or not _is_git_repo(clone_path):
            return None, INVALID_CLONE_MISSING  # 반쪽(torn) clone도 미생성 동치
        if not (clone_path / ".okf-wiki.json").is_file():
            return None, INVALID_NO_CONFIG
        return str(clone_path), None
    if not os.path.isabs(value):
        return None, INVALID_MISSING
    path = Path(value)
    if not path.is_dir():
        return None, INVALID_MISSING
    if not _is_git_repo(path):
        return None, INVALID_NOT_GIT
    if not (path / ".okf-wiki.json").is_file():
        return None, INVALID_NO_CONFIG
    return str(path), None


def load_config(project: str | Path) -> dict | None:
    """프로젝트의 `.okf-wiki.json`을 관용적으로 읽는다."""
    return read_json(Path(project) / ".okf-wiki.json")


def resolve_inject(project: str | Path) -> dict:
    """주입(읽기) 스코프를 해소한다 (#91 §2 주입 3단 규칙).

    반환 dict: target(str|None) · scope("project"|"vault"|"none") · warning(str|None)
    """
    if (Path(project) / ".okf-wiki.json").is_file():
        return {"target": str(project), "scope": "project", "warning": None}
    vault, reason = vault_state()
    if vault is None:
        return {"target": None, "scope": "none", "warning": pointer_warning(reason)}
    config = load_config(vault) or {}
    if config.get("inject") is False:
        return {"target": None, "scope": "none", "warning": None}
    return {"target": vault, "scope": "vault", "warning": None}


def pointer_warning(reason: str | None) -> str | None:
    """무효 포인터 경고 문구 (공개 헬퍼 — 주입·캡처 해석기가 공용)."""
    if reason is None:
        return None
    return f"okf: Vault 포인터 무효({reason}) — /okf-doctor로 진단"


def _pointer_file() -> Path:
    return Path(os.path.expanduser("~")) / _POINTER_REL


def _write_pointer(value: str) -> Path:
    pointer = _pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(value + "\n", encoding="utf-8")
    return pointer


def set_pointer(path: str) -> dict:
    """포인터를 검증 후 기록한다. 무효 대상은 기록하지 않고 사유를 돌려준다.

    URL 모드(#153): 포인터에 **URL 원문(크레덴셜 제거본)**을 기록하고, 관리형 clone은
    **만들지 않는다**(옵트인 — okf_remote clone, 마법사가 동의 받아 호출). ``mode``·
    ``clone_path``·``clone_exists``를 실어 마법사·doctor가 다음 단계를 결정한다.
    """
    expanded = expand(path)
    if is_url(expanded):
        stored = clone_url(expanded)
        canonical = canonicalize_url(expanded)
        if stored is None or canonical is None:
            return {"written": False, "reason": INVALID_URL_TRANSPORT}
        clone_path = managed_clone_path(canonical)
        pointer = _write_pointer(stored)
        return {
            "written": True,
            "mode": "url",
            "url": stored,
            "canonical": canonical,
            "clone_path": str(clone_path),
            "clone_exists": valid_vault(clone_path),
            "pointer": str(pointer),
        }
    saved = os.environ.get(VAULT_ENV)
    os.environ[VAULT_ENV] = expanded or "-"  # 신 env는 read_pointer 최우선 — 구 env 오염 무관
    try:
        vault, reason = vault_state()
    finally:
        if saved is None:
            os.environ.pop(VAULT_ENV, None)
        else:
            os.environ[VAULT_ENV] = saved
    if vault is None:
        return {"written": False, "reason": reason}
    pointer = _write_pointer(vault)
    return {"written": True, "mode": "path", "vault": vault, "pointer": str(pointer)}


# --- CLI — doctor(V6)·feature 층 CLI가 소비하는 generic 기계 단계 -------------


def _cli_status(project: str) -> dict:
    vault, invalid = vault_state()
    return {
        "pointer": read_pointer(),
        "vault": vault,
        "invalid": invalid,
        "inject": resolve_inject(project),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="okf_vault", description="Vault 폴백 해석기(generic)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="현재 위치의 포인터·vault·주입 해소 결과(JSON)")
    status.add_argument("project", nargs="?", default=".")
    set_cmd = sub.add_parser("set", help="포인터 검증 후 기록(JSON)")
    set_cmd.add_argument("path")
    args = ap.parse_args(argv)
    if args.cmd == "status":
        result = _cli_status(os.path.abspath(args.project))
    else:
        result = set_pointer(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
