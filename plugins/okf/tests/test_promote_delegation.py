"""승격 위임 게이트(#351 U2) — 두 승격 커맨드가 같은 집행기(apply)로 수렴했는지.

`/study`가 개념 파일을 직접 쓰던 구형 경로는 frontmatter 형식 실수와 §9 재시도
루프를 사람+모델 몫으로 남겼다. 전환의 골자를 문서 게이트로 잠근다 — (1) 판정
산출은 제안 JSON이고 직접 쓰기 금지 선언이 문서에 있다 (2) 집행은 okf_promote
apply 호출 **지시**다(낱말 언급이 아니라 — census 게이트가 감도 실증에서 걸린
전례) (3) 정식 어휘는 `layer`·`mode`이고 `target_layer`는 별칭 설명으로만 남는다
(#358 리뷰) (4) 캡처 provenance(#114 U5)가 위임 후에도 `log_note`로 log.md에 남는다.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
COMMANDS = PLUGIN / "commands"
APPLY_CALL = 'okf_promote.py" apply'


def _study() -> str:
    return (COMMANDS / "study.md").read_text(encoding="utf-8")


def _section(text: str, heading: str, stop: str) -> str:
    start = text.index(heading)
    return text[start : text.index(stop, start)]


def test_study_conceptualizing_does_not_author_concept_files():
    """4단계가 직접 쓰기 대신 제안 산출을 선언한다 — okf-promote.md와 같은 불변식."""
    body = _section(_study(), "4. **개념화", "5. **게이트")
    assert "직접 쓰지 않는다" in body, "직접 쓰기 금지 선언이 없다"
    assert "제안 JSON" in body, "판정 산출(제안 JSON)이 선언되지 않았다"


def test_study_execution_delegates_to_promote_apply():
    """5단계가 집행기 apply 실행을 지시하고 종료코드로 분기한다."""
    body = _section(_study(), "5. **게이트", "6. **드레인")
    assert APPLY_CALL in body, "5단계에 okf_promote apply 호출 지시가 없다"
    assert "종료코드" in body and "`3`" in body, "종료코드 분기가 없다"


def test_both_commands_call_the_same_executor():
    """두 승격 경로의 게이트·로그·색인 순서가 한 구현으로 수렴한다(#351 기대 효과)."""
    for name in ("study.md", "okf-promote.md"):
        body = (COMMANDS / name).read_text(encoding="utf-8")
        assert APPLY_CALL in body, f"{name}이 집행기 apply를 부르지 않는다"


def test_canonical_vocab_is_layer_and_mode():
    """정식 어휘는 `layer`·`mode` — `target_layer`는 별칭 설명으로만 남는다(#358 리뷰).

    별칭이 정식 표기로 남으면 "정식 어휘의 소비 문서가 0"인 상태가 계속된다 —
    별칭을 언급하는 줄은 그것이 별칭임을 같은 줄에서 말해야 한다.
    """
    for name in ("study.md", "okf-promote.md"):
        body = (COMMANDS / name).read_text(encoding="utf-8")
        assert "`mode`" in body, f"{name}이 mode 계약을 가르치지 않는다"
        for line in body.splitlines():
            if "target_layer" in line:
                assert "별칭" in line, f"{name}이 target_layer를 정식 어휘로 가르친다: {line[:80]}"


def test_study_keeps_capture_provenance_via_log_note():
    """캡처 일자 provenance(#114 U5)가 apply 위임 후에도 `log_note`로 새겨진다."""
    body = _study()
    assert "`log_note`" in body and "captured" in body, "provenance 이관 지시가 사라졌다"


def test_study_drain_consumes_apply_machine_fields():
    """6단계 드레인이 apply 결과의 기계 필드에 배선된다 — 반려분은 드레인하지 않는다."""
    body = _section(_study(), "6. **드레인", "7. **디스패치")
    assert "promoted[].path" in body and "promoted[].layer" in body, "드레인 배선이 없다"
    assert "rejected[]" in body, "반려분 미드레인 규칙이 없다"


def test_rejected_branching_is_code_based():
    """반려 분기는 `rejected[].reasons[]`의 `code`다(#360) — 문구 매칭 잔존 금지.

    "반려 사유에 `rubric`이 있으면"은 사실상 한국어 사유 문구의 부분일치 분기였다 —
    `lint_warns`가 `code`로 간 것과 같은 축으로, 두 소비 문서가 코드 어휘 단일원천
    (`REJECT_CODES`)을 가리키고 rubric 분기를 코드로 지시해야 한다.
    """
    for name in ("study.md", "okf-promote.md"):
        body = (COMMANDS / name).read_text(encoding="utf-8")
        assert "REJECT_CODES" in body, f"{name}이 반려 코드 단일원천을 가리키지 않는다"
        assert "`rubric_missing`" in body, f"{name}의 rubric 분기가 code 기반이 아니다"
