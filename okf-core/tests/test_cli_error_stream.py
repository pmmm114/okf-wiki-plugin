"""CLI 오류는 stderr로 (#300).

엔진의 오류 출력 스트림이 모듈마다 갈려 있었다 — `graph`·`context`·`index`·`logmd`·
`init`은 오류문을 **stdout**으로 내고 `validate`·`census`·`cli`는 stderr로 냈다.

소비자가 stdout을 산출물로 읽기 때문에 이것이 조용한 오염이 된다. 실측: PostToolUse
훅이 `okf graph --linked-to`의 stdout을 "링크하는 개념 목록"으로 컨텍스트에 주입하는데,
번들 경로가 틀리면 `오류: 번들 디렉터리가 아님: …`이라는 **문장이 그 자리에 들어간다**.
exit code로 걸러도(#300에서 그렇게 고쳤다) 스트림 자체가 갈려 있으면 다음 소비자가
같은 함정에 빠진다.

AST로 잠근다 — "오류:"로 시작하는 `print`는 `file=sys.stderr`를 달아야 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "okf_core"
MODULES = sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")
ERROR_PREFIX = "오류"


def _first_arg_text(node: ast.Call) -> str | None:
    """``print(...)`` 첫 인자의 **선두 리터럴** 텍스트(f-string 포함). 없으면 None."""
    if not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        for value in arg.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            return None  # 선두가 보간이면 판별 불가 — 대상 아님
    return None


def _goes_to_stderr(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg != "file":
            continue
        target = kw.value
        return isinstance(target, ast.Attribute) and target.attr == "stderr"
    return False


@pytest.mark.parametrize("module", MODULES, ids=[p.stem for p in MODULES])
def test_error_prints_go_to_stderr(module: Path):
    """`오류:`로 시작하는 print는 전부 stderr로 나간다."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        and (_first_arg_text(node) or "").startswith(ERROR_PREFIX)
        and not _goes_to_stderr(node)
    ]
    assert not offenders, (
        f"{module.name}: stdout으로 나가는 오류 출력 L{offenders} — "
        "소비자가 stdout을 산출물로 읽으므로 오류문이 결과로 오염된다. "
        "`file=sys.stderr`를 붙여라."
    )


def test_gate_sees_something():
    """게이트가 실제로 대상을 훑는다 — 모듈 목록이 비면 위 테스트가 공회전한다."""
    assert len(MODULES) >= 8, [p.name for p in MODULES]
