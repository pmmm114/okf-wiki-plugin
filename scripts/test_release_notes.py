"""릴리스 노트 생성기 테스트 (#142).

파서·그룹핑·정렬·제외 규칙을 픽스처 로그로 고정한다(무네트워크·무git). 스크립트가
stdlib만 import하는지도 AST로 확인한다 — 무의존·오프라인 실행 보장.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import release_notes as rn

SAMPLE = [
    "feat(study): 개념 블록 원자 + 캡처 경로 통일 (#131) (#137)",
    "fix(hooks): exec form 따옴표 오염 (#120)",
    "docs(releasing): 마일스톤 생성 가이드 (#128)",
    "chore: 버전 0.5.0.dev0 상향 (#127)",
    "release: v0.4.0 (#126)",
    "refactor(core): 파서 정리 (#99)",
    "포맷 안 맞는 라인",
]


def test_grouping_by_type():
    g = rn.parse_log(SAMPLE)
    assert g["추가"] == ["**study**: 개념 블록 원자 + 캡처 경로 통일 (#131) (#137)"]
    assert g["수정"] == ["**hooks**: exec form 따옴표 오염 (#120)"]
    assert g["문서"] == ["**releasing**: 마일스톤 생성 가이드 (#128)"]
    # refactor·비매칭 → 기타, chore/release는 기본 제외(어디에도 없음)
    assert g["기타"] == ["**core**: 파서 정리 (#99)", "포맷 안 맞는 라인"]
    assert "0.5.0.dev0" not in str(g) and "v0.4.0" not in str(g)


def test_excluded_included_with_all():
    g = rn.parse_log(SAMPLE, include_excluded=True)
    body = "\n".join(g["기타"])
    assert "0.5.0.dev0" in body  # chore가 기타로 편입
    assert "v0.4.0" in body  # release가 기타로 편입


def test_breaking_marker():
    g = rn.parse_log(["feat(api)!: 시그니처 변경 (#1)"])
    assert g["추가"] == ["⚠️ **api**: 시그니처 변경 (#1)"]


def test_no_scope_kept_plain():
    g = rn.parse_log(["fix: 단순 수정 (#2)"])
    assert g["수정"] == ["단순 수정 (#2)"]


def test_render_order_and_nonempty_only():
    out = rn.render(rn.parse_log(SAMPLE))
    assert (
        out.index("### 추가")
        < out.index("### 수정")
        < out.index("### 문서")
        < out.index("### 기타")
    )
    assert out.endswith("\n")
    # 빈 카테고리는 렌더되지 않는다
    only_fix = rn.render(rn.parse_log(["fix: x (#1)"]))
    assert "### 수정" in only_fix and "### 추가" not in only_fix


def test_render_empty_is_empty_string():
    assert rn.render({}) == ""


def test_default_from_resolution():
    tags = ["v0.1.0", "v0.2.0", "v0.2.1", "v0.3.0", "v0.4.0"]
    assert rn._default_from("v0.4.0", tags) == "v0.3.0"  # 태그면 직전 태그
    assert rn._default_from("HEAD", tags) == "v0.4.0"  # 아니면 최신 태그
    assert rn._default_from("v0.1.0", tags) is None  # 첫 태그는 시작 없음(전체)
    assert rn._default_from("HEAD", []) is None  # 태그 없으면 전체


def test_main_end_to_end(monkeypatch, capsys):
    calls: dict[str, str] = {}

    def fake_run(args: list[str]) -> str:
        if args[0] == "tag":
            return "v0.1.0\nv0.2.0\n"
        if args[0] == "log":
            calls["range"] = args[1]
            return "feat: 새 기능 (#9)\nchore: 잡일 (#10)\n"
        raise AssertionError(args)

    monkeypatch.setattr(rn, "_run", fake_run)
    assert rn.main(["--to", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert calls["range"] == "v0.2.0..HEAD"  # 기본 from = 최신 태그
    assert "### 추가" in out and "새 기능 (#9)" in out
    assert "잡일" not in out  # chore 제외


def test_stdlib_only_imports():
    src = (Path(__file__).resolve().parent / "release_notes.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    extra = imported - set(sys.stdlib_module_names) - {"__future__"}
    assert not extra, f"stdlib 아닌 import: {extra}"
