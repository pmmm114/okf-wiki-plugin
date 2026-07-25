"""번들 인구조사 — 배치·분류 판정에 먹일 결정적 관측(taxonomy 관측).

번들이 **지금 어떤 모양인가**를 계수와 원문으로 낸다: 디렉터리 형상, 임의 축의 값
분포, 축 × 디렉터리 교차표, 개념별 요약과 링크 유입. 새 개념을 어디에 두고 축 값을
무엇으로 할지는 이 출력을 보고 **사람·모델이 판정**한다 — 이 모듈은 판정하지 않는다.

**관측이지 판정이 아니다**(이 경계가 설계의 핵심):

- 임계값·순위·경고·제안이 없다. 정렬은 전부 코드포인트순 고정이라 "위에 있는 것이
  더 중요하다"는 신호조차 만들지 않는다.
- 종료코드는 0 고정(실행 오류만 2). 발견을 판정으로 승격하지 않으므로 §9 컨포먼스
  판정에 영향이 없고, 어떤 게이트의 입력도 아니다.
- 절단이 없다. 개념을 부분만 보여주고 "전부"라고 말하면 관측이 거짓말이 된다 —
  그래서 개수 상한 플래그를 두지 않는다.

**축 어휘를 모른다**(taxonomy-neutral). 무플래그 기본 축은 규칙 데이터의 필수 필드
목록에서 오고, ``--axis``로 임의 frontmatter 키를 투영할 수 있다. 어떤 축의 어떤
값이 "옳은지"는 번들마다 다르며 이 엔진의 소관이 아니다 — 실제로 번들마다 어휘가
서로소다. 그래서 값 목록은 선언받지 않고 **번들에서 귀납**한다.

개념 우주·디렉터리 트리·규칙 세대는 ``bundle``이 소유한 술어를 그대로 쓴다(index·
validate와 갈리지 않게).
"""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
from pathlib import Path

from okf_core.bundle import ParsedBundle, dir_tree, partition, rules_for
from okf_core.context import gist
from okf_core.graph import resolve_link
from okf_core.parser import FORM_EXTERNAL, ParsedDoc, walk_bundle

ROOT_DIR = "."  # 번들 루트 디렉터리의 표시 이름(내부 표현은 빈 문자열)
KIND_STR = "str"
KIND_LIST = "list"
KIND_OTHER = "other"
SOURCE_FRONTMATTER = "frontmatter"
SOURCE_BODY = "body"


def _dir_name(rel: str) -> str:
    """개념 경로가 속한 디렉터리의 표시 이름."""
    return posixpath.dirname(rel) or ROOT_DIR


def _depth(name: str) -> int:
    return 0 if name == ROOT_DIR else name.count("/") + 1


def axis_values(doc: ParsedDoc, key: str) -> tuple[tuple[str, ...], str | None]:
    """(그 개념이 이 축에 가진 값들, 값 종류|None) — 키 부재는 ``((), None)``.

    값 종류만 보고 어휘는 보지 않는다: 문자열은 값 1개, 문자열 리스트는 멤버 전부
    (중복 제거·정렬), 그 밖의 타입(숫자·날짜·매핑)은 값 0개다. 리스트를 전개하는
    이유는 다중값 축(태그류)이 통째로 "미기재"로 접히면 실제로 채워진 어휘가 관측에서
    사라지기 때문이다.
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
    return (), KIND_OTHER


def _summary(doc: ParsedDoc, rules: dict) -> tuple[str, str]:
    """(요약 문자열, 출처). 권장 필드 원문은 **절단 없이** 그대로 싣는다.

    어느 키가 요약을 담는지도 규칙 데이터에서 읽는다(권장 필드) — 키 이름을 코드에
    두지 않는 규율은 판정 상수와 같다. 원문이 없을 때만 본문에서 추출하며(공유 표면
    ``context.gist``) 그 경우는 길이 상한이 걸리므로 출처를 함께 낸다 — 소비자가
    "짧은 것"과 "잘린 것"을 구분할 수 있어야 관측이 정직하다.
    """
    fm = doc.frontmatter or {}
    for key in rules["recommended_frontmatter"]:
        value = fm.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), SOURCE_FRONTMATTER
    return gist(doc), SOURCE_BODY


def _concept_edges(parsed: ParsedBundle, concepts: set[str]) -> set[tuple[str, str]]:
    """개념 → 개념 본문 링크 집합(자기 링크·예약 파일·외부·미해소 제외).

    예약 파일명을 하드코딩하지 않고 **개념 집합과의 교집합**으로 엣지 우주를 정의한다
    — 판정 상수는 규칙 데이터가 단일 원천이라는 규율과 정합한다.
    """
    edges: set[tuple[str, str]] = set()
    for rel, doc in parsed:
        if rel not in concepts:
            continue
        for link in doc.links:
            if link.form == FORM_EXTERNAL:
                continue
            target = resolve_link(rel, link.target)
            if target is not None and target != rel and target in concepts:
                edges.add((rel, target))
    return edges


def _field_rows(parsed: ParsedBundle, concepts: set[str]) -> list[dict]:
    """개념들이 실제로 쓰는 frontmatter 키의 사용 현황(축 후보 목록).

    "개념 수 대비 서로 다른 값 수"가 축인지 산문인지를 가른다 — 값이 개념 수만큼
    많으면 자유 서술이고, 몇 개로 수렴하면 분류 축이다. 사용률 0인 축은 여기에
    나타나지 않으므로, 선언만 되고 안 쓰이는 축은 ``--axis``로 물어야 보인다.
    """
    total = len(concepts)
    present: dict[str, int] = {}
    distinct: dict[str, set[str]] = {}
    kinds: dict[str, dict[str, int]] = {}
    for rel, doc in parsed:
        if rel not in concepts:
            continue
        for key in doc.frontmatter or {}:
            values, kind = axis_values(doc, key)
            present[key] = present.get(key, 0) + 1
            distinct.setdefault(key, set()).update(values)
            if kind is not None:
                bucket = kinds.setdefault(key, {})
                bucket[kind] = bucket.get(kind, 0) + 1
    rows = []
    for key in sorted(present):
        bucket = kinds.get(key, {})
        rows.append(
            {
                "field": key,
                "present": present[key],
                "concepts": total,
                "values": len(distinct[key]),
                "kinds": {kind: bucket[kind] for kind in sorted(bucket)},
            }
        )
    return rows


def _axis_rows(parsed: ParsedBundle, concepts: set[str], axes: list[str]) -> list[dict]:
    """축별 값 목록과 값 × 디렉터리 교차표.

    "이 값이 어디에 사는가"를 낸다. 단일 축 계수만으로는 한 값이 특정 디렉터리에
    통째로 몰려 있다는 사실 — 즉 그 값이 문서 성격이 아니라 유입 경로의 흔적이라는 것 —
    이 보이지 않는다.
    """
    docs = dict(parsed)
    rows = []
    for axis in axes:
        counts: dict[str, int] = {}
        by_dir: dict[str, dict[str, int]] = {}
        present = missing = valueless = 0
        for rel in sorted(concepts):
            values, kind = axis_values(docs[rel], axis)
            if kind is None:
                missing += 1
            elif not values:
                valueless += 1
            else:
                present += 1
            for value in values:
                counts[value] = counts.get(value, 0) + 1
                bucket = by_dir.setdefault(value, {})
                name = _dir_name(rel)
                bucket[name] = bucket.get(name, 0) + 1
        rows.append(
            {
                "axis": axis,
                "present": present,
                "missing": missing,
                "valueless": valueless,
                "values": [
                    {
                        "value": value,
                        "count": counts[value],
                        "dirs": {d: by_dir[value][d] for d in sorted(by_dir[value])},
                    }
                    for value in sorted(counts)
                ],
            }
        )
    return rows


def _dir_rows(
    parsed: ParsedBundle,
    concepts: set[str],
    tree: dict[str, set[str]],
    edges: set[tuple[str, str]],
    axes: list[str],
    rules: dict,
) -> list[dict]:
    """디렉터리별 형상 — 직속·하위 개념 수, 링크 방향별 계수, 축 교차, 개념 전량.

    **직속 개념이 0개인 통과 디렉터리도 반드시 포함한다**. 개념이 매달린 잎만 보면
    "새 디렉터리를 파도 되는가"를 판단할 근거가 사라진다. 링크 계수는 임계값 없이
    0/비-0의 존재 여부만으로 고립된 잎과 자족한 묶음을 가른다.
    """
    docs = dict(parsed)
    names = sorted((name or ROOT_DIR) for name in tree)
    direct: dict[str, list[str]] = {name: [] for name in names}
    for rel in sorted(concepts):
        direct[_dir_name(rel)].append(rel)

    inbound_refs: dict[str, int] = {}
    for _src, dst in edges:
        inbound_refs[dst] = inbound_refs.get(dst, 0) + 1

    rows = []
    for name in names:
        prefix = "" if name == ROOT_DIR else name + "/"
        subtree = sorted(rel for rel in concepts if rel.startswith(prefix))
        internal = outbound = inbound = 0
        links_to: dict[str, int] = {}
        for src, dst in edges:
            src_dir, dst_dir = _dir_name(src), _dir_name(dst)
            if src_dir == name and dst_dir == name:
                internal += 1
            elif src_dir == name:
                outbound += 1
                links_to[dst_dir] = links_to.get(dst_dir, 0) + 1
            elif dst_dir == name:
                inbound += 1
        crossed: dict[str, dict[str, int]] = {}
        for axis in axes:
            bucket: dict[str, int] = {}
            for rel in direct[name]:
                for value in axis_values(docs[rel], axis)[0]:
                    bucket[value] = bucket.get(value, 0) + 1
            crossed[axis] = {value: bucket[value] for value in sorted(bucket)}
        items = []
        for rel in direct[name]:
            summary, source = _summary(docs[rel], rules)
            items.append(
                {
                    "path": rel,
                    "axes": {axis: list(axis_values(docs[rel], axis)[0]) for axis in axes},
                    "summary": summary,
                    "summary_from": source,
                    "refs": inbound_refs.get(rel, 0),
                }
            )
        rows.append(
            {
                "path": name,
                "depth": _depth(name),
                "concepts": len(direct[name]),
                "subtree": len(subtree),
                "links": {
                    "internal": internal,
                    "outbound": outbound,
                    "inbound": inbound,
                    "to": {d: links_to[d] for d in sorted(links_to)},
                },
                "axes": crossed,
                "items": items,
            }
        )
    return rows


def build_census(root: str | Path, axes: list[str] | None = None) -> dict:
    """번들 인구조사 payload. ``axes`` 미지정이면 규칙 데이터의 필수 필드가 기본 축.

    기본 축을 규칙에서 읽기 때문에 이 모듈은 축 **이름조차** 리터럴로 갖지 않는다 —
    엔진은 필수 필드가 무엇인지 데이터로 알 뿐, 그 필드의 값 어휘는 모른다.
    """
    parsed = walk_bundle(root)
    rules, version_warn = rules_for(parsed)
    part = partition(parsed, rules)
    tree = dir_tree(parsed)
    concepts = set(part.concepts)
    axis_keys = sorted(set(axes)) if axes else list(rules["required_frontmatter"])
    edges = _concept_edges(parsed, concepts)

    payload = {
        "bundle": {
            "concepts": len(part.concepts),
            "reserved": len(part.reserved),
            "failing": len(part.failing),
            "dirs": len(tree),
            "links": len(edges),
            "axes": axis_keys,
        },
        "fields": _field_rows(parsed, concepts),
        "axes": _axis_rows(parsed, concepts, axis_keys),
        "dirs": _dir_rows(parsed, concepts, tree, edges, axis_keys, rules),
    }
    if version_warn is not None:
        payload["bundle"]["notice"] = version_warn
    return payload


def render(payload: dict) -> str:
    """사람 가독 렌더 — 계산하지 않고 payload의 수치를 그대로 옮긴다(이중 원천 금지)."""
    b = payload["bundle"]
    lines = [
        f"개념 {b['concepts']} · 디렉터리 {b['dirs']} · 개념간 링크 {b['links']} · "
        f"예약 {b['reserved']} · §9 탈락 {b['failing']}"
    ]
    if "notice" in b:
        lines.append(f"알림: {b['notice']}")

    lines.append("")
    lines.append("## 필드")
    for row in payload["fields"]:
        kinds = " ".join(f"{k}:{n}" for k, n in row["kinds"].items())
        lines.append(
            f"  {row['field']:<16} {row['present']:>3}/{row['concepts']:<3} "
            f"값 {row['values']:>3}  [{kinds}]"
        )

    for axis in payload["axes"]:
        lines.append("")
        head = f"## 축 `{axis['axis']}` — 기재 {axis['present']} · 미기재 {axis['missing']}"
        if axis["valueless"]:
            lines.append(f"{head} · 비문자열 {axis['valueless']}")
        else:
            lines.append(head)
        for value in axis["values"]:
            dirs = "  ".join(f"{d}:{n}" for d, n in value["dirs"].items())
            lines.append(f"  {value['value']:<20} {value['count']:>3}   {dirs}")

    lines.append("")
    lines.append("## 디렉터리   깊이 · 직속(하위) · 링크 안/밖/들어옴")
    for row in payload["dirs"]:
        axes_text = "  ".join(
            f"{axis}={','.join(f'{v}:{n}' for v, n in values.items())}"
            for axis, values in row["axes"].items()
            if values
        )
        lines.append(
            f"  {row['path']:<30} d{row['depth']} {row['concepts']:>3}"
            f"({row['subtree']:>3}) {row['links']['internal']:>3}/"
            f"{row['links']['outbound']:>2}/{row['links']['inbound']:>2}  {axes_text}"
        )
        for item in row["items"]:
            marker = "" if item["summary_from"] == SOURCE_FRONTMATTER else " (본문 발췌)"
            lines.append(f"      - {item['path']} (refs {item['refs']}){marker}")
            if item["summary"]:
                lines.append(f"        {item['summary']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf census", description="번들 인구조사(관측 전용)")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument(
        "--axis",
        metavar="KEY",
        action="append",
        help="교차 집계할 frontmatter 축(반복 가능). 미지정이면 규칙의 필수 필드",
    )
    ap.add_argument("--json", action="store_true", help="payload를 JSON으로 출력")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}", file=sys.stderr)
        return 2

    payload = build_census(bundle, axes=args.axis)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(payload))
    return 0  # 관측은 판정하지 않는다 — 발견이 있어도 1을 내지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
