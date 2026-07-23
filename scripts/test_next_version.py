"""다음 버전 제안기 테스트 (#164).

범프 도출(타입→등급)·0.x 승격·버전 계산·엔드투엔드를 픽스처 로그로 고정한다
(무네트워크·무git). git 배관은 release_notes와 공유하므로 여기선 도출 로직에 집중한다.
next_version이 stdlib + release_notes 형제만 import하는지도 AST로 확인한다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import next_version as nv
import release_notes as rn


def test_feat_is_minor():
    assert nv.decide_bump(["feat: x (#1)", "fix: y (#2)"], pre_1_0=True) == nv._MINOR


def test_fix_only_is_patch():
    assert nv.decide_bump(["fix: y (#2)", "docs: z (#3)"], pre_1_0=True) == nv._PATCH


def test_no_signal_from_docs_chore():
    # 계약 무변화 타입만 쌓이면 올릴 이유 없음 → 범프 없음(릴리스 보류 신호)
    assert nv.decide_bump(["docs: z", "chore: w", "포맷 안 맞는 라인"], pre_1_0=True) == nv._NONE


def test_highest_signal_wins():
    # feat + fix + docs 혼재 → feat(minor)이 최고
    assert nv.decide_bump(["fix: a", "feat: b", "docs: c"], pre_1_0=True) == nv._MINOR


def test_breaking_pre_1_0_is_minor():
    # 0.x: 파괴도 major가 아니라 minor(bump-minor-pre-major) — `!` 마커·BREAKING 토큰 둘 다
    assert nv.decide_bump(["feat(api)!: 시그니처 변경 (#1)"], pre_1_0=True) == nv._MINOR
    assert nv.decide_bump(["fix: BREAKING 제거 (#2)"], pre_1_0=True) == nv._MINOR


def test_breaking_post_1_0_is_major():
    assert nv.decide_bump(["feat(api)!: 시그니처 변경 (#1)"], pre_1_0=False) == nv._MAJOR


def test_bump_version():
    assert nv.bump_version((0, 5, 1), nv._MINOR) == (0, 6, 0)
    assert nv.bump_version((0, 5, 1), nv._PATCH) == (0, 5, 2)
    assert nv.bump_version((0, 5, 1), nv._MAJOR) == (1, 0, 0)
    assert nv.bump_version((0, 5, 1), nv._NONE) == (0, 5, 1)  # 신호 없으면 유지


def test_parse_base():
    assert nv.parse_base("v0.5.1") == (0, 5, 1)
    assert nv.parse_base("0.6.0") == (0, 6, 0)
    assert nv.parse_base("v1.2.3-rc1") == (1, 2, 3)
    assert nv.parse_base(None) == (0, 0, 0)  # 첫 릴리스 전


def test_main_end_to_end(monkeypatch, capsys):
    # base=최신 태그 v0.5.1, 로그에 feat → minor → 0.6.0. stdout은 버전만.
    def fake_run(args: list[str]) -> str:
        if args[0] == "tag":
            return "v0.5.0\nv0.5.1\n"
        if args[0] == "log":
            return "feat: 새 기능 (#9)\nfix: 버그 (#10)\n"
        raise AssertionError(args)

    monkeypatch.setattr(rn, "_run", fake_run)
    assert nv.main(["--to", "HEAD"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "0.6.0"  # stdout = 버전만(스크립트용)
    assert "minor" in out.err  # 근거는 stderr


def test_main_no_signal_keeps_current(monkeypatch, capsys):
    def fake_run(args: list[str]) -> str:
        if args[0] == "tag":
            return "v0.5.1\n"
        if args[0] == "log":
            return "docs: 안내 (#11)\nchore: 잡일 (#12)\n"
        raise AssertionError(args)

    monkeypatch.setattr(rn, "_run", fake_run)
    assert nv.main(["--to", "HEAD"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "0.5.1"  # 범프 없음 → 현행 유지
    assert "범프 신호 없음" in out.err


def test_imports_stdlib_and_sibling_only():
    src = (Path(__file__).resolve().parent / "next_version.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__", "release_notes"}
    extra = imported - allowed
    assert not extra, f"허용 밖 import: {extra}"
