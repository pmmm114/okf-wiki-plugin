"""core⊥study 경계 게이트 (U2, #145).

plugin-core(``okf_*``) 스크립트는 study feature(``study_*``)를 import하지 않는다 —
엔진 무참조 grep 게이트(CLAUDE.md)와 동형인 feature 경계 계약이다. 접두사가
관례일 뿐 계약이 아니어서 study가 core로 샜던 유착(#145: okf_inbox 실체 불일치·
okf_home 융합·doctor 하드 import)을 U1·U3·U4가 해소했고, 이 게이트가 그 완료를
회귀 계약으로 고정한다 — 다음 주입 기능도 강제로 독립 모듈로 남는다.

유일한 예외는 doctor의 선택 위임 심(try-import ``study_doctor``, #145 U4 —
"있으면 실행, 없으면 생략"). 아래 ALLOWED_SEAMS에 데이터로 명시하고 **정확
일치**를 단언한다 — 심이 늘거나(새 유착) 사라지면(선언 부패) 둘 다 red다.

게이트 범위는 import 계층(정적 + 동적 상수)뿐이다 — 전면 텍스트 grep은 doctor
안내문·설정 키 "study"의 정당한 언급을 오탐한다(판정 상수 게이트가 리터럴 범위를
좁힌 것과 같은 정신). ast.walk라 함수 내부 지연 import까지 포착한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# 선언된 위임 심 — (core 파일명, 허용 study 모듈). 이 집합 외는 전부 금지.
ALLOWED_SEAMS = {("okf_doctor.py", "study_doctor")}


def _core_files() -> list[Path]:
    files = sorted(SCRIPTS.glob("okf_*.py"))
    assert files, "okf_* 스크립트 미발견 — 게이트 대상 공집합(경로 확인)"
    return files


def _is_study(name: str) -> bool:
    top = name.split(".")[0]
    return top == "study" or top.startswith("study_")


def _study_imports(path: Path) -> set[str]:
    """정적 import(함수 내부 지연 import 포함)에서 study 접두 최상위 모듈명 수집."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return {name for name in names if _is_study(name)}


def test_core_scripts_do_not_import_study():
    violations: list[str] = []
    seams_found: set[tuple[str, str]] = set()
    for path in _core_files():
        for module in sorted(_study_imports(path)):
            if (path.name, module) in ALLOWED_SEAMS:
                seams_found.add((path.name, module))
            else:
                violations.append(f"{path.name} → import {module}")
    assert not violations, f"core⊥study 경계 위반: {violations}"
    # allowlist 정확 일치 — 심 추가(새 유착)도, 선언만 남은 유령 심도 허용하지 않는다
    assert seams_found == ALLOWED_SEAMS, (
        f"위임 심 선언·실제 불일치 — 선언 {sorted(ALLOWED_SEAMS)}, 실제 {sorted(seams_found)}"
    )


def test_core_scripts_do_not_dynamic_import_study():
    # __import__/import_module의 문자열 상수 인자까지 차단(정적 게이트 우회 방지)
    violations: list[str] = []
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name not in ("__import__", "import_module"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if _is_study(arg.value):
                        violations.append(f"{path.name} → 동적 import {arg.value!r}")
    assert not violations, f"core⊥study 경계 위반(동적): {violations}"
