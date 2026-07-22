"""홈 프로젝트 폴백 공유 모듈 (#91 V2) — 포인터·스코프 해소·캡처 입구 판정.

캡처 훅(study_hook)·SessionStart 나즈(study_session)·주입 훅·doctor가 재사용하는
단일 해석기다(훅별 배선은 각자, 해석은 여기 한 곳 — #91 §2 "블랭킷 변경 금지"와
"공유 모듈" 계약의 접점). stdlib 전용, 실패는 전부 "없음/무동작" 동치로 관용한다.

용어(#91 v3.5):
- 포인터: ``~/.claude/okf/home-project``(env ``OKF_HOME_PROJECT`` 우선) 한 줄 경로.
- 유효 홈: 실재 + git repo + ``.okf-wiki.json`` 존재. 설정은 있으나 study 블록이
  없는 반쪽 상태는 "주입 전용 홈"(정상)이다.
- 침묵 정책: 포인터 부재=무음, 존재·무효=SessionStart 계열만 1줄 경고(경고 문구는
  이 모듈이 만들고, 방출 여부는 호출자가 결정한다).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

POINTER_ENV = "OKF_HOME_PROJECT"
_POINTER_REL = ".claude/okf/home-project"

# 무효 사유 코드 (doctor·경고 문구 공용)
INVALID_MISSING = "대상 없음"
INVALID_NOT_GIT = "git repo 아님"
INVALID_NO_CONFIG = ".okf-wiki.json 없음"


def _read_json(path: Path):
    """JSON 파일을 관용적으로 읽는다 — 부재·깨짐·비객체는 전부 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _expand(value: str) -> str:
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
    value = _expand(raw)
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
    return _read_json(Path(project) / ".okf-wiki.json")


def study_block(config: dict | None) -> dict | None:
    """설정에서 study 블록을 꺼낸다 — 부재·비객체는 None (블록 없음 동치)."""
    if not isinstance(config, dict):
        return None
    block = config.get("study")
    return block if isinstance(block, dict) else None


# 런타임(inbox/ledger/trust) 디렉토리 이름 — in-repo 파이프라인용. 유저 스코프는
# 포인터(~/.claude/okf/home-project)와 co-located된 ~/.claude/okf/study.
_RUNTIME_DIR_NAME = ".okf-study"


def user_scope_runtime() -> Path:
    """폴백·홈 캡처의 유저 스코프 런타임 루트(~/.claude/okf/study).

    홈은 순수 목적지이므로 런타임(inbox/ledger/trust)을 담지 않는다(#114) — 자기
    파이프라인이 없는 곳(비-git·okf 없는 repo)과 홈 폴백의 스테이징은 포인터와
    같은 유저 스코프 디렉토리에 모인다.
    """
    return Path(os.path.expanduser("~")) / ".claude" / "okf" / "study"


def _in_repo_runtime(project: str | Path) -> str:
    return str(Path(project) / _RUNTIME_DIR_NAME)


def _same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _cap(target: str | None, runtime_root: str | None, capture: str, scope: str, warning=None):
    return {
        "target": target,
        "runtime_root": runtime_root,
        "capture": capture,
        "scope": scope,
        "warning": warning,
    }


def resolve_capture(project: str | Path) -> dict:
    """캡처(쓰기) 스코프를 해소한다 (#91 §2 캡처 4단 · #114 런타임 루트 분리).

    반환 dict:
    - target: **승격 대상**(‘.okf/`가 있는 곳) 경로(str) 또는 None(무동작)
    - runtime_root: **런타임**(inbox/ledger/trust)이 사는 디렉토리(str) 또는 None
      — 자기 파이프라인 repo면 ``<repo>/.okf-study``, 홈/폴백이면 유저 스코프
    - capture: 유효 캡처 레벨("off"|"review"|"auto")
    - scope: "project" | "home" | "none"
    - warning: 무효 포인터 경고 문구(str) 또는 None — 방출은 SessionStart 계열만
    """
    block = study_block(load_config(project))
    home, reason = home_state()
    user_rt = str(user_scope_runtime())
    if block is not None:
        capture = block.get("capture", "off")
        if block.get("scope") == "home":
            # 규칙 1 — 위임 선언: 목적지만 홈, 레벨은 블록 값(부재=off)
            if home is None:
                return _cap(None, None, "off", "none", _warning(reason))
            return _cap(home, user_rt, capture, "home")
        # 규칙 2 — 명시가 이긴다(capture=off 포함). 단 프로젝트가 곧 홈이면 런타임은
        # 유저 스코프로 — 홈은 자기 study 블록이 있어도 런타임을 담지 않는다(#114 U1).
        if home is not None and _same_path(project, home):
            return _cap(home, user_rt, capture, "home")
        return _cap(str(project), _in_repo_runtime(project), capture, "project")
    # 규칙 3 — 블록 없음 → 홈 폴백
    if home is None:
        return _cap(None, None, "off", "none", _warning(reason))
    home_block = study_block(load_config(home))
    home_capture = (home_block or {}).get("capture", "off")
    if home_capture in ("review", "auto"):
        return _cap(home, user_rt, home_capture, "home")
    # 반쪽 상태(주입 전용 홈) 또는 capture=off — 정상, 무경고 (#91 #13)
    return _cap(None, None, "off", "none", None)


def resolve_inject(project: str | Path) -> dict:
    """주입(읽기) 스코프를 해소한다 (#91 §2 주입 3단 규칙).

    반환 dict: target(str|None) · scope("project"|"home"|"none") · warning(str|None)
    """
    if (Path(project) / ".okf-wiki.json").is_file():
        return {"target": str(project), "scope": "project", "warning": None}
    home, reason = home_state()
    if home is None:
        return {"target": None, "scope": "none", "warning": _warning(reason)}
    config = load_config(home) or {}
    if config.get("inject") is False:
        return {"target": None, "scope": "none", "warning": None}
    return {"target": home, "scope": "home", "warning": None}


def home_capture_state(home: str | Path) -> str:
    """유효 홈의 캡처 준비 상태를 반환한다 — 마법사·doctor의 기계 판정용.

    - ``"active"``: study 블록의 capture가 review/auto (위치 무관 적재가 켜짐)
    - ``"off"``: study 블록은 있으나 capture=off (명시적으로 꺼둠)
    - ``"absent"``: study 블록 없음 — "주입 전용 홈"(캡처가 폴백으로 꺼짐)
    """
    block = study_block(load_config(home))
    if block is None:
        return "absent"
    return "active" if block.get("capture") in ("review", "auto") else "off"


def enable_home_capture(home: str | Path, level: str = "review") -> dict:
    """홈에 캡처를 켠다(멱등) — study 런타임 스캐폴드 후 capture를 level로 올린다.

    이미 active면(review/auto) 격하하지 않고 그대로 둔다. study 블록·``.okf-study``
    골격이 없으면 스캐폴더로 보장한다(기존 키·핸들러 보존). 반환: 수행 상태 dict.
    판정·편집은 전부 이 코드 경로다(#91 #20 — 프롬프트 재량 없음).
    """
    import study_scaffold  # lazy — study_scaffold가 okf_home을 import(순환 회피)

    home = Path(home)
    before = home_capture_state(home)
    scaffold_actions = study_scaffold.scaffold(home)  # .okf-study + study(off) 보장
    config_path = home / ".okf-wiki.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    block = data.get("study")
    if not isinstance(block, dict):
        block = {}
        data["study"] = block
    if block.get("capture") in ("review", "auto"):
        changed = False  # 이미 활성 — 격하 금지
    else:
        block["capture"] = level
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        changed = True
    return {
        "before": before,
        "capture": block.get("capture"),
        "changed": changed,
        "scaffold": scaffold_actions,
    }


def _warning(reason: str | None) -> str | None:
    if reason is None:
        return None
    return f"okf: 홈 포인터 무효({reason}) — /okf-doctor로 진단"


# --- 캡처 입구(메모리 경로) 판정 — L0 ∪ L1 (∪ L3) (#91 §2, #15~#17) ---------


def _config_dir() -> str:
    # CLAUDE_CONFIG_DIR은 문서 미등재(비공식)지만 실사용 관례 — 기본형 해석에만 쓴다.
    return _expand(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def _settings_paths(project: str | Path) -> list[Path]:
    # 접근 가능한 settings.json 후보 — 우선순위 재현 없이 전부 조회(합집합 매칭 전제).
    proj = Path(project)
    return [
        Path("/etc/claude-code/managed-settings.json"),
        Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
        proj / ".claude" / "settings.local.json",
        proj / ".claude" / "settings.json",
        Path(_config_dir()) / "settings.json",
    ]


def memory_dir_candidates(project: str | Path) -> list[str]:
    """L0 명시 디렉토리 후보 — 발견된 autoMemoryDirectory 값 전부(~/ 확장)."""
    dirs: list[str] = []
    for path in _settings_paths(project):
        data = _read_json(path)
        value = (data or {}).get("autoMemoryDirectory")
        if isinstance(value, str) and value.strip():
            expanded = _expand(value)
            if os.path.isabs(expanded) and expanded not in dirs:
                dirs.append(expanded)
    return dirs


# 레거시 느슨형 — 어느 프리픽스든 `/.claude/projects/<x>/memory/<f>.md`로 끝나면 인정.
# 구현 교체 전의 유일한 판정이었고, 합집합 후보로 유지해 무회귀를 보장한다(#91 R3).
_LEGACY_MEMORY_RE = re.compile(r"/\.claude/projects/[^/]+/memory/[^/]+\.md$")


def _default_form_re() -> re.Pattern[str]:
    # 문서화된 기본형 — <config>/projects/<단일요소>/memory/<파일>.md (#16 수정 지점)
    return re.compile(re.escape(_config_dir()) + r"/projects/[^/]+/memory/[^/]+\.md$")


def _home_pattern(home: str | None) -> re.Pattern[str] | None:
    """L3 — 홈 설정의 memoryPathPattern. 무효 정규식은 stderr 1줄 후 무시."""
    if home is None:
        return None
    block = study_block(load_config(home)) or {}
    raw = block.get("memoryPathPattern")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return re.compile(raw)
    except re.error as exc:
        import sys

        print(f"okf_home: memoryPathPattern 무효 — 무시: {exc}", file=sys.stderr)
        return None


def is_memory_path(file_path: str, payload: dict | None, project: str | Path) -> bool:
    """file_path가 메모리 저장인지 후보 집합(L0 ∪ L1 ∪ L3)으로 판정한다.

    project는 L0의 프로젝트 스코프 settings 조회용(활성 스코프와 무관하게 cwd 기준).
    """
    if not file_path.endswith(".md"):
        return False
    # L0 — 명시 디렉토리(autoMemoryDirectory): 하위 깊이 무관 프리픽스 (#17 수정)
    for dir_ in memory_dir_candidates(project):
        if file_path.startswith(dir_.rstrip("/") + "/"):
            return True
    # L0 — 문서화된 기본형(CLAUDE_CONFIG_DIR 반영 — #16 수정) + 레거시 느슨형(무회귀)
    if _default_form_re().search(file_path) or _LEGACY_MEMORY_RE.search(file_path):
        return True
    # L1 — transcript 형제 memory/ (관례 백스톱)
    transcript = (payload or {}).get("transcript_path")
    if isinstance(transcript, str) and transcript:
        sibling = os.path.join(os.path.dirname(transcript), "memory")
        if file_path.startswith(sibling.rstrip("/") + "/"):
            return True
    # L3 — 홈 설정 오버라이드(홈이 유효할 때만 의미)
    home, _reason = home_state()
    pattern = _home_pattern(home)
    if pattern is not None and pattern.search(file_path):
        return True
    return False


# --- CLI — /okf-init --home·/study --scope·doctor(V6)가 소비하는 기계 단계 ---


def _cli_status(project: str) -> dict:
    return {
        "pointer": read_pointer(),
        "home": home_state()[0],
        "invalid": home_state()[1],
        "capture": resolve_capture(project),
        "inject": resolve_inject(project),
    }


def _cli_set(path: str) -> dict:
    """포인터를 검증 후 기록한다. 무효 대상은 기록하지 않고 사유를 돌려준다."""
    expanded = _expand(path)
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
    # capture_ready로 "주입 전용 홈"을 기계 판정 → 마법사가 캡처 활성 제안 여부를 결정.
    return {
        "written": True,
        "home": home,
        "pointer": str(pointer),
        "capture_ready": home_capture_state(home),
    }


def _cli_enable_capture(home: str, level: str) -> dict:
    """홈에 캡처를 켠다(CLI). 유효 홈이 아니면 편집 없이 사유를 돌려준다."""
    expanded = _expand(home)
    saved = os.environ.get(POINTER_ENV)
    os.environ[POINTER_ENV] = expanded or "-"
    try:
        valid, reason = home_state()
    finally:
        if saved is None:
            os.environ.pop(POINTER_ENV, None)
        else:
            os.environ[POINTER_ENV] = saved
    if valid is None:
        return {"enabled": False, "reason": reason}
    result = enable_home_capture(valid, level)
    return {"enabled": True, "home": valid, **result}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="okf_home", description="홈 프로젝트 폴백 해석기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="현재 위치의 스코프 해소 결과(JSON)")
    status.add_argument("project", nargs="?", default=".")
    set_cmd = sub.add_parser("set", help="포인터 검증 후 기록(JSON)")
    set_cmd.add_argument("path")
    enable = sub.add_parser("enable-capture", help="홈에 캡처(study.capture=review)를 켠다(JSON)")
    enable.add_argument("home")
    enable.add_argument("--level", default="review", choices=["review", "auto"])
    args = ap.parse_args(argv)
    if args.cmd == "status":
        result = _cli_status(os.path.abspath(args.project))
    elif args.cmd == "enable-capture":
        result = _cli_enable_capture(args.home, args.level)
    else:
        result = _cli_set(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
