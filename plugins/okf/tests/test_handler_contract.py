"""소비처 계약 대조 게이트 (#278) — 문서가 약속한 것과 파이프라인이 주는 것이 같은가.

핸들러는 소비처가 자기 repo에 주입한다(목적지 무참조). 그래서 계약 문서의 정확성이 곧
인터페이스의 정확성이고, **어긋나도 우리 테스트로는 원리적으로 안 잡힌다** — 피해자가 우리
코드가 아니라 "우리 문서를 믿고 짠 소비처 코드"이기 때문이다. 이 파일이 그 대조를 대신한다.

계약 정본은 ``docs/adopting-study.md`` §4다. 문서가 repo 밖(설치형 배치)이면 잠글 대상이
없으므로 스킵한다 — ``test_study_scaffold_handler`` 의 docs 대조와 같은 관례.
"""

from __future__ import annotations

import re
from pathlib import Path

import okf_layers
import pytest
import study
import study_dispatch
import study_scaffold_handler

CONTRACT_DOC = Path(__file__).resolve().parents[3] / "docs" / "adopting-study.md"


def _doc() -> str:
    if not CONTRACT_DOC.is_file():
        pytest.skip("계약 문서 부재(레포 외 배치)")
    return CONTRACT_DOC.read_text(encoding="utf-8")


def _doc_env_keys(text: str) -> set[str]:
    """§4 환경변수 표가 약속한 키 집합.

    **빈 집합은 실패다.** 표 서식이 바뀌어 정규식이 0건을 뽑으면 ``set() == set()``이
    성립해 대조가 조용히 통과한다 — 계약이 어긋난 것을 "일치"로 보고하는 꼴이다(#304).
    """
    keys = set(re.findall(r"^\|\s*`(OKF_[A-Z_]+)`\s*\|", text, re.MULTILINE))
    assert keys, "계약 문서에서 env 키를 하나도 뽑지 못했다 — 표 서식이 바뀌었거나 표가 사라졌다"
    return keys


def _code_env_keys(source: Path | None = None) -> set[str]:
    """``_handler_env``가 실제로 세팅하는 키 집합(소스에서 추출 — 실행 환경 오염 회피)."""
    src = (source or Path(study_dispatch.__file__)).read_text(encoding="utf-8")
    keys = set(re.findall(r'env\["(OKF_[A-Z_]+)"\]\s*=', src))
    assert keys, "디스패처 소스에서 env 키를 하나도 뽑지 못했다 — 세팅 꼴이 바뀌었다"
    return keys


def _doc_stdin_concept_keys(text: str) -> set[str]:
    """§4 stdin 예시 JSON의 ``concept`` 필드 집합."""
    block = re.search(r'"concept":\s*\{(.+?)\}', text, re.DOTALL)
    assert block, "계약 문서에서 stdin concept 예시를 찾지 못함"
    keys = set(re.findall(r'"([a-z_]+)":', block.group(1)))
    assert keys, "계약 문서의 stdin concept 예시에서 필드를 하나도 뽑지 못했다"
    return keys


def _code_stdin_concept_keys() -> set[str]:
    """``cmd_dispatch``가 item["concept"]에 싣는 필드 집합."""
    src = Path(study.__file__).read_text(encoding="utf-8")
    block = re.search(r'"concept":\s*\{(.+?)\n\s*\},', src, re.DOTALL)
    assert block, "study.py에서 concept 구성을 찾지 못함"
    return set(re.findall(r'"([a-z_]+)":', block.group(1)))


# --- 층 어휘: 입구가 기계 어휘를 강제하는가 -----------------------------------

LAYER_VALUES = okf_layers.load_layers_spec()["values"]


@pytest.mark.parametrize(
    "argv",
    [
        ["resolve", ".", "--id", "x", "--status", "promoted", "--layer", "지식"],
        ["near-bundle", ".", "--snippet", "s", "--layer", "지식"],
        ["dispatch", ".", "--concept-layer", "지식"],
    ],
)
def test_layer_args_reject_non_vocabulary(argv):
    """어휘 밖 값은 **fail-visible**로 거부한다.

    문서·help가 한국어 라벨을 값처럼 보이게 표기해 왔고 입구 검증이 없어, 한국어가 원장·
    저널·``OKF_CONCEPT_LAYER``까지 조용히 흘렀다. 같은 값을 받는 ``okf_layers``는 `미지의 층`
    으로 큰 소리로 막는다 — 한쪽만 무성인 상태를 없앤다.
    """
    with pytest.raises(SystemExit) as exc:
        study.main(argv)
    assert exc.value.code == 2  # argparse 사용법 오류


@pytest.mark.parametrize("layer", LAYER_VALUES)
def test_layer_args_accept_machine_vocabulary(layer, tmp_path):
    """기계 어휘는 파서를 통과한다(회귀 방지 — 게이트가 정상 값을 삼키면 안 된다)."""
    parser_ok = True
    try:
        study.main(["dispatch", str(tmp_path), "--concept-layer", layer])
    except SystemExit as exc:  # 파싱 실패만 잡는다. 실행 결과는 이 테스트의 관심이 아니다
        parser_ok = exc.code != 2
    assert parser_ok


# --- 계약 표면: 문서 ⟺ 코드 ---------------------------------------------------


def test_extractors_fail_loud_when_they_find_nothing(tmp_path):
    """추출이 0건이면 **실패**한다 — 빈 집합끼리의 일치가 '계약 일치'로 읽히지 않게.

    이 게이트가 없으면 서식 변경 하나로 대조 3종이 통째로 무력화되면서 전부 초록이다.
    """
    for extract, arg in (
        (_doc_env_keys, "환경변수 표가 없는 문서"),
        (_doc_stdin_concept_keys, '"concept": {}'),
    ):
        with pytest.raises(AssertionError):
            extract(arg)

    empty = tmp_path / "empty.py"
    empty.write_text("# env 세팅이 없는 소스\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _code_env_keys(empty)


def test_handler_env_keys_match_contract_doc():
    """env 키 집합이 문서와 정확히 같다 — 코드가 키를 늘리면 문서 미갱신이 red."""
    assert _code_env_keys() == _doc_env_keys(_doc())


# --- 참조 핸들러 템플릿 주석 ⟺ 계약 -------------------------------------------
#
# 템플릿 주석은 소비처가 **가장 먼저 읽는 계약 서술**이다(파일을 통째로 복사해 가므로).
# 그런데 대조 게이트가 없어 이미 드리프트해 있었다 — env 6키 중 4키만, stdin concept
# 4필드 중 3필드만 적혀 있었다(`OKF_CONCEPT_LAYER`·`OKF_TRIGGER`·`layer` 누락).


def _template_doc() -> str:
    """참조 핸들러 템플릿의 모듈 docstring(계약 서술 구간)."""
    body = study_scaffold_handler.HANDLER_TEMPLATE
    block = re.search(r'"""(.+?)"""', body, re.DOTALL)
    assert block, "핸들러 템플릿에서 모듈 docstring을 찾지 못했다"
    return block.group(1)


def test_template_comment_lists_every_env_key():
    """템플릿 주석의 env 키 목록이 실제 계약과 정확히 같다."""
    listed = set(re.findall(r"OKF_[A-Z_]+", _template_doc()))
    assert listed == _code_env_keys(), (
        f"템플릿 주석 {sorted(listed)} vs 실제 {sorted(_code_env_keys())} — "
        "소비처가 복사해 가는 계약 서술이므로 누락은 곧 소비처의 누락이다"
    )


def test_template_comment_lists_every_stdin_concept_field():
    """템플릿 주석의 stdin concept 필드가 실제 계약과 정확히 같다."""
    block = re.search(r"concept\s*:?\s*\{([^}]*)\}", _template_doc())
    assert block, "템플릿 주석에서 stdin concept 서술을 찾지 못했다"
    listed = {token.strip() for token in block.group(1).split(",") if token.strip()}
    assert listed == _code_stdin_concept_keys(), (
        f"템플릿 주석 {sorted(listed)} vs 실제 {sorted(_code_stdin_concept_keys())}"
    )


def test_stdin_concept_fields_match_contract_doc():
    """stdin ``concept`` 필드 집합이 문서와 정확히 같다."""
    assert _code_stdin_concept_keys() == _doc_stdin_concept_keys(_doc())


def test_contract_doc_layer_value_is_machine_vocabulary():
    """문서의 stdin 예시가 **값 자리**에 기계 어휘를 쓴다.

    한국어는 값이 아니라 라벨이다. 소비처가 예시를 복사하면 그대로 계약 값이 되므로
    여기에 한국어가 있으면 소비처 핸들러가 어긋난 값을 기대하게 된다.
    """
    text = _doc()
    block = re.search(r'"concept":\s*\{(.+?)\}', text, re.DOTALL)
    layer_value = re.search(r'"layer":\s*"([^"]*)"', block.group(1))
    assert layer_value, "stdin 예시에 layer 필드가 없음"
    assert not re.search(r"[가-힣]", layer_value.group(1)), (
        f"stdin 예시의 layer 값에 한국어: {layer_value.group(1)!r} — 값은 기계 어휘여야 한다"
    )


def test_trigger_vocabulary_is_reachable():
    """문서가 약속한 ``OKF_TRIGGER`` 값이 실제로 낼 수 있는 값이다.

    약속만 있고 내는 경로가 없으면 소비처의 분기가 영원히 죽은 코드가 된다.
    """
    text = _doc()
    row = re.search(r"^\|\s*`OKF_TRIGGER`\s*\|(.+)$", text, re.MULTILINE)
    assert row, "계약 문서에 OKF_TRIGGER 행이 없음"
    promised = set(re.findall(r"`([a-z]+)`", row.group(1)))
    reachable = set(_dispatch_source_choices())
    assert promised == reachable, f"문서 약속 {sorted(promised)} vs 실제 가능 {sorted(reachable)}"


def _dispatch_source_choices() -> list[str]:
    """``dispatch --source``(캡처 채널)의 허용 값. resolve --source(파일 경로)와 다르다."""
    src = Path(study.__file__).read_text(encoding="utf-8")
    m = re.search(r'dsp\.add_argument\(\s*"--source".*?choices=\[(.*?)\]', src, re.DOTALL)
    assert m, "dispatch --source에 choices가 없다 — 어휘가 코드에 고정돼 있지 않음"
    return re.findall(r'"([a-z]+)"', m.group(1))
