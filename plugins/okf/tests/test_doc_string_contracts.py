"""문서↔스크립트 문자열 결합 게이트 (#280) — 계열을 막는다, 인스턴스가 아니라.

커맨드·스킬 문서가 스크립트 출력의 **한국어 문장을 인용해 분기**하도록 지시하는
곳이 있다. 이 결합은 **표현을 다듬는 일을 곧 기능 고장으로** 만들고, 고장이 조용하다
— 문서와 코드를 대조하는 검사가 없었기 때문이다. 실제로 한 번 깨진 적이 있다
(`"핸들러 미승인"` vs 생산자의 `"핸들러 로컬 미승인 — …"`, #274가 기계 필드로 전환).

게이트는 두 방향이고, 아래 ``CONTRACTS`` 한 표가 둘 다의 단일 원천이다.

**G1(부정·계열 차단)** — 문서에서 결합 꼴을 탐지해 **표에 등록된 것만** 허용한다.
새 결합을 넣으면 red다. 반대로 결합을 없앴는데 행이 남아도 red라 표는 **줄기만** 한다.

**G2(대조)** — 표의 각 행이 인용한 문자열이 생산자 소스에 실존하는지 본다.
**이 게이트 하나만 있었어도 위 드리프트는 첫날 red였다.**

탐지기 설계는 실측으로 정했다. 문서 전체에서 "한글이 든 인용 리터럴"을 소박하게
그렙하면 46건이 나오는데 그중 40건이 오탐이다 — 셸 명령(``okf census <번들경로>``),
LAYERS.md의 개념 서술 예시(``~이면 ~하라``), 사용자 발화 인용(``적재/승격해줘``).
그런 표를 만들면 진짜 6건이 오탐 40건에 묻혀 "줄어드는 것만 허용"이 무의미해진다.
그래서 **기계 필드의 값 위치**(``reason: "…"``)로 한정한다 — 결합의 실제 꼴이 그것이고,
오탐 40건은 어느 것도 필드 값 자리에 있지 않다. 실측 정밀도 100%(6/6).

**게이트가 못 하는 것(정직하게)**: 소프트 참조(``stderr에 "미승인" 안내가 보이면``)와
런타임 합성 문자열(``[회복]`` = ``f"[{title}]"``)은 필드 값 꼴이 아니라 G1이 못 잡는다.
그런 곳은 ``auto=False``로 손등록해 G2만 건다 — 드리프트는 잡히되 신규 유입은 못 막는다.
그리고 문서에 분기가 **존재한다**는 것만 잠근다. 모델이 실제로 그 분기를 타는지는
못 잠근다(Epic #266 §7이 같은 한계를 기록).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parents[1]
SCRIPTS = PLUGIN / "scripts"

# repo ``docs/``·README도 **소비처가 실제로 읽는 문서**다(#304). 여기가 범위 밖이면
# 게이트가 지키는 것은 "우리가 보는 문서"뿐이고, 소비처가 믿는 문서는 그대로 드리프트한다.
# 편입 비용은 0이었다 — 편입 시점에 `field: "값"` 결합이 0건이라 등록할 행이 없었다.
DOC_ROOTS = (PLUGIN / "commands", PLUGIN / "skills", REPO / "docs")
DOC_EXTRA = (REPO / "README.md",)

# 판정 입력이 되는 기계 필드 — 문서가 이 필드의 **값**을 인용하면 결합이다.
_FIELDS = ("reason", "note", "status", "code", "state", "kind", "error", "message", "detail")
_QUOTED = r'(?:`[^`\n]*`|"[^"\n]*")'
_FIELD_ALT = "|".join(_FIELDS)

# 결합은 두 꼴로 쓰인다. 둘 다 봐야 한다 — 실제로 깨졌던 것은 두 번째다.
#
# ① 필드 꼴 — ``reason: "미봉인 잔재"``. 값 하나 또는 `|`로 이어진 대안 사슬.
# ② 조사 꼴 — ``결과 note에 "핸들러 미승인"가 오면``. 필드명에 한국어 조사가 붙고
#    같은 줄 근처에 리터럴이 온다. #274 이전의 그 버그가 정확히 이 모양이었으므로
#    ①만 보면 **원본 사고를 재현해도 초록**이다(이 파일의 red 실증이 그것을 확인).
#    창(40자)은 같은 줄 안에서만 이어 붙여 다른 문장의 인용을 물지 않게 한다.
_COUPLING = re.compile(
    rf"\b(?:{_FIELD_ALT})\s*[:=]\s*({_QUOTED}(?:\s*\|\s*{_QUOTED})*)"
    rf"|\b(?:{_FIELD_ALT})(?:[이가은는을를에]|에서|에는)[^`\"\n]{{0,40}}?({_QUOTED})"
)
_LITERAL = re.compile(_QUOTED)
_HANGUL = re.compile(r"[가-힣]")


# 결합 대장 — (문서, 문서가 인용한 리터럴, 생산자 소스, 소스에 실존해야 할 바늘, 자동탐지 여부).
#
# `needle`이 인용 리터럴과 다를 수 있는 이유: 런타임에 **합성**되는 출력이 있다.
# `[회복]`은 소스 어디에도 그 바이트로 없고 `f"[{title}]"` + `("회복", …)`로 만들어진다.
# 그래서 바늘은 **소스에 실제로 있는 조각**을 가리킨다 — 그 조각이 바뀌면 red다.
#
# 행을 추가하는 것이 곧 결합의 비용이다. 각 행은 전환 예정이거나 영구 허용이다.
CONTRACTS = (
    # --- 전환 예정: vault 사유(okf_vault.INVALID_*)는 기계 code를 얻으면 뺀다 ------
    ("commands/okf-init.md", "URL 포인터 — 미지원 transport", "vault/okf_vault.py", None, True),
    ("commands/okf-init.md", ".okf-wiki.json 없음", "vault/okf_vault.py", None, True),
    ("commands/okf-init.md", "대상 없음", "vault/okf_vault.py", None, True),
    ("commands/okf-init.md", "git repo 아님", "vault/okf_vault.py", None, True),
    # (remote refresh 사유는 #297이 `code` enum으로 전환해 여기서 빠졌다 —
    #  `test_okf_remote.py::test_study_md_branches_on_every_refresh_code`가 대신 잠근다)
    # --- 영구 허용: doctor는 **출력 텍스트 자체가 산출물**이라 문구 계약이 정당하다.
    #     커맨드 문서가 "자체 해석을 덧붙이지 말 것"으로 못박은 설계와 정합한다(#280 범위 제외).
    ("commands/okf-doctor.md", "[회복]", "doctor/study_doctor.py", '("회복"', False),
    # --- 영구 허용: 가시적 저하 안내의 소프트 참조. 분기가 아니라 "보이면 도와줘라"다.
    ("commands/okf-promote.md", "미승인", "explore/okf_explore.py", "미승인", False),
)


def _doc_files() -> list[Path]:
    found = [p for root in DOC_ROOTS if root.is_dir() for p in root.rglob("*.md")]
    found += [p for p in DOC_EXTRA if p.is_file()]
    return sorted(found)


def _doc_rel(path: Path) -> str:
    """CONTRACTS 표기와 같은 상대 경로 — 플러그인 문서는 PLUGIN, repo 문서는 REPO 기준."""
    try:
        return path.relative_to(PLUGIN).as_posix()
    except ValueError:
        return path.relative_to(REPO).as_posix()


def _doc_path(rel: str) -> Path:
    """``_doc_rel``의 역 — 플러그인 쪽에 없으면 repo 쪽에서 찾는다."""
    candidate = PLUGIN / rel
    return candidate if candidate.is_file() else REPO / rel


def _detect() -> set[tuple[str, str]]:
    """문서에서 (문서상대경로, 한글 인용 리터럴) 결합 꼴을 뽑는다.

    영문 기계 토큰(``diverged`` · ``offline env``)은 자연어가 아니라 코드성
    식별자라 표현을 다듬을 유인이 0이다 — 오히려 나머지 값들의 **목표 형태**이므로
    결합으로 세지 않는다.
    """
    found: set[tuple[str, str]] = set()
    for path in _doc_files():
        rel = _doc_rel(path)
        for match in _COUPLING.finditer(path.read_text(encoding="utf-8")):
            # 두 꼴 중 실제로 매치된 그룹만 산다(①=1, ②=2).
            for raw in _LITERAL.findall(match.group(1) or match.group(2)):
                literal = raw[1:-1]
                if _HANGUL.search(literal):
                    found.add((rel, literal))
    return found


def _registered(auto_only: bool) -> set[tuple[str, str]]:
    return {(doc, lit) for doc, lit, _src, _needle, auto in CONTRACTS if auto or not auto_only}


# --- G2 대조: 인용한 것이 생산자에 실존하는가 --------------------------------


def _string_literals(path: Path) -> set[str]:
    """소스의 **완결된** 문자열 리터럴 집합(AST 기준)."""
    return {
        node.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


@pytest.mark.parametrize(
    ("doc", "literal", "source", "needle", "auto"),
    [(d, lit, src, needle or lit, auto) for d, lit, src, needle, auto in CONTRACTS],
    ids=[f"{Path(d).stem}:{lit[:16]}" for d, lit, *_ in CONTRACTS],
)
def test_quoted_literal_exists_in_producer(doc, literal, source, needle, auto):
    """문서가 인용한 문자열이 생산자 소스에 실존한다 — 표현을 다듬으면 여기서 걸린다.

    ``auto=True``(필드 값 꼴)는 **리터럴 정확 일치**를 요구한다. 부분일치면 접미 확장이
    새 나간다 — 실측: ``okf_remote``의 ``"fetch 실패"``를 바꿔도 같은 파일의
    ``"fetch 실패(오프라인/인증)"``가 바늘을 만족해 전 스위트가 초록이었다.
    ``auto=False``(런타임 합성·소프트 참조)는 완결된 리터럴이 아니므로 부분일치로 남긴다.
    """
    src = SCRIPTS / source
    assert src.is_file(), f"생산자 소스 없음: {source}"
    if auto:
        assert needle in _string_literals(src), (
            f"{doc}이 인용한 {literal!r}가 {source}의 **완결된 리터럴**로 없다 — "
            "생산자가 표현을 바꿨거나(접미가 붙었거나) 문서가 틀렸다. "
            "문서를 고치거나 기계 필드로 전환하라."
        )
        return
    assert needle in src.read_text(encoding="utf-8"), (
        f"{doc}이 인용한 {literal!r}의 생산자 문자열({needle!r})이 {source}에 없다 — "
        "생산자가 표현을 바꿨거나 문서가 틀렸다. 문서를 고치거나 기계 필드로 전환하라."
    )


def test_quoted_literal_still_present_in_doc():
    """표의 각 행이 가리키는 문서 인용이 아직 그 문서에 있다 — 표가 유령을 안 남기게."""
    for doc, literal, _src, _needle, _auto in CONTRACTS:
        path = _doc_path(doc)
        assert path.is_file(), f"문서 없음: {doc}"
        assert literal in path.read_text(encoding="utf-8"), (
            f"{doc}에 {literal!r} 인용이 없다 — 결합이 사라졌으면 CONTRACTS에서 행을 빼라."
        )


# --- G1 부정: 계열 자체를 막는다 ----------------------------------------------


def test_no_unregistered_coupling():
    """새 결합은 red — 기계 필드 값 자리에 한글 리터럴을 인용하면 표에 등록해야 한다."""
    unregistered = _detect() - _registered(auto_only=False)
    assert not unregistered, (
        "등록되지 않은 문서↔스크립트 문자열 결합: "
        + ", ".join(f"{doc}  <<{lit}>>" for doc, lit in sorted(unregistered))
        + " — 기계 필드(code 등)로 분기하도록 고치거나, 불가피하면 CONTRACTS에 행을 추가하라."
    )


def test_registered_coupling_only_shrinks():
    """탐지되던 결합이 사라지면 행도 빠져야 한다 — 표가 줄기만 하게 강제한다.

    ``auto=False`` 행(소프트 참조·합성 문자열)은 탐지기가 원래 못 보므로 제외한다.
    """
    stale = _registered(auto_only=True) - _detect()
    assert not stale, (
        "문서에서 사라진 결합이 CONTRACTS에 남아 있다: "
        + ", ".join(f"{doc}  <<{lit}>>" for doc, lit in sorted(stale))
        + " — 전환이 끝났으면 행을 삭제하라(허용 목록은 줄기만 한다)."
    )


# --- 죽은 스크립트 참조 차단 (#304) -------------------------------------------
#
# 문서가 **실행되지 않는** 스크립트를 진단·설정의 주체로 서술하면, 읽는 사람의 조사가
# 죽은 코드로 향한다. 실측: `skills/okf/reference/CONFIG.md`가 프로젝트 설정 소비 주체로
# `session_start.sh`를 지목하는데 `hooks.json`이 부르는 것은 `okf_hooks.py session-start`
# 다. 파일은 실재하므로 "존재하는가"만 보는 검사로는 안 잡힌다 — **배선되어 있는가**를
# 봐야 한다.

HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
COMMANDS = PLUGIN / "commands"


def _script_basenames() -> dict[str, Path]:
    return {p.name: p for p in SCRIPTS.rglob("*") if p.suffix in (".py", ".sh") and p.is_file()}


def _wired_names() -> set[str]:
    """실행 경로에 **배선된** 스크립트 이름 — 훅 · 커맨드가 부르거나 다른 스크립트가 import.

    "다른 파일에 이름이 보이면 배선"으로 하지 않는다 — 주석·docstring의 **언급**이
    배선으로 세어져 죽은 스크립트가 살아 있는 것처럼 통과한다. 실제로 `okf_hooks.py`의
    모듈 docstring이 대체된 셸 3종을 이름으로 열거하고 있다. 그래서 판별을 좁힌다.

    - 훅·커맨드: 실행 표면이므로 **파일명 등장**이 곧 호출이다.
    - 다른 스크립트: ``import <stem>``/``from <stem> import`` 꼴만 센다(언급 제외).

    셸 스크립트는 import 개념이 없으므로 훅·커맨드 표면에서만 배선된다.
    """
    scripts = _script_basenames()
    surfaces = [HOOKS_JSON.read_text(encoding="utf-8")] if HOOKS_JSON.is_file() else []
    surfaces += [p.read_text(encoding="utf-8") for p in COMMANDS.rglob("*.md")]
    sources = {name: path.read_text(encoding="utf-8") for name, path in scripts.items()}

    wired: set[str] = set()
    for name, path in scripts.items():
        if any(name in text for text in surfaces):
            wired.add(name)
            continue
        if path.suffix != ".py":
            continue
        imported = re.compile(
            rf"^\s*(?:import\s+{path.stem}\b|from\s+{path.stem}\s+import\b)", re.M
        )
        if any(imported.search(text) for other, text in sources.items() if other != name):
            wired.add(name)
    return wired


def test_docs_do_not_point_at_unwired_scripts():
    """문서가 지목하는 스크립트는 **배선된** 것이어야 한다 — 진단이 죽은 코드로 가지 않게.

    참조는 백틱 표기만 본다(산문에 우연히 걸리는 것을 막는 다른 게이트와 같은 판별).
    실재하지 않는 이름은 여기서 보지 않는다 — 아래 별도 축이 잡는다.
    """
    scripts = _script_basenames()
    wired = _wired_names()
    offenders: list[str] = []
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"`([\w.-]+\.(?:py|sh))`", text):
            if name in scripts and name not in wired:
                offenders.append(f"{_doc_rel(path)} → {name}")
    assert not offenders, (
        "문서가 배선되지 않은 스크립트를 지목한다: "
        + ", ".join(sorted(set(offenders)))
        + " — 실제 실행 주체로 문서를 고치거나, 그 스크립트를 배선하라."
    )


# 우리 트리에 **없는 것이 정상**인 이름 — 소비처가 자기 repo에 두는 것이다(목적지 무참조).
# 이 목록을 늘리는 것이 곧 게이트를 줄이는 것이라 사유와 함께 여기 명시적으로 둔다.
_CONSUMER_SIDE_SCRIPTS = frozenset(
    {
        # 참조 핸들러의 소비처 배치명. 우리는 `docs/examples/*.example`만 배포한다.
        "okf-open-pr.py",
    }
)


def _repo_script_names() -> set[str]:
    return {
        p.name
        for p in REPO.rglob("*")
        if p.suffix in (".py", ".sh")
        and p.is_file()
        # 점디렉토리(.git·.venv·캐시)는 산출물이라 소스 이름의 근거가 아니다
        and not any(part.startswith(".") for part in p.relative_to(REPO).parts[:-1])
    }


def test_docs_reference_only_existing_scripts():
    """문서가 백틱으로 지목한 ``*.py``/``*.sh``가 repo에 실존한다 — 오타·삭제 잔존 차단."""
    known = _repo_script_names() | _CONSUMER_SIDE_SCRIPTS
    missing: list[str] = []
    for path in _doc_files():
        for name in re.findall(r"`([\w.-]+\.(?:py|sh))`", path.read_text(encoding="utf-8")):
            if name not in known:
                missing.append(f"{_doc_rel(path)} → {name}")
    assert not missing, (
        "문서가 없는 스크립트를 지목한다: "
        + ", ".join(sorted(set(missing)))
        + " — 이름을 고치거나, 소비처 배치명이면 _CONSUMER_SIDE_SCRIPTS에 사유와 함께 등록하라."
    )
