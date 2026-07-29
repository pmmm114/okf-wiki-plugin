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
import shutil
import subprocess
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


def _resolve_bundle(vault: str) -> tuple[str, str | None]:
    """(쓸 상대경로, 거부된 선언). 설정이 없거나 정상이면 두 번째는 ``None``.

    거부를 **반환값에 실어** 보내는 이유: 조용히 갈아타면 doctor가 "번들 .okf 없음"만
    말하고, `../shared`로 선언한 사용자는 자기 선언이 무시된 줄 모른 채 없는 `.okf`를
    만들러 간다. 판정은 여기 하나에 두고 문구만 호출처가 붙인다.
    """
    config = okf_vault.load_config(vault)
    declared = config.get("bundlePath") if isinstance(config, dict) else None
    if not isinstance(declared, str) or not declared.strip():
        return ".okf", None
    # vault 밖을 가리키는 선언은 쓰지 않는다 — 이 값이 잔재 열거의 **범위**로도 가므로
    # 절대경로·상위 탈출을 그대로 넘기면 사용자의 vault 밖 작업이 열거된다(#266 U5).
    candidate = declared.strip()
    root = Path(vault).resolve()
    try:
        (root / candidate).resolve().relative_to(root)
    except ValueError:
        return ".okf", candidate
    return candidate, None


def _bundle_rel(vault: str) -> str:
    """vault 안 번들의 상대 경로 — 잔재 회계의 범위 인자로도 쓴다(#266 U5).

    두 소비처가 같은 해소를 써야 진단이 가리키는 번들과 열거하는 범위가 갈리지 않는다.
    거부 사실은 ``_bundle_notes``가 말하므로 여기선 쓸 경로만 낸다.
    """
    return _resolve_bundle(vault)[0]


def _bundle_notes(vault: str) -> list[str]:
    """Vault 부합(#114 U3) — 번들 존재 진단(vault repo엔 큐레이션 번들이 필요)."""
    bundle_path, rejected = _resolve_bundle(vault)
    lines = []
    if rejected is not None:
        lines.append(f"  ⚠ bundlePath 선언 `{rejected}`은 vault 밖 — 무시하고 {bundle_path} 사용")
    if (Path(vault) / bundle_path).is_dir():
        lines.append(
            f"  부합: 번들 {bundle_path} 있음(`okf validate {bundle_path} --strict`로 건강 확인)"
        )
    else:
        lines.append(f"  부합: ⚠ 번들 {bundle_path} 없음 — vault repo엔 큐레이션 번들이 필요")
    return lines


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
    # 로컬 경로 vault도 잔재 회계를 받는다(#266 U5) — 지금까지 URL 모드에만 있어서, 미반영
    # 산출물이 쌓여도 doctor가 한 줄도 말하지 않았다. 범위를 번들로 한정하지 않으면
    # 사용자의 실작업이 잔재로 보고된다.
    lines.extend(okf_remote.local_residue_notes(vault, pathspec=_bundle_rel(vault)))
    twin = okf_remote.dualization_note(pointer, vault)  # 로컬↔관리형 clone 이원화(U4-7)
    if twin:
        lines.append(twin)
    return lines


_SMOKE_TIMEOUT = 20.0


def _smoke_okf() -> tuple[bool, str]:
    """``bin/okf --help`` 스모크 — (성공 여부, 실패 사유). 성공이면 사유는 빈 문자열.

    호출 자체가 되는지만 본다(엔진 기능은 이 진단의 관심이 아니다).
    """
    okf = Path(__file__).resolve().parents[2] / "bin" / "okf"
    if not okf.is_file():
        return False, f"셔틀 없음({okf})"
    try:
        proc = subprocess.run(
            [str(okf), "--help"], capture_output=True, timeout=_SMOKE_TIMEOUT, check=False
        )
    except OSError as exc:
        return False, f"실행 불가({exc.__class__.__name__})"
    except subprocess.TimeoutExpired:
        return False, f"시간 초과({_SMOKE_TIMEOUT:g}초)"
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return False, f"rc={proc.returncode}" + (f" — {tail[-1]}" if tail else "")
    return True, ""


def _prereq_notes() -> list[str]:
    """실행 전제 진단 — 훅·커맨드가 딛고 서는 것이 실제로 있는가(#299).

    엔진 호출 실패는 호출부에서 ``None``으로 흡수된다(훅이 세션을 깨지 않기 위해서다).
    그 결과 uv 부재·venv 손상이 **"설정이 없다"와 똑같이 무출력**으로 보인다 — 여기서
    직접 확인하지 않으면 사용자에게 남는 진단 경로가 없다.
    """
    lines: list[str] = []
    uv = shutil.which("uv")
    if uv:
        lines.append(f"  uv: {uv}")
    else:
        lines.append("  uv: ⚠ PATH에 없음 — 엔진 호출(bin/okf)이 조용히 실패한다. uv를 설치하라")
    ok, why = _smoke_okf()
    if ok:
        lines.append("  bin/okf: 실행 확인(--help)")
    else:
        lines.append(f"  bin/okf: ⚠ 스모크 실패({why}) — 주입·그래프 조회가 무동작이 된다")
    python = shutil.which("python3") or shutil.which("python")
    if python:
        lines.append(f"  bin/okf-py 대상 인터프리터: {python}")
    else:
        lines.append("  bin/okf-py 대상 인터프리터: ⚠ python3를 찾지 못함 — 훅이 spawn되지 않는다")
    return lines


def run(project: str) -> str:
    sections: list[tuple[str, list[str]]] = [("위치", [f"  {project}"])]
    sections.append(("실행 전제", _prereq_notes()))
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
