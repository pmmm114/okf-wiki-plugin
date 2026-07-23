"""study 캡처 스코프 해석기 — 캡처 정책·런타임 루트·캡처 입구 판정 (#145 U3).

okf_home(generic: 포인터·홈 판정·설정 로드·주입 해소) 위에 얹히는 study feature
층이다 — import는 study_scope→okf_home **단방향뿐**(#145 경계 원칙). 캡처 훅
(study_hook)·SessionStart 나즈(study_session)·승격 CLI(study)·doctor가 재사용
한다. stdlib 전용, 실패는 전부 "없음/무동작" 동치로 관용한다.

담당: study 블록 판정 · 캡처(쓰기) 스코프 해소(#91 §2 캡처 4단 · #114 런타임
루트 분리) · 홈 캡처 활성화(멱등) · 캡처 입구(메모리 경로) 판정(L0 ∪ L1 ∪ L3).
설정은 별도 파일이 아니라 ``.okf-wiki.json``의 ``study`` 블록이다
(``okf_home.load_config`` 재사용 — 단일 설정 파일 불변식, #145 비목표).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import okf_home


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
    block = study_block(okf_home.load_config(project))
    home, reason = okf_home.home_state()
    user_rt = str(user_scope_runtime())
    if block is not None:
        capture = block.get("capture", "off")
        if block.get("scope") == "home":
            # 규칙 1 — 위임 선언: 목적지만 홈, 레벨은 블록 값(부재=off)
            if home is None:
                return _cap(None, None, "off", "none", okf_home.pointer_warning(reason))
            return _cap(home, user_rt, capture, "home")
        # 규칙 2 — 명시가 이긴다(capture=off 포함). 단 프로젝트가 곧 홈이면 런타임은
        # 유저 스코프로 — 홈은 자기 study 블록이 있어도 런타임을 담지 않는다(#114 U1).
        if home is not None and _same_path(project, home):
            return _cap(home, user_rt, capture, "home")
        return _cap(str(project), _in_repo_runtime(project), capture, "project")
    # 규칙 3 — 블록 없음 → 홈 폴백
    if home is None:
        return _cap(None, None, "off", "none", okf_home.pointer_warning(reason))
    home_block = study_block(okf_home.load_config(home))
    home_capture = (home_block or {}).get("capture", "off")
    if home_capture in ("review", "auto"):
        return _cap(home, user_rt, home_capture, "home")
    # 반쪽 상태(주입 전용 홈) 또는 capture=off — 정상, 무경고 (#91 #13)
    return _cap(None, None, "off", "none", None)


def home_capture_state(home: str | Path) -> str:
    """유효 홈의 캡처 준비 상태를 반환한다 — 마법사·doctor의 기계 판정용.

    - ``"active"``: study 블록의 capture가 review/auto (위치 무관 적재가 켜짐)
    - ``"off"``: study 블록은 있으나 capture=off (명시적으로 꺼둠)
    - ``"absent"``: study 블록 없음 — "주입 전용 홈"(캡처가 폴백으로 꺼짐)
    """
    block = study_block(okf_home.load_config(home))
    if block is None:
        return "absent"
    return "active" if block.get("capture") in ("review", "auto") else "off"


def enable_home_capture(home: str | Path, level: str = "review") -> dict:
    """홈에 캡처를 켠다(멱등) — 홈 ``.okf-wiki.json``의 ``study.capture``(설정)만 올린다.

    **홈에 ``.okf-study`` 런타임을 만들지 않는다**(#114 U2 — 홈은 순수 목적지).
    런타임(inbox/ledger/trust)은 유저 스코프(``~/.claude/okf/study``)에 보장한다.
    이미 active면(review/auto) 격하하지 않는다. 판정·편집은 전부 이 코드 경로다
    (#20 — 프롬프트 재량 없음). 반환: 수행 상태 dict.
    """
    home = Path(home)
    before = home_capture_state(home)
    config_path = home / ".okf-wiki.json"
    data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    if not isinstance(data, dict):
        data = {}
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
    runtime = user_scope_runtime()  # 런타임은 홈이 아니라 유저 스코프에
    runtime.mkdir(parents=True, exist_ok=True)
    return {
        "before": before,
        "capture": block.get("capture"),
        "changed": changed,
        "runtime_root": str(runtime),
    }


# --- 캡처 입구(메모리 경로) 판정 — L0 ∪ L1 (∪ L3) (#91 §2, #15~#17) ---------


def _config_dir() -> str:
    # CLAUDE_CONFIG_DIR은 문서 미등재(비공식)지만 실사용 관례 — 기본형 해석에만 쓴다.
    return okf_home.expand(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def settings_paths(project: str | Path) -> list[Path]:
    """접근 가능한 settings.json 후보 — 우선순위 재현 없이 전부 조회(합집합 매칭 전제).

    공개 API — doctor의 자동메모리 진단이 함께 소비한다(#145 B3).
    """
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
    for path in settings_paths(project):
        data = okf_home.read_json(path)
        value = (data or {}).get("autoMemoryDirectory")
        if isinstance(value, str) and value.strip():
            expanded = okf_home.expand(value)
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
    block = study_block(okf_home.load_config(home)) or {}
    raw = block.get("memoryPathPattern")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return re.compile(raw)
    except re.error as exc:
        import sys

        print(f"study_scope: memoryPathPattern 무효 — 무시: {exc}", file=sys.stderr)
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
    home, _reason = okf_home.home_state()
    pattern = _home_pattern(home)
    if pattern is not None and pattern.search(file_path):
        return True
    return False


# --- CLI — /okf-init --home·/study --scope가 소비하는 기계 단계 ---------------


def _cli_status(project: str) -> dict:
    home, invalid = okf_home.home_state()
    return {
        "pointer": okf_home.read_pointer(),
        "home": home,
        "invalid": invalid,
        "capture": resolve_capture(project),
        "inject": okf_home.resolve_inject(project),
    }


def _cli_set(path: str) -> dict:
    """포인터를 검증 후 기록하고(generic 위임) 캡처 준비 상태를 덧붙인다.

    capture_ready로 "주입 전용 홈"을 기계 판정 → 마법사가 캡처 활성 제안 여부를 결정.
    URL 모드(#153)는 관리형 clone이 이미 있을 때만 capture_ready를 판정한다 — 미생성
    clone은 설정을 읽을 수 없으므로 마법사가 clone(옵트인) 후 재조회한다.
    """
    result = okf_home.set_pointer(path)
    if not result.get("written"):
        return result
    if result.get("mode") == "url":
        if result.get("clone_exists"):
            result["capture_ready"] = home_capture_state(result["clone_path"])
        return result
    result["capture_ready"] = home_capture_state(result["home"])
    return result


def _cli_enable_capture(home: str, level: str) -> dict:
    """홈에 캡처를 켠다(CLI). 유효 홈이 아니면 편집 없이 사유를 돌려준다."""
    expanded = okf_home.expand(home)
    saved = os.environ.get(okf_home.POINTER_ENV)
    os.environ[okf_home.POINTER_ENV] = expanded or "-"
    try:
        valid, reason = okf_home.home_state()
    finally:
        if saved is None:
            os.environ.pop(okf_home.POINTER_ENV, None)
        else:
            os.environ[okf_home.POINTER_ENV] = saved
    if valid is None:
        return {"enabled": False, "reason": reason}
    # URL 모드(#153 U2-6): 관리형 clone의 커밋된 .okf-wiki.json을 여기서 편집하면
    # origin과 diverge해 ff 신선도 갱신이 막힌다. 캡처 옵트인은 **원격 repo**에 커밋해야
    # 한다 — clone은 순수 소비 미러로 둔다.
    if okf_home.is_managed_clone(valid):
        return {
            "enabled": False,
            "reason": "managed-clone",
            "guidance": (
                "URL 홈은 관리형 clone이라 여기서 study.capture를 켜면 origin과 diverge한다 "
                "— 원격 repo에 study.capture(review/auto)를 커밋한 뒤 fetch로 반영하라"
            ),
        }
    result = enable_home_capture(valid, level)
    return {"enabled": True, "home": valid, **result}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="study_scope", description="study 캡처 스코프 해석기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="현재 위치의 스코프 해소 결과(JSON)")
    status.add_argument("project", nargs="?", default=".")
    set_cmd = sub.add_parser("set", help="포인터 검증 후 기록 + capture_ready(JSON)")
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
