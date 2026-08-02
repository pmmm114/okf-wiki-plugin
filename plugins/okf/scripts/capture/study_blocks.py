"""개념 블록 추출 — 캡처 원자의 단일 정의 (U2, #131 · 노이즈 필터 #256).

캡처 원자를 "줄"에서 **개념 블록**으로 올린다. 훅(예전 마지막-줄만)·scan(예전 전-줄)
두 경로가 이 함수 하나를 써서 **동일 후보 집합**을 산출한다(불일치 회귀 차단).

블록 경계 규칙:
- **헤딩**(``^\\s*#``)·**빈 줄**·**bare 수평선**(``---``)은 구분자다(내용 아님, 블록을 닫는다).
- **최상위 불릿**(``^[*+-]\\s+``, 들여쓰기 없음)은 새 블록을 연다.
- **들여쓴 줄**(하위 불릿·연속)·이어지는 비-불릿 내용 줄은 현재 블록에 붙는다.
- 블록 없는 상태의 비-불릿 내용 줄은 새 블록을 연다(산문 문단).
- **코드 펜스**(백틱 3+ 또는 ``~~~``) 안에서는 위 해석을 전부 억제한다(#354) — 내용
  줄은 현재 블록에 붙고(없으면 자체 블록), 마커 줄은 마크업이라 콘텐츠에서 제외한다.
  닫는 마커는 같은 문자 계열만 인정하는 단순 상태기계이고 미폐쇄 펜스는 파일 끝까지다.
  불릿에 싸인 마커는 펜스로 보지 않는다(산문 해석 유지).

경계 이력(#354): 펜스 인지로 펜스 포함 문서의 블록 id가 바뀌었다. 원장은 원문 없는
해시뿐이라 재해시 이행이 불가능하므로 새 경계는 **신규 캡처부터만**이고, 구 경계
후보·원장 기록은 자연 소진으로 잔존한다(1회성 공존 churn — 실측 수치는 #354 PR).

노이즈 필터(#256) — 구조 보일러플레이트는 후보가 아니다:
- **파일 선두 frontmatter 펜스**는 **위치 기준**으로 스킵한다. 텍스트 패턴 판정은
  닫는 펜스에 빈 줄 없이 붙은 본문(실측 재현)을 통째로 오폭하므로 쓰지 않는다.
- **라벨-단독 블록**은 고정 셋(``_NOISE_LABELS``, 콜론이 볼드 안/밖인 변형 포함)만
  제외한다 — 일반 휴리스틱(콜론 종결·볼드 단독)은 실사실을 오폭해 기각(실측).
  #352 재검증에서도 같은 결론: 콜론 종결 단독 35건 중 "빌드 불가 (확정):" 같은
  **정보를 담은 라벨**이 섞여 있어 일반 규칙은 재기각.
- **코드 조각 단독 블록**(펜스 마커 ``\\`\\`\\`tsx``·닫는 태그 ``</X>``)은 고정 패턴으로
  제외한다(#352 — 실코퍼스 2,759건 전수에서 매치 7건 전부 노이즈, 위양성 0).
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
_RULE_RE = re.compile(r"^-{3,}\s*$")  # bare 수평선 — **최상위만**(들여쓴 ---는 블록 내용:
# 다중 줄 블록을 중간에서 쪼개면 블록 id가 바뀌어 기존 인박스·원장 dedup과 어긋난다)
_NOISE_LABELS = frozenset({"why", "how to apply"})  # 라벨-단독 고정 셋(#256, 실측 위양성 0)
# 코드 조각 단독 줄(#352) — 펜스 마커(```tsx)·닫는 태그(</X>). 블록 전체가 이것뿐일 때만
# 노이즈다(fullmatch) — 본문 안 백틱·태그 언급은 건드리지 않는다.
_FENCE_MARK_RE = re.compile(r"(?:`{3,}|~{3,})[\w.+-]*")
_CLOSING_TAG_RE = re.compile(r"</[A-Za-z][\w.-]*>")
# 펜스 상태 전환(#354) — 들여쓴 마커는 인정, 불릿 뒤 마커는 산문(concept_blocks 참조).
_FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _strip_bullet(line: str) -> str:
    """줄 앞뒤 공백을 다듬고 불릿 마커를 제거한다(v0.4.x 줄-후보와 동일 정규화)."""
    return _BULLET_STRIP_RE.sub("", line.strip())


def _label_key(line: str) -> str:
    """볼드·이탤릭 마커(개수 무관)와 꼬리 콜론 변형을 벗긴 라벨 본문(소문자).

    ``**X:**``·``**X**:``·``***X:***``·``*X*`` 전부 같은 키로 정규화된다 — 판정은
    고정 셋 멤버십이라 마커를 공격적으로 벗겨도 실사실 라벨은 셋에 없어 안전하다.
    """
    key = line.strip().strip("*").strip()
    key = key.removesuffix(":").rstrip()
    return key.strip("*").strip().lower()


def effective_labels(declared) -> frozenset[str]:
    """유효 노이즈 라벨 셋(#370) — 내장 ∪ 소비처 선언(``_label_key`` 정규화 후).

    **additive만이다** — 선언으로 내장을 끌 수 없다(무음 캡처 억제 방지). 어휘는
    소비처(``.okf-wiki.json``의 ``study.noiseLabels``)가 선언하고, 규칙(라벨-단독
    블록의 정확 일치 제외 — 휴리스틱 없음)은 이 모듈이 소유한다. 가시화는 doctor.
    """
    extra = frozenset(_label_key(v) for v in (declared or []) if isinstance(v, str) and v.strip())
    return _NOISE_LABELS | extra


# frontmatter 안쪽이 **YAML 매핑꼴**인지 — 위치만으로 단정하지 않기 위한 확인(#305).
# 허용: `키: 값` · 들여쓴 연속(리스트 항목·중첩) · 주석 · 빈 줄.
_YAML_KEY_RE = re.compile(r"^[A-Za-z_][\w.\-]*\s*:")
_YAML_CONT_RE = re.compile(r"^(?:\s+\S|\s*#|\s*$)")


def _looks_like_frontmatter(inner: list[str]) -> bool:
    """펜스 안쪽이 YAML 매핑으로 읽히는가 — 키가 하나는 있고, 이질적 줄이 없어야 한다."""
    if not any(_YAML_KEY_RE.match(line) for line in inner):
        return False
    return all(_YAML_KEY_RE.match(line) or _YAML_CONT_RE.match(line) for line in inner)


def _body_lines(text: str) -> list[str]:
    """파일 선두 frontmatter 펜스를 걷어낸 본문 줄들(#256 · #305).

    1행이 ``---``이고 닫는 펜스(``---``/``...``)가 있으며 **안쪽이 YAML 매핑꼴**일 때만
    스킵한다. 닫는 펜스가 없으면 frontmatter가 아니므로 전체를 본문으로 남긴다(보수적).

    매핑꼴 확인이 없던 시절엔 위치만으로 단정했다. 그러면 **수평선을 구분자로 쓰는**
    메모리 파일에서 첫 구간이 통째로 삼켜진다 — 선두 ``---`` 다음에 ``---``나 ``...``가
    한 번 더 나오면 그 사이가 frontmatter로 취급됐다(실측). 선두 ``---`` 하나만으로는
    소실되지 않았기 때문에 발현 조건이 좁고 조용했다.

    **블록 분할·id 계산은 건드리지 않는다** — 경계가 바뀌면 기존 인박스·원장 dedup과
    어긋난다. 여기서 정밀화하는 것은 펜스 판정 하나뿐이다.
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("﻿"):
        lines[0] = lines[0].lstrip("﻿")  # UTF-8 BOM 파일에서 펜스 판정 무력화 방지
    if lines and _FENCE_OPEN_RE.match(lines[0]):
        for j in range(1, len(lines)):
            if _FENCE_CLOSE_RE.match(lines[j]):
                return lines[j + 1 :] if _looks_like_frontmatter(lines[1:j]) else lines
    return lines


def concept_blocks(text: str, labels: frozenset[str] | None = None) -> list[list[str]]:
    """텍스트를 개념 블록(각각 불릿-제거된 줄 리스트)으로 나눈다.

    ``labels``는 라벨-단독 노이즈 판정의 유효 셋(``effective_labels``) — 미지정이면
    내장 셋만(#370, 하위호환).
    """
    if labels is None:
        labels = _NOISE_LABELS
    blocks: list[list[str]] = []
    current: list[str] | None = None
    fence: str | None = None  # 열린 펜스의 문자 — 안에서는 구분자 해석을 억제한다(#354)
    for raw in _body_lines(text):
        marker = _FENCE_LINE_RE.match(raw)
        if fence is not None:
            if marker and marker.group(1)[0] == fence:
                fence = None  # 닫는 마커 — 마커 줄은 마크업이라 콘텐츠 제외
                continue
            code = raw.strip()  # 코드는 불릿 해석 없이 원문 유지(양끝 공백만 정돈)
            if not code:
                continue  # 펜스 안 빈 줄은 블록을 닫지 않는다
            if current is None:
                current = [code]  # 앞 산문 없는 순수 코드 펜스는 자체 블록
                blocks.append(current)
            else:
                current.append(code)  # 코드는 앞 생각의 재료다 — 블록 연속
            continue
        if marker:
            fence = marker.group(1)[0]
            continue
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
    # 라벨-단독·코드 조각 단독 블록은 후보가 아니다 — 라벨 내용은 뒤따르는 블록이
    # 이미 따로 갖고, 펜스 마커·닫는 태그는 지식이 아니라 마크업 잔재다(#352).
    return [b for b in blocks if not (len(b) == 1 and _is_structural_noise(b[0], labels))]


def _is_structural_noise(line: str, labels: frozenset[str]) -> bool:
    """단독 줄이 구조 잔재인가 — 유효 라벨 셋 또는 코드 조각 고정 패턴(fullmatch)."""
    stripped = line.strip()
    return (
        _label_key(line) in labels
        or bool(_FENCE_MARK_RE.fullmatch(stripped))
        or bool(_CLOSING_TAG_RE.fullmatch(stripped))
    )


def is_noise_snippet(snippet: str, labels: frozenset[str] | None = None) -> bool:
    """이미 적재된 후보의 노이즈 판정 — **prune 전용** 텍스트 근사(#256).

    영구 필터는 위치(선두 펜스) 기준으로 추출 단계에서 걸러지지만, DB의 스니펫엔
    위치 정보가 없어 조인 형태(``--- `` 접두 = frontmatter 블록)로 근사한다 —
    실측 코퍼스 위양성 0. diff 헤더 인용을 일상 캡처하는 코퍼스라면 prune 결과
    목록을 검토하고 쓰라. ``labels``는 추출 필터와 같은 유효 라벨 셋(#370).
    """
    if labels is None:
        labels = _NOISE_LABELS
    joined = " ".join(str(snippet).split())
    if re.fullmatch(r"-{3,}", joined):
        return True
    if joined.startswith("--- "):
        return True
    # 코드 조각 단독(#352) — 저장 스니펫 2,759건 전수 실측에서 매치 7건(펜스 4·태그 3)
    # 전부 노이즈, 위양성 0. 추출 필터와 같은 fullmatch 패턴이라 기적재 잔재를 잇는다.
    return _is_structural_noise(joined, labels)
