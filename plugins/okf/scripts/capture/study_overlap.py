"""근사중복 어휘 겹침 — BM25 대칭 평균, stdlib 전용 (#392).

정확 내용해시(sha256)는 **재서술된** 근사중복을 놓친다 — 한 글자만 고쳐도 해시가
완전히 달라진다. 이 모듈은 두 후보의 **어휘가 얼마나 겹치는지**를 재서 가까운 순으로
줄 세운다. 앞서 쓰던 SimHash를 대체한다(#387 결정 게이트 규칙 1).

**어휘 겹침은 의미 유사도가 아니다.** 실측(#387, 실제 코퍼스 IDF)에서 의미가 정반대인
쌍이 같은 의미인 쌍보다 5~7배 높은 점수를 받았다 — "캡처 원자는 개념 블록이고 줄이
아니다"와 "캡처 원자는 줄이고 개념 블록이 아니다"는 토큰 집합이 같아 최고점이 나오고,
"번들 검증은 엄격 모드로"와 "지식 저장소 확인은 strict 옵션을"은 같은 말인데 0점이다.
그래서 필드 이름이 ``similarity``가 아니라 ``overlap``이다 — 무엇을 쟀는지 그대로
부르지 않으면 읽는 쪽이 판정 근거로 삼는다. 이 지표가 하는 일은 "같은 지식인가"를
답하는 것이 아니라, 모델이 **읽어 볼 K개** 안에 진짜가 들게 하는 것뿐이다.

**임계로 자르지 않는다**(#306 유지). 점수가 높을수록 의미가 같다는 관계 자체가 없어
어떤 값으로 잘라도 진짜와 가짜가 섞인다 — #306 재서술 쌍 4건에서도 같은 쌍의 최솟값이
무관 쌍의 최댓값보다 낮았다. 순위(상위 K)와 값 표기만 낸다.

다만 **공통 토큰이 하나도 없는 상대는 목록에 오르지 않는다.** 겹침이 0이면 보여 줄 것이
없고, 무관한 후보로 K를 채우면 사람이 읽어야 할 줄만 늘어난다 — SimHash가 모든 쌍에
거리를 매겨 목록을 채우던 것과 갈리는 지점이다. 이것은 임계가 아니다(자를 값이 없다).
실무에서는 이 상황이 사실상 오지 않는다 — 실측 후보 2,198건 전부가 상위 5를 채웠다.
한국어 bigram은 조사·어미가 흔해 어떤 두 문장이든 무언가는 겹치기 때문이다.

왜 BM25인가(#387 실측, 질의 207건·풀 2318건, 자연 라벨 recall):

    지표          R@1     R@5     상위5 노이즈
    bm25-mean    0.841   1.000      46.2%
    jaccard      0.754   0.952      50.9%
    simhash      0.164   0.406      89.9%   ← 이전 구현

자카드보다 나은 것은 두 가지를 더 하기 때문이다. 흔한 토큰의 기여를 IDF로 낮추고(그래서
"하는"·"있다" 같은 조각이 겹쳐도 점수가 오르지 않는다), 문서 길이를 정규화한다(후보
토큰 수가 6에서 1680까지 퍼져 있어 이쪽이 특히 크게 작용한다).

**대칭 평균을 쓴다.** 이 비교는 검색(짧은 질의 → 긴 문서)이 아니라 후보 A와 B 중 누가
질의인지 정해져 있지 않은 대칭 관계다. 비대칭 BM25는 실측에서 A의 상위 K에 B가 있을 때
B의 상위 K에도 A가 있는 비율이 65.3%였다 — 일괄 뷰에서 한쪽만 뜨는 쌍이 3분의 1이면
목록으로 읽기 어렵다.

**점수는 코퍼스에 의존한다.** IDF가 "이 토큰이 몇 개 후보에 나오는가"라서 후보가 늘거나
줄면 같은 쌍의 점수도 달라진다. 그래서 미리 계산해 저장할 수 없고 질의 시 계산이
필연이다 — 이전 구현이 지문을 저장해 생긴 소급 결함(저장분과 원문 재계산이 어긋난 후보
105건)이 구조적으로 사라진다. 같은 이유로 점수를 절대값으로 인용하면 안 된다.

numpy/scipy 없이 stdlib(``math``·``collections``·``heapq``)만 쓴다 — 플러그인 무의존
계약 준수(게이트: test_staging_stdlib_gate). 신 버전 전용 빌트인도 쓰지 않는다:
`bin/okf-py`는 pyproject가 아니라 시스템에서 python3를 찾아 exec하고(#108) 훅 spawn은
PATH를 보장하지 않아 더 낮은 인터프리터로 떨어질 수 있다(실측 환경 3.9.6).
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict

# 자문 목록 기본 크기(#306). 임계 필터가 아니라 **거리 오름차순 상위 K**인 이유는
# 한국어에서 임계가 사실상 발화하지 않았기 때문이다 — 빈 결과가 '무검사'가 아니라
# '근사중복 없음'으로 읽혔다. 지표를 BM25로 바꿔도 이 결론은 유지된다(#387).
DEFAULT_TOP_K = 5

# BM25 표준 기본값(Robertson-Spärck Jones 계열). 튜닝하지 않는다 — 이 코퍼스에
# 맞춰 깎으면 자문이 특정 문서 형태에 과적합되고, 자문 전용이라 그 이득이 작다.
K1 = 1.5  # 용어 빈도 포화 속도(같은 토큰이 10번 나와도 10배 기여하지 않는다)
B = 0.75  # 문서 길이 정규화 강도

# 공백이 어절을 가르지 않는 문자군 — 가나(3040-30ff)·한자(3400-4dbf, 4e00-9fff)·
# 한글 음절(ac00-d7af). 리터럴 대신 코드포인트로 적어 편집기·터미널에 의존하지 않는다.
_UNSEGMENTED = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_RUN_RE = re.compile(rf"[0-9a-z]+|[{_UNSEGMENTED}]+")
_UNSEGMENTED_RE = re.compile(rf"^[{_UNSEGMENTED}]+$")


def tokens(text: str) -> list[str]:
    """어순·구두점 차이에 둔감한 특징 목록(빈도 유지 — BM25가 TF를 쓴다).

    공백이 어절을 가르는 문자(영숫자)는 어절 그대로 쓰고, 가르지 않는 문자군은
    **문자 bigram**으로 쪼갠다. 후자를 어절로 두면 조사·어미가 붙어 "스쿼시한다"와
    "스쿼시하지"가 공통 특징 0인 남남이 되어, 정작 주 용도인 재서술 근사중복을
    통째로 놓친다.

    자모 분해(4-gram)는 실측에서 기각됐다(#387) — 같은 의미 쌍의 개선이 미미한 반면
    (0.056 → 0.081) 반대 의미 쌍은 오히려 올랐다(0.714 → 0.833). 잘게 쪼개면 우연한
    겹침이 함께 늘기 때문이다.
    """
    out: list[str] = []
    for run in _RUN_RE.findall(text.lower()):
        if _UNSEGMENTED_RE.match(run) and len(run) > 1:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            out.append(run)
    return out


def build_index(texts: dict[str, str]) -> dict:
    """코퍼스 전체에서 역색인·IDF·길이 정규화 항을 만든다.

    역색인을 쓰는 이유는 비용이다. 질의 토큰을 가진 후보만 보므로 전량 쌍대 순회가
    없다 — 실측(2324건, 3.9.6)에서 색인 구축 0.07초에 일괄 뷰 3.86초로, 이전 SimHash
    전량 쌍대(3.04초)와 같은 자릿수다.

    토큰이 하나도 없는 후보(**판정 불가**)는 색인에 들이지 않는다. 이전 구현이 이런
    텍스트의 지문을 0으로 접어 무관한 둘을 거리 0으로 묶었던 자리다(#306) — 값이
    아니라 부재로 다룬다.
    """
    tok = {}
    for ident, text in texts.items():
        t = tokens(text)
        if t:  # 토큰 0개는 판정 불가 — 색인 제외
            tok[ident] = t
    n = len(tok)
    postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
    tf = {}
    for ident, t in tok.items():
        counter = Counter(t)
        tf[ident] = counter
        for term, freq in counter.items():
            postings[term].append((ident, freq))
    avgdl = (sum(len(t) for t in tok.values()) / n) if n else 0.0
    idf = {term: math.log(1 + n / (1 + len(plist))) for term, plist in postings.items()}
    norm = {ident: K1 * (1 - B + B * len(t) / avgdl) for ident, t in tok.items()} if avgdl else {}
    return {"tf": tf, "postings": dict(postings), "idf": idf, "norm": norm}


def _forward(index: dict, ident: str) -> dict[str, float]:
    """``ident``를 질의로 본 한 방향 점수 ``{상대: score}`` — 대칭화 전 재료."""
    tf = index["tf"]
    if ident not in tf:
        return {}
    postings, idf, norm = index["postings"], index["idf"], index["norm"]
    acc: dict[str, float] = defaultdict(float)
    for term in tf[ident]:
        weight = idf.get(term, 0.0)
        for other, freq in postings.get(term, ()):
            if other != ident:
                acc[other] += weight * freq * (K1 + 1) / (freq + norm[other])
    return acc


def _score(index: dict, query: str, doc: str) -> float:
    """``query -> doc`` 한 쌍의 점수. 역색인을 돌지 않고 질의 토큰만 훑는다."""
    tf = index["tf"]
    if query not in tf or doc not in tf:
        return 0.0
    tf_doc, idf, norm = tf[doc], index["idf"], index["norm"]
    total = 0.0
    for term in tf[query]:
        freq = tf_doc.get(term)
        if freq:
            total += idf.get(term, 0.0) * freq * (K1 + 1) / (freq + norm[doc])
    return total


def _top(merged: dict[str, float], top_k: int) -> list[dict]:
    """상위 K — 점수 내림차순, 동률은 id 오름차순으로 **결정적**.

    ``(-score, id)`` 튜플을 최소 힙에 넣으면 그만이다. id 형식을 가정하지 않는다 —
    앞선 SimHash 최적화 검토(#386)가 정수 거리 때문에 hex 12자를 가정해야 했던 자리와
    갈리는 지점이다.
    """
    ranked = heapq.nsmallest(top_k, ((-score, ident) for ident, score in merged.items()))
    return [{"id": ident, "overlap": round(-neg, 4)} for neg, ident in ranked]


def nearest(index: dict, ident: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """``ident``와 가까운 **상위 K** — ``[{id, overlap}]``, 겹침 내림차순.

    단건 질의용이다. 대칭 평균이라 역방향도 필요하지만, 한 건이면 상대별로 **그 쌍만**
    거꾸로 재는 것이 전치 인덱스를 통째로 만드는 것보다 싸다. 여러 후보를 한 번에 볼
    때는 `nearest_all`을 쓴다 — 거기서는 역방향이 이미 계산되어 있다.
    """
    fwd = _forward(index, ident)
    if not fwd:
        return []
    merged = {other: (score + _score(index, other, ident)) / 2 for other, score in fwd.items()}
    return _top(merged, top_k)


def nearest_all(
    index: dict, idents: list[str], top_k: int = DEFAULT_TOP_K
) -> dict[str, list[dict]]:
    """``idents`` 각각의 상위 K — ``{id: [{id, overlap}]}``, 빈 결과는 뺀다.

    `nearest`와 계약이 같고 다른 것은 **역방향을 재계산하지 않는다**는 것뿐이다.
    각 후보가 한 번씩 질의가 되므로 반대 방향 점수는 그 패스에서 이미 나왔다 — 전치로
    재사용한다. 실측(2324건, 3.9.6): 질의마다 전체를 훑는 순진한 구현 4.85초, 전치
    3.86초(−20%). "채우면서 함께 넣기"(동시 적재)는 4.41초로 오히려 느렸다 — dict 쓰기가
    전치 패스 한 번보다 비싸다.

    #386이 SimHash에서 같은 재사용을 검토했으나 30줄 복잡도와 id 형식 가정(hex 12자)
    때문에 사지 않았다. 실수 점수에서는 그 대가가 사라진다(`_top` 주석).
    """
    fwd = {ident: _forward(index, ident) for ident in index["tf"]}
    rev: dict[str, dict[str, float]] = defaultdict(dict)
    for ident, acc in fwd.items():
        for other, score in acc.items():
            rev[other][ident] = score  # rev[b][a] == score(a -> b)
    pairs = {}
    for ident in idents:
        f = fwd.get(ident, {})
        r = rev.get(ident, {})
        if not f and not r:
            continue
        merged = {o: (f.get(o, 0.0) + r.get(o, 0.0)) / 2 for o in set(f) | set(r)}
        top = _top(merged, top_k)
        if top:
            pairs[ident] = top
    return pairs
