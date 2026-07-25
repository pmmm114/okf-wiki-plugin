"""개념 블록 추출 — 캡처 원자의 단일 정의 (U2, #131 · 노이즈 필터 #256).

캡처 원자를 "줄"에서 **개념 블록**으로 올린다. 훅(예전 마지막-줄만)·scan(예전 전-줄)
두 경로가 이 함수 하나를 써서 **동일 후보 집합**을 산출한다(불일치 회귀 차단).

블록 경계 규칙:
- **헤딩**(``^\\s*#``)·**빈 줄**·**bare 수평선**(``---``)은 구분자다(내용 아님, 블록을 닫는다).
- **최상위 불릿**(``^[*+-]\\s+``, 들여쓰기 없음)은 새 블록을 연다.
- **들여쓴 줄**(하위 불릿·연속)·이어지는 비-불릿 내용 줄은 현재 블록에 붙는다.
- 블록 없는 상태의 비-불릿 내용 줄은 새 블록을 연다(산문 문단).

노이즈 필터(#256) — 구조 보일러플레이트는 후보가 아니다:
- **파일 선두 frontmatter 펜스**는 **위치 기준**으로 스킵한다. 텍스트 패턴 판정은
  닫는 펜스에 빈 줄 없이 붙은 본문(실측 재현)을 통째로 오폭하므로 쓰지 않는다.
- **라벨-단독 블록**은 고정 셋(``_NOISE_LABELS``, 콜론이 볼드 안/밖인 변형 포함)만
  제외한다 — 일반 휴리스틱(콜론 종결·볼드 단독)은 실사실을 오폭해 기각(실측).
- 이미 적재된 후보의 정리는 텍스트 근사인 ``is_noise_snippet``(prune 전용)이 맡는다 —
  영구 필터(위치 기준)와 판정 축이 다르다.

각 블록은 **줄 리스트**(불릿 마커 제거)다. 블록 텍스트는 줄을 공백으로 이은 것이고,
그 내용해시가 후보 id다. 개별 **줄-해시**는 v0.4.x 줄-후보 해시와 동일하게(불릿 제거
후 sanitize) 계산돼 ledger 연속성(A2′ 자식 병존)을 잇는다.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^\s*#")
_TOP_BULLET_RE = re.compile(r"^[*+-]\s+")  # 들여쓰기 없는 최상위 불릿
_BULLET_STRIP_RE = re.compile(r"^[*+-]\s+")  # 줄 앞 불릿 마커 제거(정규화)
_FENCE_OPEN_RE = re.compile(r"^---\s*$")  # frontmatter 여는 펜스(파일 1행 전용)
_FENCE_CLOSE_RE = re.compile(r"^(?:---|\.\.\.)\s*$")  # 닫는 펜스(YAML은 ...도 허용)
_RULE_RE = re.compile(r"^\s*-{3,}\s*$")  # bare 수평선 — 내용을 가질 수 없는 줄
_NOISE_LABELS = frozenset({"why", "how to apply"})  # 라벨-단독 고정 셋(#256, 실측 위양성 0)


def _strip_bullet(line: str) -> str:
    """줄 앞뒤 공백을 다듬고 불릿 마커를 제거한다(v0.4.x 줄-후보와 동일 정규화)."""
    return _BULLET_STRIP_RE.sub("", line.strip())


def _label_key(line: str) -> str:
    """볼드 마커·꼬리 콜론 변형(``**X:**``/``**X**:``)을 벗긴 라벨 본문(소문자)."""
    key = line.strip()
    for _ in range(2):  # 콜론이 볼드 안/밖 어느 쪽이든 두 바퀴면 정규화된다
        key = key.removesuffix(":").rstrip()
        if key.startswith("**") and key.endswith("**") and len(key) > 4:
            key = key[2:-2].strip()
    return key.lower()


def _body_lines(text: str) -> list[str]:
    """파일 선두 frontmatter 펜스를 위치 기준으로 걷어낸 본문 줄들(#256).

    1행이 ``---``이고 닫는 펜스(``---``/``...``)가 있으면 안쪽을 통째로 스킵한다.
    닫는 펜스가 없으면 frontmatter가 아니므로 전체를 본문으로 남긴다(보수적).
    """
    lines = text.splitlines()
    if lines and _FENCE_OPEN_RE.match(lines[0]):
        for j in range(1, len(lines)):
            if _FENCE_CLOSE_RE.match(lines[j]):
                return lines[j + 1 :]
    return lines


def concept_blocks(text: str) -> list[list[str]]:
    """텍스트를 개념 블록(각각 불릿-제거된 줄 리스트)으로 나눈다."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for raw in _body_lines(text):
        if not raw.strip() or _HEADING_RE.match(raw) or _RULE_RE.match(raw):
            current = None  # 빈 줄·헤딩·수평선은 현재 블록을 닫는다
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
    # 라벨-단독 블록은 후보가 아니다 — 내용은 뒤따르는 블록이 이미 따로 갖는다
    return [b for b in blocks if not (len(b) == 1 and _label_key(b[0]) in _NOISE_LABELS)]


def is_noise_snippet(snippet: str) -> bool:
    """이미 적재된 후보의 노이즈 판정 — **prune 전용** 텍스트 근사(#256).

    영구 필터는 위치(선두 펜스) 기준으로 추출 단계에서 걸러지지만, DB의 스니펫엔
    위치 정보가 없어 조인 형태(``--- `` 접두 = frontmatter 블록)로 근사한다 —
    실측 코퍼스 위양성 0. diff 헤더 인용을 일상 캡처하는 코퍼스라면 prune 결과
    목록을 검토하고 쓰라.
    """
    joined = " ".join(str(snippet).split())
    if re.fullmatch(r"-{3,}", joined):
        return True
    if joined.startswith("--- "):
        return True
    return _label_key(joined) in _NOISE_LABELS
