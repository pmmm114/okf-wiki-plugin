"""okf 홈 폴백 진단 스크립트 (#91 V6) — /okf-doctor의 실체.

판정·안내는 **전부 코드 경로**다(#20 — 프롬프트 재량 없음): 현재 위치의 스코프
해소 결과와 이유(결정 트레이스), 포인터·홈 건강(반쪽 상태·무의미 scope 조합·위임
블록 handlers 무시), 캡처 입구 진단(자동 메모리 활성·L0 후보·입구 생존), 양 스코프
inbox 대기 수, 미큐잉 회복 안내를 사람이 읽는 텍스트로 출력한다. stdlib 전용.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import okf_home
import okf_inbox
import study as study_cli


def _capture_trace(project: str) -> list[str]:
    block = okf_home.study_block(okf_home.load_config(project))
    home, reason = okf_home.home_state()
    scope = okf_home.resolve_capture(project)
    if block is not None and block.get("scope") == "home":
        why = 'study 블록의 scope:"home" 위임'
    elif block is not None:
        why = "프로젝트 study 블록 존재(명시가 이긴다)"
    elif scope["scope"] == "home":
        why = "study 블록 없음 → 유효 홈 폴백"
    elif reason is not None:
        why = f"홈 포인터 무효({reason})"
    elif home is not None:
        why = "홈이 캡처 비활성(주입 전용 홈 또는 capture=off)"
    else:
        why = "study 블록 없음 + 홈 포인터 없음(옵트인 안 함)"
    lines = [f"  스코프: {scope['scope']} (capture={scope['capture']}) ← {why}"]
    if scope["target"]:
        lines.append(f"  대상: {scope['target']}")
    return lines


def _inject_trace(project: str) -> list[str]:
    result = okf_home.resolve_inject(project)
    if result["scope"] == "project":
        why = ".okf-wiki.json 존재"
    elif result["scope"] == "home":
        why = "프로젝트 설정 없음 → 유효 홈"
    else:
        home, reason = okf_home.home_state()
        why = f"홈 포인터 무효({reason})" if reason else "설정·포인터 없음(또는 홈 inject=false)"
    lines = [f"  스코프: {result['scope']} ← {why}"]
    if result["target"]:
        lines.append(f"  대상: {result['target']}")
    return lines


def _home_notes(project: str) -> list[str]:
    lines = []
    pointer = okf_home.read_pointer()
    home, reason = okf_home.home_state()
    if pointer is None:
        lines.append("  포인터: 없음(옵트인 안 함)")
        return lines
    if reason is not None:
        lines.append(f"  포인터: {pointer} — 무효({reason})")
        return lines
    lines.append(f"  포인터: {home} (유효)")
    if okf_home.study_block(okf_home.load_config(home)) is None:
        lines.append("  메모: 반쪽 상태 — 주입 전용 홈(study 블록 없음, 캡처 비활성)")
    block = okf_home.study_block(okf_home.load_config(project))
    if block is not None and block.get("scope") == "home":
        if "capture" not in block:
            lines.append('  메모: scope:"home"인데 capture 부재 — 위임이 비활성(무의미 조합)')
        if block.get("handlers"):
            lines.append('  메모: scope:"home" 블록의 handlers는 무시됨(홈 핸들러 사용)')
    return lines


def _entrance_lines(project: str) -> list[str]:
    lines = []
    disabled = []
    if os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1":
        disabled.append("CLAUDE_CODE_DISABLE_AUTO_MEMORY=1")
    for path in okf_home._settings_paths(project):
        data = okf_home._read_json(path)
        if data is not None and data.get("autoMemoryEnabled") is False:
            disabled.append(f"autoMemoryEnabled:false @{path}")
    if disabled:
        lines.append(f"  자동 메모리: 비활성({' · '.join(disabled)}) — 캡처 트리거 자체가 없음")
    else:
        lines.append("  자동 메모리: 활성(비활성 신호 없음)")
    explicit = okf_home.memory_dir_candidates(project)
    config = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    shown = explicit or "(autoMemoryDirectory 없음)"
    lines.append(f"  L0 후보: {shown} + 기본형 {config}/projects/*/memory/")
    memory_dirs = study_cli._memory_dirs(project)
    latest: float | None = None
    for directory in memory_dirs:
        for path in directory.rglob("*.md"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
    if not memory_dirs:
        lines.append(
            "  입구 생존: 메모리 디렉토리 미발견 — 배치 변경 가능성. memoryPathPattern 검토"
        )
    elif latest is None:
        lines.append("  입구 생존: 디렉토리는 있으나 .md 기록 없음")
    else:
        import datetime

        stamp = datetime.datetime.fromtimestamp(latest).isoformat(timespec="seconds")
        lines.append(f"  입구 생존: 최근 기록 {stamp}")
    return lines


def _inbox_lines(project: str) -> list[str]:
    lines = []
    if okf_home.study_block(okf_home.load_config(project)) is not None:
        lines.append(f"  project 대기: {len(okf_inbox.list_candidates(project))}")
    home, _reason = okf_home.home_state()
    if home is not None:
        lines.append(f"  home 대기: {len(okf_inbox.list_candidates(home))}")
    return lines or ["  (활성 inbox 없음)"]


def _recovery_lines(project: str) -> list[str]:
    home, reason = okf_home.home_state()
    if reason is not None:
        return [
            "  홈 포인터가 무효다 — `/okf-init --home <경로>`로 수리한 뒤 "
            "`study scan`으로 미큐잉을 확인하라."
        ]
    target = okf_home.resolve_capture(project)["target"]
    if target is None:
        return []
    result = study_cli.scan_memory(target, enqueue=False)
    count = len(result["unqueued"])
    if count == 0:
        return []
    return [
        f"  미큐잉 후보 {count}개 — `python3 study.py scan {target} --enqueue`로 "
        "재적재 후 /study로 선별 승격하라."
    ]


def run(project: str) -> str:
    sections = [
        ("위치", [f"  {project}"]),
        ("캡처", _capture_trace(project)),
        ("주입", _inject_trace(project)),
        ("홈", _home_notes(project)),
        ("캡처 입구", _entrance_lines(project)),
        ("inbox", _inbox_lines(project)),
    ]
    recovery = _recovery_lines(project)
    if recovery:
        sections.append(("회복", recovery))
    out = ["== okf doctor =="]
    for title, lines in sections:
        out.append(f"[{title}]")
        out.extend(lines)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = os.path.abspath(argv[0]) if argv else os.path.abspath(".")
    if not Path(project).is_dir():
        print(f"okf_doctor: 디렉토리가 아님 — {project}", file=sys.stderr)
        return 1
    print(run(project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
