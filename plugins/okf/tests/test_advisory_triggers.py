"""자문 축의 트리거가 기계 필드인가 (#306).

세 자문 축(prune·접지 린트·근사중복)이 결정적 판정기를 갖고 있거나 가질 수 있는데도
트리거가 **모델의 눈대중**에 걸려 있었다. 자문의 성격(warn·exit 0·자동병합 없음)은
그대로 두고, **무엇을 보고 트리거하는가**만 기계 축으로 옮긴다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import okf_layers
import pytest

PLUGIN = Path(__file__).resolve().parent.parent
STUDY_MD = PLUGIN / "commands" / "study.md"


def _doc() -> str:
    return STUDY_MD.read_text(encoding="utf-8")


# --- 접지 린트: 코드 enum ↔ 문서 분기 (U3·U4와 동형) --------------------------


def test_warn_codes_have_recovery():
    """모든 린트 코드에 실행 가능한 복구 지시가 있다."""
    assert okf_layers.WARN_CODES, "WARN_CODES 상수가 없다"
    for code, recovery in okf_layers.WARN_CODES.items():
        assert recovery.strip(), f"{code}에 복구 지시가 없다"


def test_study_md_branches_on_every_warn_code():
    """`study.md`가 **모든** 린트 코드에 분기를 갖는다 — 코드가 늘면 문서 미갱신이 red."""
    body = _doc()
    missing = [c for c in okf_layers.WARN_CODES if f"`{c}`" not in body]
    assert not missing, f"study.md에 분기 없는 린트 코드: {missing}"


def test_check_findings_only_emit_registered_codes():
    """`check_findings`가 내는 코드가 전부 `WARN_CODES`에 등록돼 있다."""
    src = Path(okf_layers.__file__).read_text(encoding="utf-8")
    declared = {
        getattr(okf_layers, name)
        for name in dir(okf_layers)
        if name.startswith("WARN_") and isinstance(getattr(okf_layers, name), str)
    }
    assert declared == set(okf_layers.WARN_CODES), (
        f"WARN_* 상수 {sorted(declared)} vs WARN_CODES {sorted(okf_layers.WARN_CODES)}"
    )
    assert "_check_coded" in src


def test_lint_json_contract(tmp_path):
    """`--json`이 `{count, warns:[{path, code, message}]}` 계약을 낸다."""
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "index.md").write_text('---\nokf_version: "0.1"\n---\n# Concepts\n', encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts" / "explore" / "okf_layers.py"),
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    if not proc.stdout.strip():
        pytest.skip(f"엔진 셔틀 미가용: {proc.stderr.strip()[:120]}")
    payload = json.loads(proc.stdout)
    assert set(payload) == {"count", "warns"}, payload
    assert payload["count"] == len(payload["warns"])
    for warn in payload["warns"]:
        assert set(warn) == {"path", "code", "message"}, warn
        assert warn["code"] in okf_layers.WARN_CODES


def test_lint_stays_advisory():
    """린트는 여전히 **자문**이다 — 기본 exit 0(LAYERS.md 설계 의도 불변)."""
    src = Path(okf_layers.__file__).read_text(encoding="utf-8")
    assert "return 1 if (findings and args.strict) else 0" in src


# --- prune 트리거가 dry-run 결과에 걸리는가 ------------------------------------


def test_study_md_triggers_prune_from_dry_run():
    """3단계가 `prune --dry-run`을 **항상 먼저** 돌리고 `matches` 건수로 분기한다."""
    body = _doc()
    assert "prune <project> --dry-run" in body, "dry-run 선행 지시가 없다"
    assert "matches" in body, "판정 축(matches)이 문서에 없다"


def test_study_md_does_not_trigger_prune_by_eyeball():
    """ "보이면"을 트리거로 쓰지 않는다 — 펼치지 않은 그룹의 노이즈는 보이지 않는다."""
    # 인용된 `"보이면"`은 **왜 트리거로 쓰면 안 되는지** 설명하는 것이라 통과시킨다 —
    # 값 표기와 산문을 가르는 다른 게이트들과 같은 판별이다.
    for line in _doc().splitlines():
        if "노이즈" in line and "prune" in line:
            bare = line.replace('"보이면"', "")
            assert "보이면" not in bare, f"눈대중 트리거가 남아 있다: {line.strip()[:80]}"


def test_study_md_gates_clear_suggestion_on_dry_run():
    """8단계 `--clear` 제안도 dry-run 0건일 때만."""
    tail = _doc().rsplit("8. **요약**", 1)[-1]
    assert "dry-run" in tail and "0건" in tail, tail
