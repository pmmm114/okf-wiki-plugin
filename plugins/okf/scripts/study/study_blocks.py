"""개념 블록 추출 — 캡처 원자의 단일 정의 (U2, #131).

캡처 원자를 "줄"에서 **개념 블록**으로 올린다. 훅(예전 마지막-줄만)·scan(예전 전-줄)
두 경로가 이 함수 하나를 써서 **동일 후보 집합**을 산출한다(불일치 회귀 차단).

블록 경계 규칙:
- **헤딩**(``^\\s*#``)·**빈 줄**은 구분자다(내용 아님, 블록을 닫는다).
- **최상위 불릿**(``^[*+-]\\s+``, 들여쓰기 없음)은 새 블록을 연다.
- **들여쓴 줄**(하위 불릿·연속)·이어지는 비-불릿 내용 줄은 현재 블록에 붙는다.
- 블록 없는 상태의 비-불릿 내용 줄은 새 블록을 연다(산문 문단).

각 블록은 **줄 리스트**(불릿 마커 제거)다. 블록 텍스트는 줄을 공백으로 이은 것이고,
그 내용해시가 후보 id다. 개별 **줄-해시**는 v0.4.x 줄-후보 해시와 동일하게(불릿 제거
후 sanitize) 계산돼 ledger 연속성(A2′ 자식 병존)을 잇는다.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^\s*#")
_TOP_BULLET_RE = re.compile(r"^[*+-]\s+")  # 들여쓰기 없는 최상위 불릿
_BULLET_STRIP_RE = re.compile(r"^[*+-]\s+")  # 줄 앞 불릿 마커 제거(정규화)


def _strip_bullet(line: str) -> str:
    """줄 앞뒤 공백을 다듬고 불릿 마커를 제거한다(v0.4.x 줄-후보와 동일 정규화)."""
    return _BULLET_STRIP_RE.sub("", line.strip())


def concept_blocks(text: str) -> list[list[str]]:
    """텍스트를 개념 블록(각각 불릿-제거된 줄 리스트)으로 나눈다."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or _HEADING_RE.match(raw):
            current = None  # 빈 줄·헤딩은 현재 블록을 닫는다
            continue
        stripped = _strip_bullet(raw)
        if not stripped:
            continue
        is_top_bullet = bool(_TOP_BULLET_RE.match(raw))  # 원본 기준(들여쓰기 없는 불릿)
        if is_top_bullet or current is None:
            current = [stripped]  # 새 블록: 최상위 불릿 또는 문단 첫 줄
            blocks.append(current)
        else:
            current.append(stripped)  # 들여쓴 하위 불릿·산문 연속 줄
    return blocks
