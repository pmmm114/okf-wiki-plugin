"""근사중복 SimHash 지문 — stdlib 전용 (U4, #133).

정확 내용해시(sha256)는 **재서술된** 근사중복을 놓친다 — 한 글자만 고쳐도 해시가
완전히 달라진다. SimHash(Charikar)는 토큰 특징을 고정폭 지문으로 접어, 근사중복이
**해밍거리 몇 비트**만 차이 나게 한다. 이 모듈은 그 지문과 해밍거리만 제공한다.

**자문 전용**이다(#133): 근사중복 신호는 트리아지에서 "가능성"으로 보일 뿐, 정확
내용해시 트러스트/dedup 앵커를 절대 대체하지 않는다(SimHash는 근사라 오탐·누락이
있다). 임계(비트폭·해밍 거리)는 검증된 기본값이 없어 실측으로 튜닝한다 — 여기선
64비트 + 보수적 기본 임계를 두되 파라미터로 노출한다.

numpy/scipy 없이 stdlib(`hashlib`·비트연산)만 쓴다 — 플러그인 무의존 계약 준수.
"""

from __future__ import annotations

import hashlib
import re

BITS = 64
DEFAULT_THRESHOLD = 3  # 64비트 지문의 보수적 기본 해밍 임계(검증된 값 아님 — 실측 튜닝)

_TOKEN_RE = re.compile(r"[0-9a-z]+")


def _tokens(text: str) -> list[str]:
    """소문자 영숫자 토큰 — 어순·구두점 차이에 둔감한 특징 집합."""
    return _TOKEN_RE.findall(text.lower())


def fingerprint(text: str, bits: int = BITS) -> int:
    """텍스트의 SimHash 지문(정수). 토큰이 없으면 0."""
    tokens = _tokens(text)
    if not tokens:
        return 0
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


def fingerprint_hex(text: str, bits: int = BITS) -> str:
    """지문을 16진 문자열로(SQLite 저장용 — 64비트 부호 오버플로 회피)."""
    return f"{fingerprint(text, bits):0{bits // 4}x}"


def hamming(a: int, b: int) -> int:
    """두 지문의 해밍거리(다른 비트 수)."""
    return (a ^ b).bit_count()
