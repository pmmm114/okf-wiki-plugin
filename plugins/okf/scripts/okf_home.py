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


def resolve_capture(project: str | Path) -> dict:
    """캡처(쓰기) 스코프를 해소한다 (#91 §2 캡처 4단 규칙).

    반환 dict:
    - target: 적재 대상 프로젝트 경로(str) 또는 None(무동작)
    - capture: 유효 캡처 레벨("off"|"review"|"auto")
    - scope: "project" | "home" | "none"
    - warning: 무효 포인터 경고 문구(str) 또는 None — 방출은 SessionStart 계열만
    """
    block = study_block(load_config(project))
    if block is not None:
        capture = block.get("capture", "off")
        if block.get("scope") == "home":
            # 규칙 1 — 위임 선언: 목적지만 홈, 레벨은 블록 값(부재=off)
            home, reason = home_state()
            if home is None:
                # 포인터 부재=무음, 무효=경고(문구만 생성)
                return {
                    "target": None,
                    "capture": "off",
                    "scope": "none",
                    "warning": _warning(reason),
                }
            return {"target": home, "capture": capture, "scope": "home", "warning": None}
        # 규칙 2 — 명시가 이긴다(capture=off 포함)
        return {"target": str(project), "capture": capture, "scope": "project", "warning": None}
    # 규칙 3 — 블록 없음 → 홈 폴백
    home, reason = home_state()
    if home is None:
        return {"target": None, "capture": "off", "scope": "none", "warning": _warning(reason)}
    home_block = study_block(load_config(home))
    home_capture = (home_block or {}).get("capture", "off")
    if home_capture in ("review", "auto"):
        return {"target": home, "capture": home_capture, "scope": "home", "warning": None}
    # 반쪽 상태(주입 전용 홈) 또는 capture=off — 정상, 무경고 (#91 #13)
    return {"target": None, "capture": "off", "scope": "none", "warning": None}


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
    # [고의-red] L0 명시 디렉토리 판정 제거 — #16·#17 게이트 실증용
    return bool(_LEGACY_MEMORY_RE.search(file_path))
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
