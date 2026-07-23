"""홈 프로젝트 폴백 공유 모듈 (#91 V2) — 포인터·홈 판정·설정 로드·주입 해소.

주입 훅(okf_hooks)·doctor·상위 feature 층이 재사용하는 generic 단일 해석기다
(훅별 배선은 각자, 해석은 여기 한 곳 — #91 §2 "블랭킷 변경 금지"와 "공유 모듈"
계약의 접점). 캡처(쓰기) 정책·런타임 루트·메모리 경로 판정 같은 feature 정책은
이 모듈이 아니라 feature 층 소관이다(#145 U3 분할) — 이 파일은 feature 층을
모르고, feature 층이 이 파일을 단방향으로 import한다. stdlib 전용, 실패는 전부
"없음/무동작" 동치로 관용한다.

용어(#91 v3.5):
- 포인터: ``~/.claude/okf/home-project``(env ``OKF_HOME_PROJECT`` 우선) 한 줄 경로.
- 유효 홈: 실재 + git repo + ``.okf-wiki.json`` 존재.
- 침묵 정책: 포인터 부재=무음, 존재·무효=SessionStart 계열만 1줄 경고(경고 문구는
  이 모듈이 만들고, 방출 여부는 호출자가 결정한다).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

POINTER_ENV = "OKF_HOME_PROJECT"
_POINTER_REL = ".claude/okf/home-project"

# 무효 사유 코드 (doctor·경고 문구 공용)
INVALID_MISSING = "대상 없음"
INVALID_NOT_GIT = "git repo 아님"
INVALID_NO_CONFIG = ".okf-wiki.json 없음"


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
    """포인터 원문(확장 후 경로)을 반환한다. 부재·빈 값은 None (옵트인 안 함)."""
    raw = os.environ.get(POINTER_ENV)
    if raw is None:
        pointer = Path(os.path.expanduser("~")) / _POINTER_REL
        try:
            raw = pointer.read_text(encoding="utf-8")
        except OSError:
            return None
    value = expand(raw)
    return value or None


def _is_git_repo(path: Path) -> bool:
    # .git은 디렉토리(일반) 또는 파일(worktree/서브모듈) — 존재만 본다.
    return (path / ".git").exists()


def home_state() -> tuple[str | None, str | None]:
    """(유효 홈 경로, 무효 사유)를 반환한다.

    - (None, None): 포인터 부재 — 옵트인 안 함(완전 무음)
    - (path, None): 유효 홈
    - (None, 사유): 포인터 존재·무효 — SessionStart 계열에서만 경고
    """
    value = read_pointer()
    if value is None:
        return None, None
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

    반환 dict: target(str|None) · scope("project"|"home"|"none") · warning(str|None)
    """
    if (Path(project) / ".okf-wiki.json").is_file():
        return {"target": str(project), "scope": "project", "warning": None}
    home, reason = home_state()
    if home is None:
        return {"target": None, "scope": "none", "warning": pointer_warning(reason)}
    config = load_config(home) or {}
    if config.get("inject") is False:
        return {"target": None, "scope": "none", "warning": None}
    return {"target": home, "scope": "home", "warning": None}


def pointer_warning(reason: str | None) -> str | None:
    """무효 포인터 경고 문구 (공개 헬퍼 — 주입·캡처 해석기가 공용)."""
    if reason is None:
        return None
    return f"okf: 홈 포인터 무효({reason}) — /okf-doctor로 진단"


def set_pointer(path: str) -> dict:
    """포인터를 검증 후 기록한다. 무효 대상은 기록하지 않고 사유를 돌려준다."""
    expanded = expand(path)
    saved = os.environ.get(POINTER_ENV)
    os.environ[POINTER_ENV] = expanded or "-"
    try:
        home, reason = home_state()
    finally:
        if saved is None:
            os.environ.pop(POINTER_ENV, None)
        else:
            os.environ[POINTER_ENV] = saved
    if home is None:
        return {"written": False, "reason": reason}
    pointer = Path(os.path.expanduser("~")) / _POINTER_REL
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(home + "\n", encoding="utf-8")
    return {"written": True, "home": home, "pointer": str(pointer)}


# --- CLI — doctor(V6)·feature 층 CLI가 소비하는 generic 기계 단계 -------------


def _cli_status(project: str) -> dict:
    home, invalid = home_state()
    return {
        "pointer": read_pointer(),
        "home": home,
        "invalid": invalid,
        "inject": resolve_inject(project),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="okf_home", description="홈 프로젝트 폴백 해석기(generic)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="현재 위치의 포인터·홈·주입 해소 결과(JSON)")
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
