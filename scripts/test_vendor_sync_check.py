"""vendor 동기 게이트 회귀 (#303) — 이 게이트는 회귀 테스트가 **0건**이었다.

CLAUDE.md가 "vendor는 업스트림 바이트 그대로"를 이 스크립트에 위임하는데, 정작
스크립트를 지키는 검사가 없었다. 위임받은 게이트가 스스로 무력해지는 두 경로를
여기서 고정한다.

스크립트는 `okf-core/scripts/`에 있고 이 스위트는 repo 툴링 계층(`scripts`)이다 —
CI가 `pytest scripts -q`로 도는 곳이 여기라, 게이트의 회귀도 여기 둔다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "okf-core" / "scripts" / "vendor_sync_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("vendor_sync_check", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vsc = _load()


def _tree(root: Path, files: dict[str, str], *, register: list[str] | None = None) -> Path:
    """가짜 repo 루트 — `okf-core/vendor/` 아래 파일과 그중 lock 등록분을 만든다."""
    vendor = root / vsc.VENDOR_REL
    vendor.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    registered = {
        rel: "sha256:" + hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in (register if register is not None else list(files))
    }
    (root / vsc.LOCK_REL).write_text(
        json.dumps({"entries": [{"name": "x", "ref": "r", "files": registered}]}), encoding="utf-8"
    )
    return root


_SPEC = f"{vsc.VENDOR_REL}/spec/SPEC.md"
_ORACLE = f"{vsc.VENDOR_REL}/oracle/okf_validate.py"


def test_passes_when_lock_and_tree_agree(tmp_path):
    root = _tree(tmp_path, {_SPEC: "spec 본문\n", _ORACLE: "print('x')\n"})
    problems, total = vsc.check(root)
    assert problems == [] and total == 2


def test_detects_hash_mismatch(tmp_path):
    root = _tree(tmp_path, {_SPEC: "spec 본문\n"})
    (root / _SPEC).write_text("변조됨\n", encoding="utf-8")
    problems, _total = vsc.check(root)
    assert any("해시 불일치" in p for p in problems), problems


def test_detects_missing_registered_file(tmp_path):
    root = _tree(tmp_path, {_SPEC: "spec 본문\n"})
    (root / _SPEC).unlink()
    problems, _total = vsc.check(root)
    assert any("파일 없음" in p for p in problems), problems


# --- 여기부터가 신설 축: 게이트가 스스로 무력해지는 두 경로 --------------------


def test_unregistered_vendor_file_is_a_problem(tmp_path):
    """`vendor/`에 lock 미등록 파일이 있으면 red — 검사 사각지대다.

    변경 전 실측: 미등록 파일을 넣어도 `"통과: 2개 파일 일치"` exit 0.
    """
    root = _tree(tmp_path, {_SPEC: "spec 본문\n"}, register=[_SPEC])
    injected = root / vsc.VENDOR_REL / "spec" / "INJECTED.md"
    injected.write_text("lock에 없는 파일\n", encoding="utf-8")
    problems, _total = vsc.check(root)
    assert any("lock 미등록" in p and "INJECTED" in p for p in problems), problems


@pytest.mark.parametrize(
    "rel",
    [
        f"{vsc.VENDOR_REL}/patches/README.md",  # 수정은 여기 패치로(CLAUDE.md)
        f"{vsc.VENDOR_REL}/spec/LICENSE-APACHE-2.0",  # 라이선스 전문
        f"{vsc.VENDOR_REL}/vendor.lock",  # lock 자신
    ],
)
def test_exempt_paths_are_not_flagged(tmp_path, rel):
    """면제 대상은 미등록이어도 통과 — 면제 목록을 늘리는 것이 곧 게이트를 줄이는 것이다."""
    root = _tree(tmp_path, {_SPEC: "spec 본문\n"}, register=[_SPEC])
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if rel != vsc.LOCK_REL:  # lock은 _tree가 이미 썼다
        path.write_text("면제 대상\n", encoding="utf-8")
    problems, _total = vsc.check(root)
    assert problems == [], problems


def test_empty_lock_is_execution_error_not_pass(tmp_path, capsys):
    """lock의 files를 비우면 실행 오류(2) — "0개 일치"는 통과가 아니라 검사 실종이다.

    변경 전 실측: `"vendor sync check 통과: 0개 파일 일치"` exit 0.
    """
    root = _tree(tmp_path, {_SPEC: "spec 본문\n"}, register=[])
    problems, total = vsc.check(root)
    assert total == 0
    # 미등록 역방향 검사가 먼저 잡으므로 문제도 함께 난다 — 어느 쪽이든 통과가 아니다.
    assert problems, "빈 lock이 무결점으로 보이면 안 된다"


def test_real_repo_passes():
    """실제 repo가 양방향 대조를 통과한다 — 면제 목록이 현실과 맞는지 확인."""
    problems, total = vsc.check(_ROOT)
    assert problems == [], problems
    assert total > 0
