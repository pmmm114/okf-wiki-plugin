"""README 「동작 방식」 ↔ 엔진 정합 게이트 — 다이어그램을 코드에서 유도해 대조한다.

이 절은 #331 착지 후 한 번 썩었다 — 엔진에 census·query가 들어왔는데 다이어그램은
validate·index·graph·context 4개만 그린 채 남았고, 대조하는 검사가 없어 조용했다.
같은 드리프트가 재발하지 않게 두 축을 잠근다.

- **완전성**: `ParsedDoc`을 소비하는 CLI 서브커맨드 모듈(코드에서 유도 —
  ``walk_bundle(`` 호출 ∩ ``cli._COMMANDS`` 등록)이 전부 다이어그램 노드로 있다.
  소비자가 늘었는데 README가 안 늘면 red다.
- **역할 어휘**: 노드의 역할 표기(``<이름><br/><역할> — …``)가 그 모듈 docstring
  첫 줄의 역할(``<이름> — <역할>: …``, #335가 고정한 정본)과 일치한다.

산문 문장은 잠그지 않는다 — 표현을 다듬는 일이 red가 되면 게이트가 문서를 얼리는
것이고, 기계 대조가 가능한 것은 위 두 축뿐이다. stdlib 전용(scripts 게이트 규율).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "okf-core" / "src" / "okf_core"
_README = _ROOT / "README.md"

# mermaid 노드 라벨 `validate<br/>판정 — …`에서 (모듈, 역할)을 뽑는다.
_NODE = re.compile(r'(\w+)<br/>([^\s"<]+) — ')
# 모듈 docstring 첫 줄 `"""<이름> — <역할>: …`(#335 형식)에서 역할을 뽑는다.
_DOCSTRING_ROLE = re.compile(r'^"""(\w+) — ([^:]+):')
# cli._COMMANDS의 `"<서브커맨드>": <모듈>.main` 등록.
_COMMAND = re.compile(r'"(\w+)": \w+\.main')


def _behavior_section() -> str:
    text = _README.read_text(encoding="utf-8")
    start = text.index("\n## 동작 방식\n")
    return text[start : text.index("\n## ", start + 1)]


def _diagram_modules() -> dict[str, str]:
    """다이어그램이 역할 표기로 그린 {모듈: 역할}."""
    return dict(_NODE.findall(_behavior_section()))


def _parsed_consuming_commands() -> set[str]:
    """`ParsedDoc` 소비 CLI 모듈 — walk_bundle 호출 ∩ _COMMANDS 등록(코드에서 유도)."""
    commands = set(_COMMAND.findall((_SRC / "cli.py").read_text(encoding="utf-8")))
    assert commands, "cli._COMMANDS 파싱 실패 — 게이트 감도 상실"
    return {
        p.stem
        for p in _SRC.glob("*.py")
        if p.stem in commands and "walk_bundle(" in p.read_text(encoding="utf-8")
    }


def _module_role(stem: str) -> str:
    first = (_SRC / f"{stem}.py").read_text(encoding="utf-8").split("\n", 1)[0]
    m = _DOCSTRING_ROLE.match(first)
    assert m and m.group(1) == stem, f"{stem}.py docstring 첫 줄이 역할 형식이 아님: {first}"
    return m.group(2)


def test_diagram_covers_all_parsed_consumers():
    """엔진의 ParsedDoc 소비 서브커맨드가 전부 다이어그램에 있다 — #331류 드리프트 차단."""
    consumers = _parsed_consuming_commands()
    # 감도 앵커: 실제로 썩었던 두 모듈이 유도 집합에 들어 있어야 유도 자체가 살아 있다.
    assert {"census", "query"} <= consumers, f"소비자 유도가 무뎌졌다: {sorted(consumers)}"
    drawn = set(_diagram_modules())
    missing = consumers - drawn
    assert not missing, (
        f"ParsedDoc 소비자가 README 동작 방식 다이어그램에 없다: {sorted(missing)} — "
        "다이어그램에 `<모듈><br/><역할> — <한 줄>` 노드를 추가하라."
    )


def test_diagram_roles_match_module_docstrings():
    """노드의 역할 표기가 모듈 docstring 첫 줄(#335 정본)과 일치한다 — 어휘 이원화 차단."""
    drawn = _diagram_modules()
    assert drawn, "다이어그램에서 역할 표기 노드를 하나도 못 읽었다 — 게이트 감도 상실"
    mismatch = {
        name: (role, _module_role(name))
        for name, role in drawn.items()
        if role != _module_role(name)
    }
    assert not mismatch, (
        "README 다이어그램의 역할 표기가 docstring 정본과 다르다 "
        + ", ".join(f"{n}: 문서 {d!r} ≠ 정본 {s!r}" for n, (d, s) in sorted(mismatch.items()))
        + " — README를 고치거나, 역할이 진짜 바뀌었으면 docstring·CLAUDE.md 표와 함께 바꿔라."
    )


def test_detectors_catch_drift():
    """탐지기 자기검증 — 라벨·docstring·등록 3개 정규식이 실물 꼴을 실제로 잡는다."""
    assert _NODE.findall('P --> Q["query<br/>재료 — 인메모리 sqlite"]') == [("query", "재료")]
    m = _DOCSTRING_ROLE.match('"""query — 재료: 번들을 SQLite로 짓는다.')
    assert m and m.group(2) == "재료"
    assert _COMMAND.findall('    "query": query.main,') == ["query"]
