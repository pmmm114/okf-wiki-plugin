"""훅 컴파일 대상 도출 게이트 (#299).

CI의 하한 게이트가 무엇을 검사하는지는 이 도출이 정한다. 도출이 조용히 좁아지면
게이트도 같이 좁아지므로, **좁아짐 자체**를 여기서 막는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_compile_targets as hct  # noqa: E402

# 배선된 훅 스크립트 — 도출 결과는 최소한 이것들을 **반드시** 포함한다.
# core 3파일만 손으로 적혀 있던 시절 검사 밖이었던 것이 정확히 study 훅 2종이다.
MUST_INCLUDE = (
    "plugins/okf/scripts/hooks/okf_hooks.py",
    "plugins/okf/scripts/hooks/study_hook.py",
    "plugins/okf/scripts/hooks/study_session.py",
)


def _rel(paths):
    return {p.relative_to(hct.REPO).as_posix() for p in paths}


def test_entry_points_come_from_hooks_json():
    stems = hct.wired_entry_points()
    assert stems, "hooks.json에서 배선 스크립트를 하나도 뽑지 못했다"
    assert "okf_hooks" in stems


def test_closure_covers_every_wired_hook():
    targets = _rel(hct.closure(hct.wired_entry_points()))
    missing = [name for name in MUST_INCLUDE if name not in targets]
    assert not missing, f"컴파일 대상에서 빠진 배선 훅: {missing}"


def test_closure_pulls_transitive_imports():
    """진입점이 직접 부르지 않는 모듈도 import로 닿으면 포함된다."""
    targets = _rel(hct.closure(hct.wired_entry_points()))
    # study_session → study_inbox → study_store 로 이어지는 전이 경로
    assert "plugins/okf/scripts/capture/study_store.py" in targets, sorted(targets)


def test_every_target_exists():
    for path in hct.closure(hct.wired_entry_points()):
        assert path.is_file(), path


def test_empty_derivation_fails_closed(tmp_path, monkeypatch):
    """도출 0건은 exit 2 — '검사할 것이 없음'으로 통과시키지 않는다."""
    empty = tmp_path / "hooks.json"
    empty.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    monkeypatch.setattr(hct, "HOOKS_JSON", empty)
    assert hct.main() == 2


def test_cli_prints_repo_relative_paths():
    proc = subprocess.run(
        [sys.executable, str(hct.REPO / "scripts" / "hook_compile_targets.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines
    for line in lines:
        assert not line.startswith("/"), line
        assert (hct.REPO / line).is_file(), line


def test_module_stems_are_unique():
    """훅 모듈 stem이 유일하다 — 셔틀은 flat 네임스페이스로 import한다.

    `bin/okf-py`가 `scripts/` 아래 도메인 디렉토리를 **전부** PYTHONPATH에 넣으므로
    같은 stem이 양쪽에 있으면 앞선 것이 뒤를 가린다. 도출도 stem으로 색인하니 컴파일
    대상이 조용히 한쪽만 남는다.
    """
    seen: dict[str, Path] = {}
    dupes = []
    for path in sorted(hct.SCRIPTS.rglob("*.py")):
        prev = seen.setdefault(path.stem, path)
        if prev != path:
            dupes.append((path.stem, prev.name, path.name))
    assert not dupes, f"stem 충돌: {dupes} — flat import에서 한쪽이 가려진다"
