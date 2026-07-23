"""스테이징 모듈 무의존 게이트 (U6, #135).

스터디 스테이징(store·simhash·blocks·legacy)은 **stdlib + 로컬 모듈만** import한다 —
numpy/scipy 등 서드파티가 새어들면 `--no-project` 플러그인 테스트·오프라인 단독 배달이
깨진다(그래서 MinHash/datasketch 대신 stdlib SimHash를 골랐다). 회귀를 AST로 고정한다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "study"
STAGING = ["study_store.py", "study_simhash.py", "study_blocks.py", "study_legacy.py"]
FORBIDDEN = {"numpy", "scipy", "pandas", "requests", "datasketch", "simhash"}
LOCAL = {
    "okf_home",
    "study_inbox",
    "study_store",
    "study_simhash",
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
