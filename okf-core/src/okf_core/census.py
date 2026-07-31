"""census — 관측: 번들의 현재 형상을 낸다. 판정하지 않는다(배치·분류 판정의 재료).

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
import string
import sys
import unicodedata
from pathlib import Path

from okf_core.bundle import ParsedBundle, dir_tree, partition, rules_for
from okf_core.context import (
    KIND_DATE,
    KIND_LIST,
    KIND_NUM,
    KIND_OTHER,
    KIND_STR,
    axis_values,
    gist,
)
from okf_core.graph import resolve_link
from okf_core.parser import FORM_EXTERNAL, ParsedDoc, walk_bundle

ROOT_DIR = "."  # 번들 루트 디렉터리의 표시 이름(내부 표현은 빈 문자열)
SOURCE_FRONTMATTER = "frontmatter"
SOURCE_BODY = "body"

# 렌더 전용 표시 어휘. payload의 기계 어휘(``str``·``list``)를 그대로 화면에 내면
# 읽는 쪽이 엔진 내부 표현을 알아야 하므로, 표시 계층에서만 옮긴다.
KIND_LABELS = {
    KIND_STR: "문자열",
    KIND_LIST: "목록",
    KIND_DATE: "날짜",
    KIND_NUM: "숫자",
    KIND_OTHER: "기타",
}
INDENT = "  "

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = TEMPLATES_DIR / "census.json"
ALIGN_RIGHT = "right"
AXES_CELL = "axes"  # 축 개수만큼 열로 펼쳐지는 지시자
AXIS_PREFIX = "axis:"  # 특정 축 하나만 열로 세울 때


def _dir_name(rel: str) -> str:
    """개념 경로가 속한 디렉터리의 표시 이름."""
    return posixpath.dirname(rel) or ROOT_DIR


def _depth(name: str) -> int:
    return 0 if name == ROOT_DIR else name.count("/") + 1


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


def _display_width(text: str) -> int:
    """터미널 표시 폭. 한글·전각 문자는 두 칸을 차지하므로 ``len``으로 재면 열이 어긋난다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    gap = " " * max(0, width - _display_width(text))
    return gap + text if right else text + gap


def _table(
    headers: list[str],
    rows: list[list[str]],
    right: frozenset[int] = frozenset(),
    notes: list[str] | None = None,
) -> list[str]:
    """헤더·구분선·본문 표. 열 폭은 **내용에서 도출하고 절단하지 않는다**(관측 계약).

    ``right``는 우측 정렬할 열 인덱스 — 수치는 자릿수가 맞아야 크기 차이가 눈에 든다.
    ``notes``는 행마다 딸린 부가 줄(빈 문자열이면 생략)로, 표 폭 계산에서 제외해
    긴 원문 한 줄이 표 전체를 밀어내지 않게 한다.
    """
    if not rows:
        return []
    widths = [
        max(_display_width(headers[i]), *(_display_width(row[i]) for row in rows))
        for i in range(len(headers))
    ]

    def line(cells: list[str]) -> str:
        padded = (
            _pad(cell, width, i in right)
            for i, (cell, width) in enumerate(zip(cells, widths, strict=True))
        )
        return (INDENT + "  ".join(padded)).rstrip()

    out = [line(headers), INDENT + "  ".join("─" * w for w in widths)]
    for i, row in enumerate(rows):
        out.append(line(row))
        note = notes[i] if notes else ""
        if note:
            out.append(INDENT * 2 + note)
    return out


def _summary_note(item: dict) -> str:
    """개념 행에 딸리는 요약 줄. 본문 발췌는 길이 상한이 걸리므로 출처를 밝힌다 —
    읽는 쪽이 "짧은 것"과 "잘린 것"을 구분할 수 있어야 관측이 정직하다."""
    if not item["summary"]:
        return ""
    mark = "" if item["summary_from"] == SOURCE_FRONTMATTER else " (본문 발췌)"
    return item["summary"] + mark


def _counts(mapping: dict[str, int]) -> str:
    """``{값: 수}``를 한 셀에 담는 표시 — 값이 어디에 얼마나 있는지가 한 줄에 보이게."""
    return " · ".join(f"{key} {n}" for key, n in mapping.items())


# --- 셀 조립자 —— 템플릿이 이름으로 고르는 표시 계약 -------------------------
#
# 값 계산은 전부 이쪽에 있고 템플릿은 "어느 것을 어느 자리에"만 정한다. 그래서
# 템플릿에는 행을 거르거나 자르거나 순위로 다시 정렬하는 **문법 자체가 없다** —
# 표시를 아무리 바꿔도 관측(절단 없음·판정 없음)은 줄어들지 않는다.

_BUNDLE_VALUES = ("concepts", "dirs", "links", "reserved", "failing")

_FIELD_CELLS = {
    "field": lambda row: row["field"],
    "present": lambda row: str(row["present"]),
    "total": lambda row: str(row["concepts"]),
    "present_of_total": lambda row: f"{row['present']} / {row['concepts']}",
    "values": lambda row: str(row["values"]),
    "kinds": lambda row: _counts({KIND_LABELS.get(k, k): n for k, n in row["kinds"].items()}),
}

_AXIS_CELLS = {
    "value": lambda row: row["value"],
    "count": lambda row: str(row["count"]),
    "dirs": lambda row: _counts(row["dirs"]),
}

_DIR_CELLS = {
    "path": lambda row: row["path"],
    "depth": lambda row: str(row["depth"]),
    "concepts": lambda row: str(row["concepts"]),
    "subtree": lambda row: str(row["subtree"]),
    "concepts_with_subtree": lambda row: f"{row['concepts']} ({row['subtree']})",
    "links_internal": lambda row: str(row["links"]["internal"]),
    "links_outbound": lambda row: str(row["links"]["outbound"]),
    "links_inbound": lambda row: str(row["links"]["inbound"]),
    "links_flow": lambda row: (
        f"{row['links']['internal']} / {row['links']['outbound']} / {row['links']['inbound']}"
    ),
}

_CONCEPT_CELLS = {
    "file": lambda item: posixpath.basename(item["path"]),
    "path": lambda item: item["path"],
    "refs": lambda item: str(item["refs"]),
}

# 축 열을 쓸 수 있는 섹션과, 그 섹션에서 축 값을 읽는 법.
_AXIS_READERS = {
    "dirs": lambda row, axis: _counts(row["axes"].get(axis, {})),
    "concepts": lambda item, axis: ", ".join(item["axes"].get(axis, ())),
}

# 헤딩 문구에서 쓸 수 있는 치환 이름. 나머지 섹션 헤딩은 고정 문구다.
_HEADING_FIELDS = {
    "axes": ("axis", "present", "missing", "valueless"),
    "concepts": ("path", "depth", "concepts", "subtree"),
}


def _columns(section: dict, axis_keys: list[str]) -> tuple[list[str], list[str], frozenset[int]]:
    """템플릿 열 정의를 (헤더, 셀 이름, 우측 정렬 인덱스)로 편다.

    ``axes`` 셀은 축 개수만큼 펼쳐진다 — 축은 ``--axis``로 실행 때 정해지므로
    템플릿이 열 개수를 미리 적을 수 없다.
    """
    headers: list[str] = []
    names: list[str] = []
    right: set[int] = set()
    for col in section["columns"]:
        cell = col.get("cell", "")
        expanded = (
            [(axis, AXIS_PREFIX + axis) for axis in axis_keys]
            if cell == AXES_CELL
            else [(col.get("label", ""), cell)]
        )
        for label, name in expanded:
            if col.get("align") == ALIGN_RIGHT:
                right.add(len(headers))
            headers.append(label)
            names.append(name)
    return headers, names, frozenset(right)


def _cell(name: str, item: dict, cells: dict, reader) -> str:
    if name.startswith(AXIS_PREFIX):
        return reader(item, name[len(AXIS_PREFIX) :])
    return cells[name](item)


def _rows_table(
    section: dict,
    items: list[dict],
    cells: dict,
    axis_keys: list[str],
    reader=None,
    notes: list[str] | None = None,
) -> list[str]:
    headers, names, right = _columns(section, axis_keys)
    rows = [[_cell(name, item, cells, reader) for name in names] for item in items]
    return _table(headers, rows, right, notes)


def _heading(section: dict, key: str, source: dict, kind: str) -> str:
    fields = {name: source[name] for name in _HEADING_FIELDS.get(kind, ())}
    return section.get(key, section["heading"]).format(**fields)


def _render_bundle(payload: dict, section: dict, _axis_keys: list[str]) -> list[list[str]]:
    bundle = payload["bundle"]
    headers, _names, right = _columns(section, [])
    rows = [[row["label"], str(bundle[row["value"]])] for row in section["rows"]]
    blocks = [[section["heading"], *_table(headers, rows, right)]]
    if "notice" in bundle:
        blocks.append([f"{section.get('notice_label', '알림')}: {bundle['notice']}"])
    return blocks


def _render_fields(payload: dict, section: dict, axis_keys: list[str]) -> list[list[str]]:
    table = _rows_table(section, payload["fields"], _FIELD_CELLS, axis_keys)
    return [[section["heading"], *table]]


def _render_axes(payload: dict, section: dict, _axis_keys: list[str]) -> list[list[str]]:
    blocks = []
    for axis in payload["axes"]:
        key = "heading_with_valueless" if axis["valueless"] else "heading"
        table = _rows_table(section, axis["values"], _AXIS_CELLS, [])
        blocks.append([_heading(section, key, axis, "axes"), *table])
    return blocks


def _render_dirs(payload: dict, section: dict, axis_keys: list[str]) -> list[list[str]]:
    table = _rows_table(section, payload["dirs"], _DIR_CELLS, axis_keys, _AXIS_READERS["dirs"])
    return [[section["heading"], *table]]


def _render_concepts(payload: dict, section: dict, axis_keys: list[str]) -> list[list[str]]:
    blocks = []
    for row in payload["dirs"]:
        if not row["items"]:
            continue
        table = _rows_table(
            section,
            row["items"],
            _CONCEPT_CELLS,
            axis_keys,
            _AXIS_READERS["concepts"],
            # 요약은 원문 그대로라 길이가 제각각이다 — 열로 세우면 표가 무너지므로
            # 행 아래 딸린 줄로 내린다. 표시 여부는 템플릿이 정하지 못한다(절단 금지).
            notes=[_summary_note(item) for item in row["items"]],
        )
        blocks.append([_heading(section, "heading", row, "concepts"), *table])
    return blocks


_SECTION_RENDERERS = {
    "bundle": _render_bundle,
    "fields": _render_fields,
    "axes": _render_axes,
    "dirs": _render_dirs,
    "concepts": _render_concepts,
}

_SECTION_CELLS = {
    "fields": _FIELD_CELLS,
    "axes": _AXIS_CELLS,
    "dirs": _DIR_CELLS,
    "concepts": _CONCEPT_CELLS,
}


def _cell_errors(kind: str, columns: list, where: str) -> list[str]:
    cells = _SECTION_CELLS[kind]
    dynamic = kind in _AXIS_READERS
    allowed = sorted([*cells] + ([AXES_CELL] if dynamic else []))
    errors = []
    for j, col in enumerate(columns):
        name = col.get("cell")
        if name in allowed or (dynamic and isinstance(name, str) and name.startswith(AXIS_PREFIX)):
            continue
        hint = f" (또는 `{AXIS_PREFIX}<축 이름>`)" if dynamic else ""
        errors.append(
            f"{where}.columns[{j}]: 미지 셀 {name!r} — 쓸 수 있는 이름: {', '.join(allowed)}{hint}"
        )
    return errors


def _bundle_errors(section: dict, columns: list, where: str) -> list[str]:
    errors = []
    if len(columns) != 2:
        errors.append(f"{where}: bundle 섹션은 열이 2개(항목·수)여야 한다 — 현재 {len(columns)}개")
    rows = section.get("rows")
    if not isinstance(rows, list) or not rows:
        return [*errors, f"{where}: `rows`가 비었거나 목록이 아님"]
    for j, row in enumerate(rows):
        if not isinstance(row.get("label"), str):
            errors.append(f"{where}.rows[{j}]: `label` 문자열이 필요하다")
        if row.get("value") not in _BUNDLE_VALUES:
            errors.append(
                f"{where}.rows[{j}]: 미지 값 {row.get('value')!r} — "
                f"쓸 수 있는 이름: {', '.join(_BUNDLE_VALUES)}"
            )
    return errors


def _heading_errors(section: dict, kind: str, where: str) -> list[str]:
    allowed = set(_HEADING_FIELDS.get(kind, ()))
    errors = []
    for key in ("heading", "heading_with_valueless"):
        text = section.get(key)
        if not isinstance(text, str):
            continue
        for _literal, field, _spec, _conv in string.Formatter().parse(text):
            if field is None or field in allowed:
                continue
            usable = f"쓸 수 있는 이름: {', '.join(sorted(allowed))}" if allowed else "치환 불가"
            errors.append(f"{where}.{key}: 미지 치환 {{{field}}} — {usable}")
    return errors


def _section_errors(section: dict, where: str) -> list[str]:
    kind = section.get("kind")
    if kind not in _SECTION_RENDERERS:
        return [f"{where}: 미지 kind {kind!r} — 쓸 수 있는 값: {', '.join(_SECTION_RENDERERS)}"]
    errors = []
    if not isinstance(section.get("heading"), str):
        errors.append(f"{where}: `heading` 문자열이 필요하다")
    columns = section.get("columns")
    if not isinstance(columns, list) or not columns:
        errors.append(f"{where}: `columns`가 비었거나 목록이 아님")
        columns = []
    errors += (
        _bundle_errors(section, columns, where)
        if kind == "bundle"
        else _cell_errors(kind, columns, where)
    )
    return errors + _heading_errors(section, kind, where)


def template_errors(template: dict) -> list[str]:
    """템플릿 계약 위반 목록(빈 목록이면 통과).

    메시지는 무엇이 틀렸는지와 **대신 쓸 수 있는 이름**을 함께 낸다 — 커스텀 템플릿을
    쓰는 쪽은 엔진 코드를 읽지 않으므로, 오류 자체가 유일한 계약 문서다.
    """
    sections = template.get("sections")
    if not isinstance(sections, list) or not sections:
        return ["`sections`가 비었거나 목록이 아님 — 섹션을 하나 이상 둔다"]
    errors: list[str] = []
    for i, section in enumerate(sections):
        errors += _section_errors(section, f"sections[{i}]")
    return errors


def load_template(path: str | Path | None = None) -> dict:
    """렌더 템플릿을 읽는다. 미지정이면 엔진 기본 템플릿.

    위반은 렌더 전에 **전부 모아** 올린다 — 렌더 도중에 터지면 출력이 반쯤 나온 채
    실패해 무엇이 잘못됐는지 읽기 어렵다.
    """
    source = Path(path) if path else DEFAULT_TEMPLATE
    template = json.loads(source.read_text(encoding="utf-8"))
    errors = template_errors(template)
    if errors:
        detail = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"템플릿 계약 위반: {source}\n{detail}")
    return template


def render(payload: dict, template: dict | None = None) -> str:
    """사람 가독 렌더 — 계산하지 않고 payload의 수치를 그대로 옮긴다(이중 원천 금지).

    **표시는 템플릿이 정하고 관측은 엔진이 지킨다.** 템플릿이 바꾸는 것은 섹션 순서·
    헤딩 문구·열의 라벨/순서/선택/정렬뿐이고, 행 집합과 행 순서는 payload 그대로다.

    스펙 조항 번호·엔진 내부 어휘는 라벨에 쓰지 않는다. 이 출력의 독자는 번들을 다루는
    사람이지 엔진 구현자가 아니므로, 화면에서 뜻이 닫히지 않는 참조는 라벨이 아니다.
    """
    spec = template if template is not None else load_template()
    axis_keys = payload["bundle"]["axes"]
    blocks: list[list[str]] = []
    for section in spec["sections"]:
        blocks += _SECTION_RENDERERS[section["kind"]](payload, section, axis_keys)
    return "\n\n".join("\n".join(block) for block in blocks if block)


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
    ap.add_argument(
        "--template",
        metavar="PATH",
        help=(
            "렌더 템플릿 JSON(섹션·라벨·열 구성). 미지정이면 엔진 기본 — "
            "`okf_core/templates/census.json`을 복사해 고쳐 쓴다"
        ),
    )
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}", file=sys.stderr)
        return 2

    payload = build_census(bundle, axes=args.axis)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    try:
        template = load_template(args.template)
    except (OSError, ValueError) as exc:
        print(f"오류: 템플릿을 쓸 수 없음 — {exc}", file=sys.stderr)
        return 2
    print(render(payload, template))
    return 0  # 관측은 판정하지 않는다 — 발견이 있어도 1을 내지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
