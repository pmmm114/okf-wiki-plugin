"""okf Vault 폴백 진단 스크립트 (#91 V6) — /okf-doctor의 실체.

판정·안내는 **전부 코드 경로**다(#20 — 프롬프트 재량 없음): 현재 위치의 스코프
해소 결과와 이유(결정 트레이스), 포인터·vault 건강, 캡처 입구 진단, 양 스코프 inbox
대기 수, 미큐잉 회복 안내를 사람이 읽는 텍스트로 출력한다. stdlib 전용.

core⊥study 경계(#145 U4): 이 파일은 generic(okf_vault)만 하드 의존하고, study
진단(캡처 트레이스·vault study 메모·캡처 입구·스토어·inbox·이력·회복)은 아래
try-import 심 1개로 **선택 위임**한다 — study 모듈이 없으면 core 섹션(위치·주입·
vault)만으로 정상 동작한다. 이 심은 경계 게이트(#145 U2)의 유일한 allowlist 항목이다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import okf_remote
import okf_vault

try:
    import study_doctor  # 있으면 실행, 없으면 생략 — #145 U4 선택 위임 심
except ImportError as _exc:  # pragma: no cover - study 미배치 배포에서도 core 진단 생존
    study_doctor = None
    if _exc.name != "study_doctor":
        # 부분 배치(심은 있으나 연쇄 모듈 결손) — 조용히 '미배치'로 위장하지 않고
        # 결손 모듈명을 남긴다(진단 도구가 자기 절반의 결손을 은폐하면 안 된다).
        print(f"okf_doctor: ⚠ study 진단 생략 — 모듈 결손({_exc.name})", file=sys.stderr)


def _inject_trace(project: str) -> list[str]:
    result = okf_vault.resolve_inject(project)
    if result["scope"] == "project":
        why = ".okf-wiki.json 존재"
    elif result["scope"] == "vault":
        why = "프로젝트 설정 없음 → 유효 vault"
    else:
        vault, reason = okf_vault.vault_state()
        if reason:
            why = f"Vault 포인터 무효({reason})"
        else:
            why = "설정·포인터 없음(또는 vault inject=false)"
    lines = [f"  스코프: {result['scope']} ← {why}"]
    if result["target"]:
        lines.append(f"  대상: {result['target']}")
    return lines


def _bundle_notes(vault: str) -> list[str]:
    """Vault 부합(#114 U3) — 번들 존재 진단(vault repo엔 큐레이션 번들이 필요)."""
    vault_config = okf_vault.load_config(vault)
    bundle_path = ".okf"
    if isinstance(vault_config, dict) and isinstance(vault_config.get("bundlePath"), str):
        bundle_path = vault_config["bundlePath"]
    if (Path(vault) / bundle_path).is_dir():
        return [
            f"  부합: 번들 {bundle_path} 있음(`okf validate {bundle_path} --strict`로 건강 확인)"
        ]
    return [f"  부합: ⚠ 번들 {bundle_path} 없음 — vault repo엔 큐레이션 번들이 필요"]


def _vault_notes(project: str) -> list[str]:
    """generic vault 메모 — 포인터 상태 + 번들 부합. study 관점 메모는 심이 덧붙인다.

    URL 모드(#153): 포인터가 URL이면 관리형 clone의 무네트워크 신선도(모드·clone
    상태·마지막 fetch·behind·dirty)를 okf_remote에 위임한다 — doctor는 능동 fetch를
    하지 않는다(U1-8). 로컬 경로 vault는 같은 origin의 관리형 clone 이원화를 표면화한다(U4-7).
    """
    lines = []
    notice = okf_vault.legacy_surface_notice()  # 구 env·파일 감지 시 마이그레이션 1줄(#152)
    if notice:
        lines.append(f"  ⚠ {notice}")
    pointer = okf_vault.read_pointer()
    vault, reason = okf_vault.vault_state()
    if pointer is None:
        lines.append("  포인터: 없음(옵트인 안 함)")
        return lines
    if okf_vault.is_url(pointer):
        lines.extend(okf_remote.doctor_vault_notes(pointer))
        if reason is None:  # 유효 관리형 clone이면 번들 부합까지
            lines.extend(_bundle_notes(vault))
        return lines
    if reason is not None:
        lines.append(f"  포인터: {pointer} — 무효({reason})")
        return lines
    lines.append(f"  포인터: {vault} (유효)")
    lines.extend(_bundle_notes(vault))
    twin = okf_remote.dualization_note(pointer, vault)  # 로컬↔관리형 clone 이원화(U4-7)
    if twin:
        lines.append(twin)
    return lines


def run(project: str) -> str:
    sections: list[tuple[str, list[str]]] = [("위치", [f"  {project}"])]
    if study_doctor is not None:
        sections.append(("캡처", study_doctor.capture_trace(project)))
    sections.append(("주입", _inject_trace(project)))
    vault_lines = _vault_notes(project)
    if study_doctor is not None:
        vault_lines = vault_lines + study_doctor.vault_notes(project)
    # 한 줄 정의 병기 — [홈]의 모호함("뭔지 몰랐다") 해소(#152 오독 사례 1)
    sections.append(("Vault", ["  (지식 저장고 — 주입 원천·승격 목적지)", *vault_lines]))
    if study_doctor is not None:
        sections.extend(study_doctor.tail_sections(project))
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
