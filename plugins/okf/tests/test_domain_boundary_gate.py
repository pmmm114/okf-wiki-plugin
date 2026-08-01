"""도메인 경계 게이트 — 교차 도메인 import는 선언된 방향으로만.

scripts/는 도메인 디렉토리(hooks 진입점·vault 저장고·capture 캡처·promote 승격·
explore 탐색·doctor 진단)로 물리 분리돼 있고, 모듈명은 flat이라(bin/okf-py 셔틀
PYTHONPATH) **디렉토리가 곧 경계 선언**이다. 이 게이트는 그 선언을 강제한다.

- 교차 도메인 import(정적 + 동적 문자열 상수)는 ALLOWED_EDGES 방향만 — 새 유착은 red.
- 선언됐는데 실사용이 없는 방향(유령 엣지)도 red — 허용 목록은 실사용만 남는다.
- ``okf_promote``는 캡처 런타임·훅 무-import(구 core⊥study 불변식의 파일 단위 승계)
  — 집행 게이트가 캡처 런타임에 유착하면 판정/집행 분리가 흐려진다.
- hooks.json이 부르는 진입점은 scripts/hooks/에만 — 배선 표면과 디렉토리가 일치해야
  진단·문서가 실행 주체를 놓치지 않는다(#304 죽은 참조 게이트와 같은 정신).
- 도메인 집합 자체를 잠근다 — 새 도메인은 bin/okf-py·conftest 명시 배선과 함께만
  늘어난다(조용한 디렉토리 증식이 PYTHONPATH 밖 죽은 코드를 만들지 않게).

구 게이트(test_core_study_boundary_gate, #145)의 승계다: core⊥study 2분할이 도메인
6분할 DAG로 세분화됐고, doctor→study_doctor 위임 심(구 ALLOWED_SEAMS)은 doctor/
도메인 내부가 되어 예외 선언이 필요 없어졌다. 게이트 범위가 import 계층뿐인 이유,
ast.walk로 지연 import까지 보는 이유는 구 게이트와 같다 — 전면 텍스트 grep은 doctor
안내문·설정 키 "study"의 정당한 언급을 오탐한다.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

# 도메인 어휘 — CLAUDE.md "플러그인 스크립트 도메인" 표와 같은 집합.
DOMAINS = ("hooks", "vault", "capture", "promote", "explore", "doctor")

# 허용 교차 도메인 방향 — vault가 바닥인 DAG. 방향마다 사유를 명시한다.
ALLOWED_EDGES = {
    ("hooks", "capture"),  # 캡처 훅이 스코프·블록·인박스를 쓴다
    ("hooks", "vault"),  # 주입 훅이 포인터 해소·URL 신선도(fetch)를 쓴다
    ("capture", "vault"),  # 캡처 스코프는 vault 해소 위에 얹힌다(#145 U3 단방향)
    ("promote", "capture"),  # 승격 CLI가 인박스·스테이징을 소비(드레인)한다
    ("promote", "explore"),  # 승격 게이트·CLI가 접지 린트(내장 제공자)를 부른다
    ("promote", "vault"),  # 디스패치·스캐폴드가 vault 판정·git I/O를 쓴다
    ("doctor", "capture"),  # 진단이 캡처 런타임 상태를 읽는다
    ("doctor", "promote"),  # 진단이 승격 CLI(scan) 상태를 위임한다
    ("doctor", "vault"),  # 진단이 포인터 해소를 재사용한다
}

# 집행 게이트의 강화 조항 — okf_promote가 import해선 안 되는 도메인.
PROMOTE_FORBIDDEN_DOMAINS = {"capture", "hooks", "doctor"}


def _domain_dirs() -> list[Path]:
    return sorted(p for p in SCRIPTS.iterdir() if p.is_dir() and p.name != "__pycache__")


def _module_domains() -> dict[str, str]:
    """flat 모듈명 → 도메인. stem 유일성은 루트 게이트가 잠그지만 여기서도 지킨다."""
    mapping: dict[str, str] = {}
    for directory in _domain_dirs():
        for path in sorted(directory.glob("*.py")):
            assert path.stem not in mapping, f"stem 충돌: {path.stem} — flat import가 가려진다"
            mapping[path.stem] = directory.name
    assert mapping, "scripts/ 파이썬 파일 미발견 — 게이트 대상 공집합(경로 확인)"
    return mapping


def _imported_names(path: Path) -> set[str]:
    """정적 import(지연 import 포함) + 동적 import(__import__/import_module 상수)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            elif node.level:
                # `from . import x` — 상대 import는 alias가 곧 모듈명이다
                names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            attr = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if attr in ("__import__", "import_module"):
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        names.add(arg.value.split(".")[0])
    return names


def _cross_domain_edges() -> dict[tuple[str, str], list[str]]:
    """교차 도메인 엣지 → 근거 import 목록("파일 → 모듈")."""
    domains = _module_domains()
    edges: dict[tuple[str, str], list[str]] = {}
    for directory in _domain_dirs():
        for path in sorted(directory.glob("*.py")):
            for name in sorted(_imported_names(path)):
                dst = domains.get(name)
                if dst is None or dst == directory.name:
                    continue
                edges.setdefault((directory.name, dst), []).append(f"{path.name} → {name}")
    return edges


def test_domain_set_is_locked():
    actual = [p.name for p in _domain_dirs()]
    assert actual == sorted(DOMAINS), (
        f"도메인 디렉토리 변동: {actual} — 새 도메인은 이 게이트의 DOMAINS와 "
        "bin/okf-py·tests/conftest.py 배선, CLAUDE.md 도메인 표를 함께 고친다."
    )


def test_cross_domain_imports_follow_declared_edges():
    edges = _cross_domain_edges()
    violations = {edge: uses for edge, uses in edges.items() if edge not in ALLOWED_EDGES}
    assert not violations, (
        "선언되지 않은 교차 도메인 import: "
        + "; ".join(
            f"{src}→{dst} ({', '.join(uses)})" for (src, dst), uses in sorted(violations.items())
        )
        + " — 경계를 지키도록 고치거나, 설계 변경이면 ALLOWED_EDGES에 사유와 함께 선언하라."
    )


def test_declared_edges_are_all_in_use():
    """유령 선언 차단 — 실사용이 사라진 방향은 선언도 빠져야 한다(허용 목록은 줄기만 한다)."""
    unused = ALLOWED_EDGES - set(_cross_domain_edges())
    assert not unused, f"실사용 없는 선언 엣지: {sorted(unused)} — ALLOWED_EDGES에서 행을 빼라."


def test_promote_gate_keeps_capture_and_hooks_at_arms_length():
    """okf_promote는 캡처 런타임·훅·진단 무-import — 판정 산물 집행기는 홀로 선다."""
    domains = _module_domains()
    path = SCRIPTS / "promote" / "okf_promote.py"
    offending = sorted(
        name
        for name in _imported_names(path)
        if domains.get(name) in PROMOTE_FORBIDDEN_DOMAINS
        # 벨트-앤-서스펜더: 미존재 study_* 모듈 참조도 잡는다(구 게이트 승계)
        or name == "study"
        or name.startswith("study_")
    )
    assert not offending, f"okf_promote 강화 조항 위반: {offending}"


def test_hook_entry_points_live_in_hooks_domain():
    """hooks.json 배선 표면 == scripts/hooks/ — 진입점 디렉토리 선언이 실배선과 일치한다."""
    text = HOOKS_JSON.read_text(encoding="utf-8")
    json.loads(text)  # 구조 파손 조기 검출(배선 regex가 빈 집합으로 조용히 통과하지 않게)
    wired = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([^/\"]+)/([^\"]+\.py)", text)
    assert wired, "hooks.json에서 배선 스크립트를 하나도 뽑지 못했다"
    misplaced = [f"{domain}/{name}" for domain, name in wired if domain != "hooks"]
    assert not misplaced, f"hooks.json이 hooks/ 밖을 부른다: {misplaced}"
    missing = [name for _domain, name in wired if not (SCRIPTS / "hooks" / name).is_file()]
    assert not missing, f"배선된 진입점이 실재하지 않는다: {missing}"
    # 역방향 — hooks/에 있는데 배선되지 않은 스크립트도 없다(죽은 진입점 차단).
    unwired = sorted(
        p.name for p in (SCRIPTS / "hooks").glob("*.py") if p.name not in {n for _, n in wired}
    )
    assert not unwired, f"hooks/에 배선되지 않은 스크립트: {unwired} — 배선하거나 도메인을 옮겨라."
