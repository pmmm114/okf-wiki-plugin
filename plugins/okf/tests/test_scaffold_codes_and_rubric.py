"""스캐폴드 코드 집합 ⊆ 문서 분기, rubric 계약의 실체화 (#307).

`okf-init.md`가 스크립트의 오류 반환에 **분기를 갖지 않았다** — 문서가 정의한 상태
집합이 코드가 내는 집합보다 좁았다. `exit 2`(설정 파싱 실패)와 `ok: false`에 지정된
행동이 아예 없어, 모델이 그대로 다음 단계로 진행할 수 있었다.

`rubric`은 반대 방향이다 — 문서가 계약으로 정의해 놓고 **코드 어디에서도 읽지도 저장하지도
않았다**. 선별 표의 '새 인식·반증' 열이 그 자리에서 지어낸 문장이 되고, 그것이 승인
근거로 쓰인다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import okf_promote
import pytest
import study_scaffold

PLUGIN = Path(__file__).resolve().parent.parent
INIT_MD = PLUGIN / "commands" / "okf-init.md"
SPEC = {
    "field": "layer",
    "derivation_field": "derived_from",
    "order": ["information", "knowledge", "wisdom"],
    "rules": {},
}


# --- 코드 집합 ↔ 문서 분기 (test_dispatch_verdict와 동형) ----------------------


def test_scaffold_codes_have_recovery():
    assert study_scaffold.SCAFFOLD_CODES
    for code, recovery in study_scaffold.SCAFFOLD_CODES.items():
        assert recovery.strip(), f"{code}에 복구 지시가 없다"


def test_init_md_branches_on_every_scaffold_code():
    """`okf-init.md`가 **모든** 스캐폴드 코드에 분기를 갖는다."""
    body = INIT_MD.read_text(encoding="utf-8")
    missing = [c for c in study_scaffold.SCAFFOLD_CODES if f"`{c}`" not in body]
    assert not missing, f"okf-init.md에 분기 없는 코드: {missing}"


def _branch_line(code: str) -> str:
    """분기 목록 항목 줄(- `code` → …). 산문 언급은 세지 않는다.

    "파일 어딘가에 코드가 언급됐는가"로는 부족하다 — 다른 절의 언급이 이 절의 분기
    부재를 가려 게이트가 공회전한다(실측: 1단계 분기를 지워도 H1b 언급 때문에 통과했다).
    """
    body = INIT_MD.read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if ln.lstrip().startswith(f"- `{code}`")]
    assert lines, f"okf-init.md 1단계에 `{code}` 분기 항목이 없다"
    return lines[0]


def test_init_md_forbids_advancing_after_parse_error():
    """파싱 실패 분기가 **2단계 진행 금지**를 명시한다.

    진행하면 `bundlePath`를 읽을 수 없어 기본값으로 떨어져, 사용자가 지정한 위치가
    아닌 곳에 번들이 생긴다 — 그것이 이 분기가 없을 때 실제로 일어나는 일이다.
    """
    # **1단계 항목**(3칸 들여쓰기)만 본다. H1b에도 같은 코드의 분기가 있어서, 아무
    # 항목이나 받으면 1단계 분기를 지워도 통과한다(실측) — 2단계 진행을 막는 것은
    # 1단계 분기의 몫이므로 그 자리를 지정해 잠근다.
    body = INIT_MD.read_text(encoding="utf-8")
    prefix = f"   - `{study_scaffold.CODE_CONFIG_PARSE_ERROR}`"
    step_one = [ln for ln in body.splitlines() if ln.startswith(prefix)]
    assert step_one, "okf-init.md 1단계에 config_parse_error 분기 항목이 없다"
    assert "진행하지 않는다" in step_one[0], step_one[0]


@pytest.mark.parametrize("code", sorted(study_scaffold.SCAFFOLD_CODES))
def test_init_md_has_a_branch_item_per_code(code):
    """코드마다 **분기 항목**이 있다 — 산문 언급으로는 부족하다."""
    assert _branch_line(code)


# --- 실제 동작: 깨진 설정에서 부분 산출물이 없다 -------------------------------


def _run_scaffold(project: Path):
    return subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "capture" / "study_scaffold.py"), str(project)],
        capture_output=True,
        text=True,
    )


def test_broken_config_yields_machine_code_and_no_partial_output(tmp_path):
    """트레일링 콤마 설정 → `{ok: false, code: config_parse_error}` + `.okf-study/` 미생성."""
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".okf-wiki.json").write_text('{"bundlePath": "kb",}', encoding="utf-8")

    proc = _run_scaffold(project)
    assert proc.returncode == 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["code"] == study_scaffold.CODE_CONFIG_PARSE_ERROR, payload
    assert payload["reason"], payload
    assert not (project / ".okf-study").exists(), "파싱 실패인데 부분 산출물이 남았다"


def test_guard_refusal_yields_machine_code(tmp_path):
    """비-git 디렉토리 → `{ok: false, code: guard_refused}` + exit 3."""
    proc = _run_scaffold(tmp_path)
    assert proc.returncode == 3, proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["code"] == study_scaffold.CODE_GUARD_REFUSED, payload


def test_success_is_idempotent_and_machine_shaped(tmp_path):
    """정상 경로는 `{ok: true, code: ok}`이고 **멱등**이다(기존 계약 무변경)."""
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    first = json.loads(_run_scaffold(project).stdout)
    assert first["ok"] is True and first["code"] == study_scaffold.CODE_OK
    assert (project / ".okf-study" / ".gitignore").is_file()

    second = json.loads(_run_scaffold(project).stdout)
    assert second["ok"] is True  # 재실행이 안전하다


# --- rubric: 게이트되고 영속된다 ------------------------------------------------


def _proposal(**over):
    base = {
        "target_layer": "knowledge",
        "type": "concept",
        "description": "연결.",
        "derived_from": [],
        "body": "# 본문",
        "rubric": {"new_insight": "새 연결.", "falsification": "근거가 틀리면."},
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "rubric",
    [None, {}, {"new_insight": "있음"}, {"new_insight": " ", "falsification": ""}],
    ids=["없음", "빈dict", "필드누락", "공백만"],
)
def test_upper_layer_proposal_without_rubric_is_rejected(rubric):
    """상위 층 제안은 rubric 없이 통과하지 못한다 — 빈칸은 승인 근거가 아니다."""
    reasons = okf_promote.gate_proposal(SPEC, {}, ".", _proposal(rubric=rubric))
    assert any(r["code"] == "rubric_missing" for r in reasons), reasons


def test_information_layer_does_not_require_rubric():
    """정보 층은 요구하지 않는다 — 자기검증은 **상위 층의 새 인식**에 대한 것이다."""
    reasons = okf_promote.gate_proposal(
        SPEC, {}, ".", _proposal(target_layer="information", rubric=None)
    )
    assert not any(r["code"] == "rubric_missing" for r in reasons), reasons


def test_rubric_is_persisted_in_rendered_concept():
    """rubric이 개념 파일에 **남는다** — 그 자리의 즉흥이 아니라 읽히는 기록이 되게."""
    text = okf_promote.render_concept(SPEC, _proposal())
    assert "## 자기검증" in text, text
    assert "새 연결." in text and "근거가 틀리면." in text, text


def test_rendered_rubric_is_body_not_frontmatter():
    """frontmatter가 아니라 본문에 둔다 — 엔진의 taxonomy-neutral 계약을 늘리지 않는다."""
    text = okf_promote.render_concept(SPEC, _proposal())
    frontmatter = text.split("---", 2)[1]
    assert "new_insight" not in frontmatter, frontmatter
    assert "## 자기검증" in text.split("---", 2)[2]


def test_render_without_rubric_is_unchanged():
    """rubric 없는 제안(정보 층 등)의 산출물은 그대로 — 회귀 방지."""
    text = okf_promote.render_concept(SPEC, _proposal(rubric=None))
    assert "## 자기검증" not in text
    assert text.endswith("# 본문\n")


def test_promote_md_states_rubric_is_required_and_persisted():
    """`okf-promote.md`가 rubric의 **실제 계약**을 말한다 — 정의만 해 놓고 끝내지 않는다.

    문서가 필드를 계약으로 적어 두고 코드가 읽지도 저장하지도 않던 것이 이 유닛의 배경이다.
    코드를 고친 뒤 문서가 그대로면 이번엔 반대 방향으로 어긋난다(요구되는데 안 적혀 있음).
    """
    body = (PLUGIN / "commands" / "okf-promote.md").read_text(encoding="utf-8")
    assert "rubric은 상위 층" in body and "필수" in body, "필수 여부가 문서에 없다"
    assert "## 자기검증" in body, "영속 위치가 문서에 없다"
