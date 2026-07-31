"""context — 주입: 세션에 넣을 압축 지식 인덱스를 만든다 (T-P2-4).

개념 문서당 한 줄 ``<경로> [<type>] — <핵심 값>`` 형식으로 압축한다. 핵심 값은
frontmatter description, 없으면 본문 첫 표 행·첫 문장에서 추출. 결과는
``<okf-context>...</okf-context>``로 감싸고, 절단 기준은 **문자 수만**
(``--max-chars``, 기본 8000 — 훅 10,000자 한도 마진). 개념 수 절단(maxConcepts류)은
재도입 금지 — 폐기 확정 안티패턴.

축 투영(``--group-by KEY``)·필터(``--filter KEY=VALUE``)는 **임의 frontmatter 키**를
받는다 — 엔진은 특정 축 이름·값 어휘를 모른다(taxonomy-neutral). 축 해석은
``axis_values`` **공유 표면 하나**다(census와 동일 규칙 — 해석 사본 금지, #329).
필터는 리스트 축을 멤버 일치로 전개한다. 그룹은 값 알파벳순, 미기재는
``(unclassified)``로 맨 뒤 — 다중값 축(리스트 혼재 포함)은 묶지 않고 무플래그와
동일하게 내며 진단은 stderr로 낸다(거부는 훅 경로에서 주입 전무가 되는 순회귀).
무플래그 출력은 바이트 불변. ``--outline``은 전량 목록 대신 축 윤곽(#336) —
개념 수 무관 크기라 절단이 없고, 세부는 ``okf query`` 몫이다.
"""

from __future__ import annotations

import argparse
import datetime
import posixpath
import sys
from pathlib import Path

from okf_core.parser import ParsedDoc, walk_bundle

RESERVED = frozenset({"index.md", "log.md"})
DEFAULT_MAX_CHARS = 8000
_OPEN = "<okf-context>"
_CLOSE = "</okf-context>"
_GIST_MAX = 160


def gist(doc: ParsedDoc) -> str:
    """개념 1줄 요약 — description, 없으면 본문 첫 표 행·첫 문장(공유 표면).

    **1줄이라는 계약을 description 경로에서도 지킨다.** `description`은 스펙상 자유
    텍스트라 YAML 블록 스칼라로 다중행이 될 수 있는데(엔진은 taxonomy-neutral이라
    validate가 막지 않는다), 그것을 그대로 흘리면 렌더 한 줄이 여러 줄로 벌어진다.
    소비자는 이 렌더를 줄 단위로 파싱하므로 개행 하나가 곧 가짜 섹션·유령 항목이 된다.
    본문 폴백 경로는 원래 첫 문장·길이 절단으로 이 계약을 지키고 있었다.
    """
    fm = doc.frontmatter or {}
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        return " ".join(desc.split())[:_GIST_MAX]
    # 본문에서 추출: 첫 표 행 또는 헤딩이 아닌 첫 문장
    for line in doc.body.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```") or s.startswith("~~~"):
            continue
        if not s.startswith("|"):
            cut = s.find(". ")
            if cut != -1:
                s = s[: cut + 1]
        return s[:_GIST_MAX]
    return ""


_UNCLASSIFIED = "(unclassified)"

KIND_STR = "str"
KIND_LIST = "list"
KIND_DATE = "date"
KIND_NUM = "num"
KIND_OTHER = "other"


def axis_values(doc: ParsedDoc, key: str) -> tuple[tuple[str, ...], str | None]:
    """(그 개념이 이 축에 가진 값들, 값 종류|None) — 키 부재는 ``((), None)``.

    축 해석의 **공유 표면** — census(관측)·context(필터·그룹)가 이 규칙 하나를
    쓴다(#329, 불변식 게이트). 값 종류만 보고 어휘는 보지 않는다. 판정 표(#330):

    - str → 값 1개. 문자열 리스트 → 멤버 전개(중복 제거·정렬) — 통째로 "미기재"로
      접히면 채워진 어휘가 관측에서 사라진다
    - date·datetime → ``isoformat()`` 값 1개. ISO 8601 정본이고 **동일 오프셋
      안에서는** 코드포인트순 정렬이 시간순과 일치한다(오프셋이 섞인 축은 보장
      없음 — DA 실측 반례: ``+00:00``와 ``+09:00`` 혼재). ``str()``은 ``T``
      구분자를 잃는다. 값 정본은 isoformat
      그대로(#330 (a)) — ``Z``→``+00:00``은 의미 동일 정규화이고, 원문 표기 보존은
      ParsedDoc이 frontmatter 원문을 따로 들어야 해서 기각됐다
    - int·float → ``str()`` 값 1개(왕복 무손실, bool 제외)
    - bool·null·매핑 등 → 값 0개(KIND_OTHER). 2값 축은 분류 정보가 없고, null은
      부재와 구분 불가한 값 문자열을 만들며, 매핑은 축 어휘가 아니다

    이 표를 rules 데이터에 두지 않는다(#330 검토 결과): rules는 §9 판정 상수(번들
    언어)의 단일 원천이고 타입→정규화는 파이썬 실행 표현의 문제라 층이 다르다 —
    JSON에 파이썬 타입명을 적는 순간 규칙 데이터가 엔진 구현에 결합된다.
    """
    fm = doc.frontmatter or {}
    if key not in fm:
        return (), None
    raw = fm[key]
    if isinstance(raw, str):
        value = raw.strip()
        return ((value,) if value else ()), KIND_STR
    if isinstance(raw, list):
        members = {m.strip() for m in raw if isinstance(m, str) and m.strip()}
        return tuple(sorted(members)), KIND_LIST
    if isinstance(raw, datetime.date):  # datetime.datetime 포함(서브클래스)
        return (raw.isoformat(),), KIND_DATE
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return (str(raw),), KIND_NUM
    return (), KIND_OTHER


def build_context(
    root: str | Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    filter_key: str | None = None,
    filter_value: str | None = None,
    group_by: str | None = None,
    warnings: list[str] | None = None,
) -> str:
    """max_chars를 넘지 않는 래핑된 압축 인덱스 문자열을 만든다.

    filter_key/value(쌍)가 주어지면 그 frontmatter 축 값이 일치하는 개념만 담는다 —
    리스트 축은 멤버 일치로 전개한다(공유 표면 ``axis_values``). group_by가 주어지면
    축 값별 ``## <값>`` 섹션으로 묶는다(값 알파벳순, 미기재는 맨 뒤
    ``## (unclassified)``). 번들에서 하나라도 리스트인 축(다중값)은 묶지 않고
    무플래그와 동일하게 내며 진단을 ``warnings``에 남긴다 — 다중값 판정은 필터로
    좁힌 부분집합이 아니라 번들 전체의 성질이다. 축 키·값은 엔진이 해석하지 않는
    임의 frontmatter다.
    """
    entries: list[tuple[str | None, str]] = []  # (group_value|None, line)
    group_kinds: set[str] = set()
    group_present = 0
    group_distinct: set[str] = set()
    for rel, doc in walk_bundle(root):
        if posixpath.basename(rel) in RESERVED:
            continue
        group = None
        if group_by:
            values, kind = axis_values(doc, group_by)
            if kind is not None:
                group_kinds.add(kind)
                if values:
                    group_present += 1
                group_distinct.update(values)
            group = values[0] if values else None
        if filter_key is not None and filter_value not in axis_values(doc, filter_key)[0]:
            continue
        fm = doc.frontmatter or {}
        type_val = fm.get("type")
        type_str = type_val.strip() if isinstance(type_val, str) and type_val.strip() else "?"
        summary = gist(doc)
        head = f"{rel} [{type_str}]"
        line = f"{head} — {summary}" if summary else head
        entries.append((group, line))

    grouping = bool(group_by)
    if grouping and KIND_LIST in group_kinds:
        grouping = False  # 그룹핑만 포기 — 주입(무플래그 동일 출력)은 유지한다
        if warnings is not None:
            warnings.append(
                f"경고: 축 `{group_by}`는 다중값(리스트)이라 --group-by로 묶을 수 없어 "
                f"그룹핑을 생략했다 (기재 {group_present}에 값 {len(group_distinct)} — "
                f"개념 하나가 여러 값을 가짐). 값으로 좁히려면: --filter {group_by}=<값>"
            )

    if grouping:
        groups: dict[str | None, list[str]] = {}
        for group, line in entries:
            groups.setdefault(group, []).append(line)
        # 값 알파벳순, 미기재(None)는 맨 뒤
        ordered = sorted(k for k in groups if k is not None) + ([None] if None in groups else [])
        out_lines: list[str] = []
        for key in ordered:
            out_lines.append(f"## {key}" if key is not None else f"## {_UNCLASSIFIED}")
            out_lines.extend(groups[key])
    else:
        out_lines = [line for _, line in entries]

    out = _OPEN
    budget = max_chars - len(_CLOSE) - 1  # 닫는 래퍼 + 개행 몫 선차감
    for line in out_lines:
        if len(out) + 1 + len(line) > budget:
            break
        out += "\n" + line
    return out + "\n" + _CLOSE


def build_outline(root: str | Path) -> str:
    """축 윤곽 — 전량 목록 대신 저장고의 형상(계수·축 종수·디렉터리)을 낸다(#336).

    전량 주입은 규모가 커지면 예산 절단으로 개념이 조용히 사라진다(개념 수 절단
    금지를 문자 수 절단이 재현하는 꼴). 윤곽은 개념 수와 무관한 크기라 절단이
    원리적으로 없다 — 무엇이 있는지(축·주제 영역)만 담고 세부는 ``okf query``에
    맡긴다. 데이터는 census payload를 그대로 쓴다(이중 계산 금지).
    """
    # census가 이 모듈(gist·axis_values)을 import하므로 모듈 레벨이면 순환이다
    from okf_core.census import build_census  # 순환 참조 회피(런타임 로드)

    payload = build_census(root)
    b = payload["bundle"]
    lines = [f"개념 {b['concepts']} · 디렉터리 {b['dirs']} · 링크 {b['links']}"]
    axes = [f"{row['field']}({row['values']}종)" for row in payload["fields"]]
    if axes:
        lines.append("축: " + " · ".join(axes))
    dirs = [row["path"] for row in payload["dirs"]]
    if dirs:
        lines.append("디렉터리: " + " ".join(dirs))
    lines.append("세부 조회: okf query <번들경로> <SQL|-> — 축 값·링크·본문을 SQL로 판다.")
    return _OPEN + "\n" + "\n".join(lines) + "\n" + _CLOSE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf context", description="주입용 압축 인덱스")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="출력 상한(문자 수)")
    ap.add_argument("--group-by", metavar="KEY", help="frontmatter 축으로 섹션 그룹핑")
    ap.add_argument("--filter", metavar="KEY=VALUE", help="frontmatter 축 값으로 필터")
    ap.add_argument("--outline", action="store_true", help="전량 목록 대신 축 윤곽(#336)")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}", file=sys.stderr)
        return 2

    if args.outline:
        if args.group_by or args.filter:
            print(
                "오류: --outline은 단독 모드 — --group-by·--filter와 함께 쓸 수 없음",
                file=sys.stderr,
            )
            return 2
        print(build_outline(bundle))
        return 0

    filter_key = filter_value = None
    if args.filter is not None:
        if "=" not in args.filter:
            print("오류: --filter는 KEY=VALUE 형식이어야 함", file=sys.stderr)
            return 2
        filter_key, filter_value = args.filter.split("=", 1)

    warns: list[str] = []
    text = build_context(
        bundle,
        max_chars=args.max_chars,
        filter_key=filter_key,
        filter_value=filter_value,
        group_by=args.group_by,
        warnings=warns,
    )
    for warn in warns:
        print(warn, file=sys.stderr)  # stdout은 주입 산출물 — 진단이 섞이면 오염(#300과 동일 원리)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
