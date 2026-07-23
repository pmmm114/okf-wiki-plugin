"""core⊥study 경계 게이트 (U2, #145).

plugin-core(``okf_*``) 스크립트는 study feature(``study_*``)를 import하지 않는다 —
엔진 무참조 grep 게이트(CLAUDE.md)와 동형인 feature 경계 계약이다. 접두사가
관례일 뿐 계약이 아니어서 study가 core로 샜던 유착(#145: okf_inbox 실체 불일치·
okf_vault 융합·doctor 하드 import)을 U1·U3·U4가 해소했고, 이 게이트가 그 완료를
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

# #145 U5 물리 분리 — 경계 정의가 접두사(okf_*)에서 디렉토리(scripts/core/)로
# 승격됐다. core 디렉토리의 모든 파이썬 파일(하위 패키지 포함 — rglob)이 대상이고,
# 금지 집합도 scripts/study/ 실파일에서 도출한다(접두사 관례가 깨져도 경계 유지).
SCRIPTS_CORE = Path(__file__).resolve().parent.parent / "scripts" / "core"
SCRIPTS_STUDY = Path(__file__).resolve().parent.parent / "scripts" / "study"

# 선언된 위임 심 — (core 파일명, 허용 study 모듈). 이 집합 외는 전부 금지.
ALLOWED_SEAMS = {("okf_doctor.py", "study_doctor")}


def _core_files() -> list[Path]:
    # rglob — core/가 하위 패키지로 정리돼도 게이트가 실명하지 않는다(셔틀
    # PYTHONPATH가 scripts/core를 노출하므로 하위 패키지도 런타임 도달 가능)
    files = sorted(SCRIPTS_CORE.rglob("*.py"))
    assert files, "scripts/core/ 파이썬 파일 미발견 — 게이트 대상 공집합(경로 확인)"
    return files


def _study_modules() -> set[str]:
    """금지 모듈명 집합 — scripts/study/ 실파일(stem)에서 도출(디렉토리가 정본)."""
    stems = {p.stem for p in SCRIPTS_STUDY.rglob("*.py")}
    assert stems, "scripts/study/ 파이썬 파일 미발견 — 금지 집합 공집합(경로 확인)"
    return stems


def _is_study(name: str, study_modules: set[str]) -> bool:
    # 접두사 관례(study/study_*)는 벨트-앤-서스펜더로 유지 — 미존재 study_* 모듈
    # 참조도 잡고, 디렉토리 도출 집합이 관례 밖 파일명(예: formatter.py)을 커버한다.
    top = name.split(".")[0]
    return top == "study" or top.startswith("study_") or top in study_modules


def _study_imports(path: Path, study_modules: set[str]) -> set[str]:
    """정적 import(함수 내부 지연 import 포함)에서 금지 모듈명 수집."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            elif node.level:
                # `from . import study_x` — 상대 import는 alias가 곧 모듈명이다
                names.update(alias.name.split(".")[0] for alias in node.names)
    return {name for name in names if _is_study(name, study_modules)}


def test_core_scripts_do_not_import_study():
    study_modules = _study_modules()
    violations: list[str] = []
    seams_found: set[tuple[str, str]] = set()
    for path in _core_files():
        for module in sorted(_study_imports(path, study_modules)):
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
    study_modules = _study_modules()
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
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if _is_study(arg.value, study_modules):
                        violations.append(f"{path.name} → 동적 import {arg.value!r}")
    assert not violations, f"core⊥study 경계 위반(동적): {violations}"
