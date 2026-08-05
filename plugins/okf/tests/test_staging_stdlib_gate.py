"""스테이징 모듈 무의존 게이트 (U6, #135).

스터디 스테이징(store·simhash·blocks·legacy)은 **stdlib + 로컬 모듈만** import한다 —
numpy/scipy 등 서드파티가 새어들면 `--no-project` 플러그인 테스트·오프라인 단독 배달이
깨진다(그래서 MinHash/datasketch 대신 stdlib SimHash를 골랐다). 회귀를 AST로 고정한다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "capture"
STAGING = ["study_store.py", "study_overlap.py", "study_blocks.py", "study_legacy.py"]
FORBIDDEN = {"numpy", "scipy", "pandas", "requests", "datasketch", "simhash"}
LOCAL = {
    "okf_vault",
    "study_inbox",
    "study_store",
    "study_overlap",
    "study_blocks",
    "study_legacy",
    "study_dispatch",
    "study_trust",
    "__future__",
}


def _top_level_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_staging_modules_import_only_stdlib_and_local():
    stdlib = set(sys.stdlib_module_names)
    for name in STAGING:
        imported = _top_level_imports(SCRIPTS / name)
        assert not (imported & FORBIDDEN), f"{name}: 금지 의존 {imported & FORBIDDEN}"
        extra = imported - stdlib - LOCAL
        assert not extra, f"{name}: stdlib·로컬 아닌 import {extra}"


# 무의존 계약의 나머지 절반 — **인터프리터 하한**. `bin/okf-py`는 pyproject가 아니라
# 시스템에서 python3를 찾아 exec하고(#108), 훅 spawn은 로그인 쉘 PATH를 보장하지 않아
# `/usr/bin/python3`(구 버전)로 떨어질 수 있다. 루트 `requires-python`은 엔진·pip 소비용
# 셔틀의 계약이지 이 경로를 덮지 않으므로, 스테이징 모듈에는 신 버전 전용 API를 쓰지 않는다.
NEWER_ONLY_ATTRS = {"bit_count"}  # int.bit_count()는 3.10+


def _attribute_names(path: Path) -> set[str]:
    return {
        node.attr
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
    }


def test_staging_modules_avoid_newer_only_builtin_apis():
    for name in STAGING:
        used = _attribute_names(SCRIPTS / name) & NEWER_ONLY_ATTRS
        assert not used, f"{name}: 인터프리터 하한 위반 API {used} — bin/okf-py 경로에서 죽는다"
