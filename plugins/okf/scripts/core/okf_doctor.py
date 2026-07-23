"""okf 홈 폴백 진단 스크립트 (#91 V6) — /okf-doctor의 실체.

판정·안내는 **전부 코드 경로**다(#20 — 프롬프트 재량 없음): 현재 위치의 스코프
해소 결과와 이유(결정 트레이스), 포인터·홈 건강, 캡처 입구 진단, 양 스코프 inbox
대기 수, 미큐잉 회복 안내를 사람이 읽는 텍스트로 출력한다. stdlib 전용.

core⊥study 경계(#145 U4): 이 파일은 generic(okf_home)만 하드 의존하고, study
진단(캡처 트레이스·홈 study 메모·캡처 입구·스토어·inbox·이력·회복)은 아래
try-import 심 1개로 **선택 위임**한다 — study 모듈이 없으면 core 섹션(위치·주입·
홈)만으로 정상 동작한다. 이 심은 경계 게이트(#145 U2)의 유일한 allowlist 항목이다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import okf_home

try:
    import study_doctor  # 있으면 실행, 없으면 생략 — #145 U4 선택 위임 심
except ImportError as _exc:  # pragma: no cover - study 미배치 배포에서도 core 진단 생존
    study_doctor = None
    if _exc.name != "study_doctor":
        # 부분 배치(심은 있으나 연쇄 모듈 결손) — 조용히 '미배치'로 위장하지 않고
        # 결손 모듈명을 남긴다(진단 도구가 자기 절반의 결손을 은폐하면 안 된다).
        print(f"okf_doctor: ⚠ study 진단 생략 — 모듈 결손({_exc.name})", file=sys.stderr)


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
    """generic 홈 메모 — 포인터 상태 + 번들 부합. study 관점 메모는 심이 덧붙인다."""
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
    # 홈 부합(#114 U3) — 번들 존재 진단(홈 repo엔 큐레이션 번들이 필요)
    home_config = okf_home.load_config(home)
    bundle_path = ".okf"
    if isinstance(home_config, dict) and isinstance(home_config.get("bundlePath"), str):
        bundle_path = home_config["bundlePath"]
    if (Path(home) / bundle_path).is_dir():
        lines.append(
            f"  부합: 번들 {bundle_path} 있음(`okf validate {bundle_path} --strict`로 건강 확인)"
        )
    else:
        lines.append(f"  부합: ⚠ 번들 {bundle_path} 없음 — 홈 repo엔 큐레이션 번들이 필요")
    return lines


def run(project: str) -> str:
    sections: list[tuple[str, list[str]]] = [("위치", [f"  {project}"])]
    if study_doctor is not None:
        sections.append(("캡처", study_doctor.capture_trace(project)))
    sections.append(("주입", _inject_trace(project)))
    home_lines = _home_notes(project)
    if study_doctor is not None:
        home_lines = home_lines + study_doctor.home_notes(project)
    sections.append(("홈", home_lines))
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
