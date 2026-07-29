"""훅 하한 인터프리터 컴파일 대상 도출 (#299).

CI의 `훅 py3.10 하한 게이트`는 소비처의 파이썬이 몇인지 모른다는 전제로 컴파일만
확인한다. 그런데 대상이 `core/` 3파일로 **손으로 적혀** 있었다 — 그래서 배선된
study 훅 2종(`study_hook.py`·`study_session.py`)과 그 import 폐포가 검사 밖이었다.
훅이 늘거나 import가 늘면 목록은 그대로 남고 게이트만 조용히 좁아진다.

여기서는 `hooks.json`이 실제로 배선한 스크립트에서 시작해 **import 전이 폐포**를
계산한다. 목록이 배선을 따라오므로 손질할 것이 없다.

stdlib 전용. 표준출력에 파일 경로를 한 줄씩 낸다(빈 결과는 exit 2 — 도출 실패를
"검사할 것 없음"으로 보고하지 않는다).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "okf"
SCRIPTS = PLUGIN / "scripts"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"


def _py_files() -> dict[str, Path]:
    """모듈 stem → 경로. 훅은 패키지가 아니라 flat 모듈로 import된다(셔틀 PYTHONPATH)."""
    return {p.stem: p for p in SCRIPTS.rglob("*.py") if p.is_file()}


def wired_entry_points() -> list[str]:
    """`hooks.json`이 배선한 `.py` 스크립트의 stem 목록."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    stems: list[str] = []
    for groups in data["hooks"].values():
        for entry in groups:
            for spec in entry["hooks"]:
                for token in [spec.get("command", ""), *spec.get("args", [])]:
                    if token.endswith(".py"):
                        stems.append(token.rsplit("/", 1)[-1][: -len(".py")])
    return sorted(set(stems))


def closure(stems: list[str]) -> list[Path]:
    """진입점 stem에서 import로 닿는 로컬 모듈 전이 폐포(경로, 정렬)."""
    modules = _py_files()
    seen: set[str] = set()
    frontier = [s for s in stems if s in modules]
    while frontier:
        stem = frontier.pop()
        if stem in seen:
            continue
        seen.add(stem)
        for node in ast.walk(ast.parse(modules[stem].read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                targets = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module.split(".")[0]]
            else:
                continue
            frontier += [t for t in targets if t in modules and t not in seen]
    return sorted(modules[s] for s in seen)


def main() -> int:
    targets = closure(wired_entry_points())
    if not targets:
        print(
            "hook_compile_targets: 배선 스크립트를 하나도 도출하지 못했습니다 — "
            "hooks.json 서식이 바뀌었는지 확인하세요",
            file=sys.stderr,
        )
        return 2
    for path in targets:
        print(path.relative_to(REPO).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
