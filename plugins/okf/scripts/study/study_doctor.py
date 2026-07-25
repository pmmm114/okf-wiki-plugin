"""study 진단 섹션 (#145 U4) — okf_doctor가 선택 위임하는 study 절반.

core doctor(okf_doctor)는 이 모듈을 **try-import 심 1개**로 위임한다 — 있으면
섹션을 이어붙이고, 없으면 core 섹션(위치·주입·vault)만으로 정상 동작한다("있으면
실행, 없으면 생략"). 이 심은 core⊥study import 게이트(#145 U2)의 유일한
allowlist 항목이다. 판정·안내는 전부 코드 경로(#20), stdlib 전용.

담당 섹션: 캡처 트레이스 · vault study 메모(캡처 준비 제안·런타임 잔존·scope 조합) ·
캡처 입구(자동 메모리·L0 후보·입구 생존) · 스토어 · inbox · 최근 이력 · 회복.
"""

from __future__ import annotations

import os
from pathlib import Path

import okf_vault
import study as study_cli
import study_blocks
import study_inbox
import study_legacy
import study_scope
import study_store


def capture_trace(project: str) -> list[str]:
    block = study_scope.study_block(okf_vault.load_config(project))
    vault, reason = okf_vault.vault_state()
    scope = study_scope.resolve_capture(project)
    if block is not None and study_scope.is_vault_scope(block.get("scope")):
        why = 'study 블록의 scope:"vault" 위임'
    elif block is not None:
        why = "프로젝트 study 블록 존재(명시가 이긴다)"
    elif scope["scope"] == "vault":
        why = "study 블록 없음 → 유효 vault 폴백"
    elif reason is not None:
        why = f"Vault 포인터 무효({reason})"
    elif vault is not None:
        why = "vault가 캡처 비활성(주입 전용 vault 또는 capture=off)"
    else:
        why = "study 블록 없음 + vault 포인터 없음(옵트인 안 함)"
    lines = [f"  스코프: {scope['scope']} (capture={scope['capture']}) ← {why}"]
    if scope["target"]:
        lines.append(f"  승격 대상: {scope['target']}")
    if scope["runtime_root"]:
        lines.append(f"  적재(런타임): {scope['runtime_root']}")
    return lines


def vault_notes(project: str) -> list[str]:
    """유효 vault에 대한 study 관점 메모 — vault 무효·부재면 [] (core 쪽이 이미 안내)."""
    vault, reason = okf_vault.vault_state()
    if vault is None or reason is not None:
        return []
    lines = []
    cap_state = study_scope.vault_capture_state(vault)
    if cap_state == "absent":
        lines.append(
            "  메모: 주입 전용 vault(study 블록 없음, 캡처 비활성) — 위치 무관 적재를 "
            "켜려면 `/okf-init --vault <vault>` 재실행(캡처 활성 제안)"
        )
    elif cap_state == "off":
        lines.append(
            "  메모: vault 캡처 off(capture=off) — 켜려면 vault study.capture를 review로 "
            "(또는 `/okf-init --vault <vault>` 재실행)"
        )
    if (Path(vault) / ".okf-study").exists():
        lines.append(
            "  부합: ⚠ vault에 `.okf-study` 런타임 잔존 — vault는 순수 목적지여야 한다. "
            "`study migrate`로 유저 스코프 이동(#114)"
        )
    block = study_scope.study_block(okf_vault.load_config(project))
    if block is not None and study_scope.is_vault_scope(block.get("scope")):
        if "capture" not in block:
            lines.append('  메모: scope:"vault"인데 capture 부재 — 위임이 비활성(무의미 조합)')
        if block.get("handlers"):
            lines.append('  메모: scope:"vault" 블록의 handlers는 무시됨(vault 핸들러 사용)')
    return lines


def _entrance_lines(project: str) -> list[str]:
    lines = []
    disabled = []
    if os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1":
        disabled.append("CLAUDE_CODE_DISABLE_AUTO_MEMORY=1")
    for path in study_scope.settings_paths(project):
        data = okf_vault.read_json(path)
        if data is not None and data.get("autoMemoryEnabled") is False:
            disabled.append(f"autoMemoryEnabled:false @{path}")
    if disabled:
        lines.append(f"  자동 메모리: 비활성({' · '.join(disabled)}) — 캡처 트리거 자체가 없음")
    else:
        lines.append("  자동 메모리: 활성(비활성 신호 없음)")
    explicit = study_scope.memory_dir_candidates(project)
    config = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    shown = explicit or "(autoMemoryDirectory 없음)"
    lines.append(f"  L0 후보: {shown} + 기본형 {config}/projects/*/memory/")
    memory_dirs = study_cli.memory_dirs(project)
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


def _pending_summary(runtime: str) -> str:
    """대기 수 + 파일 수(#257) + 재등장(recurrence>1) 후보 수 요약(#132)."""
    cands = study_inbox.list_candidates(runtime)
    recurring = sum(1 for c in cands if c.get("recurrence", 1) > 1)
    parts = [f"파일 {len({c['source'] for c in cands})}"] if cands else []
    if recurring:
        parts.append(f"재등장 {recurring}")
    return f"{len(cands)}" + (f" ({', '.join(parts)})" if parts else "")


def _store_notes(project: str) -> list[str]:
    # 스테이징 스토어 건강(#130) + 레거시 markdown 잔존 감지·마이그레이션 안내(#134)
    if not study_store.available():
        return [
            "  ⚠ 이 파이썬에 sqlite3(_sqlite3) 없음 — 스테이징 비활성(fail-closed). "
            "OKF_PYTHON을 SQLite 포함 파이썬으로 지정하라."
        ]
    lines = ["  sqlite3: 사용 가능"]
    if study_legacy.has_legacy(str(study_scope.user_scope_runtime())):
        lines.append(
            "  ⚠ 유저 스코프에 레거시 markdown 스테이징 잔존 — `study migrate`로 study.db 이관"
        )
    vault, _reason = okf_vault.vault_state()
    if vault is not None and study_legacy.has_legacy(Path(vault) / ".okf-study"):
        lines.append(
            "  ⚠ vault에 레거시 markdown 스테이징 잔존 — `study migrate`로 유저 스코프 이관"
        )
    return lines


def _prune_cmd(project_arg: str) -> str:
    plugin = Path(__file__).resolve().parent.parent.parent
    return f'"{plugin}/bin/okf-py" "{plugin}/scripts/study/study.py" prune {project_arg}'


def _noise_advisory(runtime: str, cmd: str | None) -> list[str]:
    """기적재 노이즈가 있으면 prune 자문 1줄(#263) — discard 오폭(원장 비가역 오염) 방지.

    is_noise_snippet은 prune과 같은 텍스트 근사(#256) — 안내 대상과 prune 매치가 일치한다.
    cmd가 None이면 어떤 project 인자로도 prune이 이 런타임에 닿지 않는 배치(주입 전용
    vault 등) — 실행 불가 명령 인용은 오도라 카운트만 보인다.
    """
    cands = study_inbox.list_candidates(runtime)
    noise = sum(1 for c in cands if study_blocks.is_noise_snippet(c["snippet"]))
    if not noise:
        return []
    if cmd is None:
        return [f"  노이즈 {noise}건(기적재 구조 노이즈) — discard가 아니라 `study prune` 대상"]
    return [
        f"  노이즈 {noise}건 — `{cmd}`로 원장 오염 없이 정리"
        "(--dry-run 선행으로 오폭 검토, discard는 원장 영구 기록)"
    ]


def _inbox_lines(project: str) -> list[str]:
    lines = []
    scope = study_scope.resolve_capture(project)
    if scope["runtime_root"] and scope["scope"] == "project":
        lines.append(f"  project 대기: {_pending_summary(scope['runtime_root'])}")
        lines += _noise_advisory(scope["runtime_root"], _prune_cmd(project))
    vault, _reason = okf_vault.vault_state()
    if vault is not None:
        shared = str(study_scope.user_scope_runtime())
        lines.append(f"  vault(유저 스코프) 대기: {_pending_summary(shared)}")
        # 도달성 게이트(#263): vault 경로를 project 인자로 준 prune이 유저 스코프로
        # 해소될 때만 명령을 인용한다 — 주입 전용 vault는 어떤 인자로도 닿지 않는다.
        reachable = study_scope.resolve_capture(str(vault))["runtime_root"] == shared
        lines += _noise_advisory(shared, _prune_cmd(str(vault)) if reachable else None)
    return lines or ["  (활성 inbox 없음)"]


def _journal_lines(project: str) -> list[str]:
    # 최근 이벤트 저널(순서·시각) — 비-git 스테이징의 로그(#114 U5)
    runtime = study_scope.resolve_capture(project)["runtime_root"]
    if runtime is None:
        return ["  (활성 런타임 없음)"]
    events = study_inbox.read_journal(runtime, limit=5)
    if not events:
        return ["  (이력 없음)"]
    return [f"  {e.get('ts', '?')} {e.get('action', '?')} {e.get('id', '?')}" for e in events]


def _recovery_lines(project: str) -> list[str]:
    vault, reason = okf_vault.vault_state()
    if reason is not None:
        return [
            "  Vault 포인터가 무효다 — `/okf-init --vault <경로>`로 수리한 뒤 "
            "`study scan`으로 미큐잉을 확인하라."
        ]
    scope = study_scope.resolve_capture(project)
    runtime = scope["runtime_root"]
    if runtime is None:
        return []
    result = study_cli.scan_memory(project, runtime, enqueue=False)
    unqueued = result["unqueued"]
    if not unqueued:
        return []
    files = len({c["source"] for c in unqueued})  # 후보(블록/줄) 수가 아닌 파일 수로 집계
    plugin = Path(__file__).resolve().parent.parent.parent
    cmd = f'"{plugin}/bin/okf-py" "{plugin}/scripts/study/study.py" scan {project} --enqueue'
    return [f"  미큐잉 후보가 있는 파일 {files}개 — `{cmd}`로 재적재 후 /study로 선별 승격하라."]


def tail_sections(project: str) -> list[tuple[str, list[str]]]:
    """캡처 입구부터의 study 꼬리 섹션들 — 회복은 내용이 있을 때만 붙는다."""
    sections = [
        ("캡처 입구", _entrance_lines(project)),
        ("스토어", _store_notes(project)),
        ("inbox", _inbox_lines(project)),
        ("최근 이력", _journal_lines(project)),
    ]
    recovery = _recovery_lines(project)
    if recovery:
        sections.append(("회복", recovery))
    return sections
