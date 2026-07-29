"""근사중복 SimHash 지문 — stdlib 전용 (U4, #133).

정확 내용해시(sha256)는 **재서술된** 근사중복을 놓친다 — 한 글자만 고쳐도 해시가
완전히 달라진다. SimHash(Charikar)는 토큰 특징을 고정폭 지문으로 접어, 근사중복이
**해밍거리 몇 비트**만 차이 나게 한다. 이 모듈은 그 지문과 해밍거리만 제공한다.

**자문 전용**이다(#133): 근사중복 신호는 트리아지에서 "가능성"으로 보일 뿐, 정확
내용해시 트러스트/dedup 앵커를 절대 대체하지 않는다(SimHash는 근사라 오탐·누락이
있다). 임계(비트폭·해밍 거리)는 검증된 기본값이 없어 실측으로 튜닝한다 — 여기선
64비트 + 보수적 기본 임계를 두되 파라미터로 노출한다.

**알려진 한계 — 짧은 텍스트에서 해밍거리는 분리력이 없다.** 실측 코퍼스(개념
description 21건, 210쌍)에서 같은 주제 그룹 쌍은 해밍 20~37(중앙 27), 다른 그룹 쌍은
19~41(중앙 31)로 범위가 거의 겹쳤고 ``DEFAULT_THRESHOLD`` 이하는 **양쪽 0건**이었다.
SimHash는 원래 긴 문서용이라 토큰이 수십 개뿐인 한 문장에서는 지문이 흩어진다(같은
코퍼스에서 토큰 집합의 자카드 유사도는 같은 그룹 최대 0.19 / 다른 그룹 최대 0.07로
분리됐다). 즉 이 모듈은 지금 **지문·거리 계산으로서는 정상**이지만 임계로 후보를
거르는 용도로는 미달이다 — 지표·임계 재설정은 별건이다.

numpy/scipy 없이 stdlib(`hashlib`·비트연산)만 쓴다 — 플러그인 무의존 계약 준수.
그리고 신 버전 전용 빌트인 API도 쓰지 않는다: `bin/okf-py`는 pyproject가 아니라
시스템에서 python3를 찾아 exec하고(#108) 훅 spawn은 PATH를 보장하지 않아 더 낮은
인터프리터로 떨어질 수 있다(게이트: test_staging_stdlib_gate).
"""

from __future__ import annotations

import hashlib
import re

BITS = 64

# 자문 목록 기본 크기(#306). 예전에는 해밍 임계 3이었는데, **한국어에서 사실상 발화하지
# 않았다** — 실측 재서술 쌍 4개의 거리가 11·27·30·32로 임계 3에 드는 것이 하나도 없다.
# bigram 토큰이라 어순·조사만 바뀌어도 지문이 흩어지는 성질이고, 그 위에 임계를 얹으면
# `near`가 사실상 항상 빈 결과를 낸다. 그 빈 결과가 '무검사'가 아니라 '근사중복 없음'으로
# 읽힌다 — 자문이 조용히 사라진 것이 결과를 낸 것처럼 보이는 꼴이다.
#
# 알고리즘은 그대로 두고 **거리 오름차순 상위 K + 거리 표기**로 바꾼다. 자문 전용이라
# (자동병합 없음) 목록에 오르는 것 자체가 판정이 아니고, 거리는 사람·모델이 본다.
DEFAULT_TOP_K = 5

# 공백이 어절을 가르지 않는 문자군 — 가나(3040-30ff)·한자(3400-4dbf, 4e00-9fff)·
# 한글 음절(ac00-d7af). 리터럴 대신 코드포인트로 적어 편집기·터미널에 의존하지 않는다.
_UNSEGMENTED = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_RUN_RE = re.compile(rf"[0-9a-z]+|[{_UNSEGMENTED}]+")
_UNSEGMENTED_RE = re.compile(rf"^[{_UNSEGMENTED}]+$")


def _tokens(text: str) -> list[str]:
    """어순·구두점 차이에 둔감한 특징 집합.

    공백이 어절을 가르는 문자(영숫자)는 어절 그대로 쓰고, 가르지 않는 문자군은
    **문자 bigram**으로 쪼갠다. 후자를 어절로 두면 조사·어미가 붙어 "스쿼시한다"와
    "스쿼시하지"가 공통 특징 0인 남남이 되어, 정작 주 용도인 재서술 근사중복을
    통째로 놓친다. ASCII 전용 토크나이저였을 때는 더해서 그런 본문이 토큰 0개 →
    지문 0으로 접혀, 무관한 둘이 해밍거리 0으로 보고됐다.
    """
    tokens: list[str] = []
    for run in _RUN_RE.findall(text.lower()):
        if _UNSEGMENTED_RE.match(run) and len(run) > 1:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


def fingerprint(text: str, bits: int = BITS) -> int | None:
    """텍스트의 SimHash 지문(정수). 토큰이 없으면 **판정 불가(None)**.

    예전에는 0을 돌려줬다. 그러면 토크나이저가 다루지 않는 문자군(키릴·그리스 등)의
    스니펫이 전부 지문 0으로 접혀 **서로 해밍거리 0**이 된다 — 무관한 둘이 "같은 지식의
    변주"로 제시된다(실측). 판정 불가를 0이라는 **유효한 값**으로 표현한 것이 원인이라,
    값이 아니라 부재로 돌려준다.
    """
    tokens = _tokens(text)
    if not tokens:
        return None
    vector = [0] * bits
    for token in tokens:
        digest = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16)
        for i in range(bits):
            vector[i] += 1 if (digest >> i) & 1 else -1
    value = 0
    for i in range(bits):
        if vector[i] > 0:
            value |= 1 << i
    return value


def fingerprint_hex(text: str, bits: int = BITS) -> str | None:
    """지문을 16진 문자열로(SQLite 저장용). 판정 불가는 None — 저장도 하지 않는다."""
    value = fingerprint(text, bits)
    return None if value is None else f"{value:0{bits // 4}x}"


def hamming(a: int, b: int) -> int:
    """두 지문의 해밍거리(다른 비트 수).

    ``int.bit_count()``(3.10+)를 쓰지 않는다 — 이 모듈은 `bin/okf-py`가 찾아낸
    시스템 인터프리터에서도 돌아야 한다(모듈 docstring).
    """
    return bin(a ^ b).count("1")
