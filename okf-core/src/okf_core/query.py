"""지식 질의 (okf query) — 번들을 인메모리 SQLite로 짓고 SQL로 묻는다 (#333).

재료 제공자다(census와 같은 부류). 판정 재료를 제공할 뿐 판정자가 아니다:

- 임계값·순위·경고·제안을 만들지 않는다
- 결과를 절단하지 않는다(``--max-chars``류 없음) — 절단은 소비자의 ``LIMIT`` 몫
- 종료코드로 판정하지 않는다 — 결과 0건도 0(판정이 아니라 사실), 2는 실행 오류뿐

파일도 캐시도 남기지 않는다 — 프로세스마다 현재 ``.md``에서 새로 지으므로 신선하지
않은 상태가 존재할 수 없다(신선도·세대·동시성·무효화 문제가 원천 부재). 스키마
정본은 Epic(#331):

  concept(path, dir, type, summary, body, frontmatter_json, conforms)
  axis_value(path, axis, value, kind)   -- 축은 컬럼이 아니라 행(taxonomy-neutral)
  edge(src, dst, via)                   -- via NULL이면 본문 링크, 아니면 그 축 이름
  valid 뷰                              -- conforms=1 (일반 질의 우주)
  wide 뷰                               -- 단일값 축을 컬럼으로 승격(번들에서 귀납)

축 해석은 공유 표면 ``context.axis_values`` 하나를 그대로 쓴다 — 이 함수가 내지
않는 값은 DB에도 없다(#329·#330). 임의 SQL은 ``PRAGMA query_only`` + authorizer
(ATTACH·PRAGMA 차단)로 읽기 전용에 가둔다 — 인메모리라 파괴해도 무해하지만
ATTACH는 바깥 파일에 닿는다.
"""

from __future__ import annotations

import argparse
import datetime
import json
import posixpath
import re
import sqlite3
import sys
from pathlib import Path

from okf_core.bundle import partition, rules_for
from okf_core.context import KIND_LIST, axis_values, gist
from okf_core.graph import resolve_link
from okf_core.parser import FORM_EXTERNAL, walk_bundle

ROOT_DIR = "."  # 번들 루트의 표시 이름 — census와 동일 표기
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONCEPT_COLUMNS = frozenset(
    {"path", "dir", "type", "summary", "body", "frontmatter_json", "conforms"}
)

_SCHEMA = """\
CREATE TABLE concept (
    path TEXT PRIMARY KEY,
    dir TEXT NOT NULL,
    type TEXT,
    summary TEXT NOT NULL,
    body TEXT NOT NULL,
    frontmatter_json TEXT,
    conforms INTEGER NOT NULL
);
CREATE TABLE axis_value (
    path TEXT NOT NULL,
    axis TEXT NOT NULL,
    value TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (path, axis, value)
);
CREATE TABLE edge (src TEXT NOT NULL, dst TEXT NOT NULL, via TEXT);
CREATE INDEX idx_axis_value ON axis_value(axis, value);
CREATE INDEX idx_edge_dst ON edge(dst);
CREATE VIEW valid AS SELECT * FROM concept WHERE conforms = 1;
"""


def _fm_json(fm: dict | None) -> str | None:
    """frontmatter 무손실 원본(JSON). EAV가 타입을 문자열로 뭉개므로 함께 보관한다."""
    if fm is None:
        return None

    def default(obj):
        if isinstance(obj, datetime.date):  # datetime 포함(서브클래스) — 축 값과 같은 표기
            return obj.isoformat()
        return str(obj)

    return json.dumps(fm, ensure_ascii=False, sort_keys=True, default=default)


def _wide_axes(docs: dict, concepts: list[str]) -> list[str]:
    """wide 뷰로 승격할 축을 번들에서 귀납한다(축 이름을 코드에 적지 않는다).

    승격 조건: SQL 식별자 이름 · concept 컬럼과 무충돌 · **리스트 kind 무관측**(#329의
    다중값 술어와 동일 — 단일 멤버 리스트 축을 관측만으로 승격하면 CLI는 그룹핑을
    거부하는데 wide는 승격하는 두 답이 생긴다) · 문서당 값 1개 · 값이 개념 수보다
    적게 수렴(자유 서술 배제).
    """
    paths: dict[str, set[str]] = {}
    rows: dict[str, int] = {}
    distinct: dict[str, set[str]] = {}
    kinds: dict[str, set[str]] = {}
    for rel in concepts:
        doc = docs[rel]
        for key in doc.frontmatter or {}:
            values, kind = axis_values(doc, key)
            if kind is not None:
                kinds.setdefault(key, set()).add(kind)
            if values:
                paths.setdefault(key, set()).add(rel)
                rows[key] = rows.get(key, 0) + len(values)
                distinct.setdefault(key, set()).update(values)
    induced = []
    for axis in sorted(paths):
        if not _IDENTIFIER.match(axis) or axis in _CONCEPT_COLUMNS:
            continue
        if KIND_LIST in kinds.get(axis, set()):
            continue
        single = rows[axis] == len(paths[axis])
        converge = len(distinct[axis]) < len(paths[axis])
        if single and converge:
            induced.append(axis)
    return induced


def build_db(root: str | Path) -> sqlite3.Connection:
    """번들 → 인메모리 DB. 파스는 walk_bundle 1회, 이후 ParsedDoc 재사용(파스 1회 규율)."""
    parsed = walk_bundle(root)
    rules, _ = rules_for(parsed)
    part = partition(parsed, rules)
    docs = dict(parsed)
    concepts = sorted(part.concepts)
    concept_set = set(concepts)

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)

    for rel in sorted(list(part.concepts) + list(part.failing)):
        doc = docs[rel]
        fm = doc.frontmatter or {}
        type_val = fm.get("type")
        type_str = type_val.strip() if isinstance(type_val, str) and type_val.strip() else None
        conn.execute(
            "INSERT INTO concept VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rel,
                posixpath.dirname(rel) or ROOT_DIR,
                type_str,
                gist(doc),
                doc.body,
                _fm_json(doc.frontmatter),
                1 if rel in concept_set else 0,
            ),
        )
        for key in sorted(fm):
            values, kind = axis_values(doc, key)
            for value in values:
                conn.execute(
                    "INSERT OR IGNORE INTO axis_value VALUES (?, ?, ?, ?)", (rel, key, value, kind)
                )

    # 엣지 우주는 개념 집합과의 교집합(census와 동일). 본문 링크는 via NULL, 개념
    # 경로로 해소되는 축 값은 via=<축> — 어느 축이 관계 축인지 선언받지 않고 값이
    # 실재 개념으로 해소되는지로 귀납한다(taxonomy-neutral).
    edges: set[tuple[str, str, str | None]] = set()
    for rel in concepts:
        doc = docs[rel]
        for link in doc.links:
            if link.form == FORM_EXTERNAL:
                continue
            target = resolve_link(rel, link.target)
            if target is not None and target != rel and target in concept_set:
                edges.add((rel, target, None))
        for key in doc.frontmatter or {}:
            for value in axis_values(doc, key)[0]:
                target = resolve_link(rel, value)
                if target is not None and target != rel and target in concept_set:
                    edges.add((rel, target, key))
    ordered = sorted(edges, key=lambda e: (e[0], e[1], e[2] or ""))
    conn.executemany("INSERT INTO edge VALUES (?, ?, ?)", ordered)

    axes = _wide_axes(docs, concepts)
    subquery = "(SELECT a.value FROM axis_value a WHERE a.path = v.path AND a.axis = '{0}')"
    columns = "".join(f',\n    {subquery.format(axis)} AS "{axis}"' for axis in axes)
    conn.execute(f"CREATE VIEW wide AS SELECT v.*{columns}\nFROM valid v")
    conn.commit()
    return conn


def _lock_read_only(conn: sqlite3.Connection) -> None:
    """임의 SQL 실행 전 읽기 전용 봉인 — 쓰기(query_only)와 ATTACH·PRAGMA(authorizer)."""
    conn.execute("PRAGMA query_only=ON")

    def authorizer(action, *_):
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_PRAGMA):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)


def render(columns: list[str], rows: list[tuple]) -> str:
    """사람 가독 렌더 — 헤더 + 열 정렬. 계산·절단 없이 값을 그대로 옮긴다."""
    table = [columns] + [["" if v is None else str(v) for v in row] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(columns))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in table]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf query", description="번들 지식 SQL 질의(재료 제공 전용)")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("sql", help="실행할 SQL — `-`면 stdin에서 읽는다(따옴표 인용 충돌 회피)")
    ap.add_argument("--json", action="store_true", help="결과를 행 객체 배열 JSON으로 출력")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}", file=sys.stderr)
        return 2

    sql = sys.stdin.read() if args.sql == "-" else args.sql
    conn = build_db(bundle)
    try:
        _lock_read_only(conn)
        try:
            cur = conn.execute(sql)
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
        except (sqlite3.Error, sqlite3.Warning) as exc:
            # Warning은 Error의 서브클래스가 아니다 — 다중 스테이트먼트가 여기로 온다
            print(f"오류: SQL 실행 실패: {exc}", file=sys.stderr)
            return 2
    finally:
        conn.close()

    if args.json:
        print(json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, indent=2))
    elif rows:
        print(render(columns, rows))
    return 0  # 결과 0건도 0 — 판정이 아니라 사실(재료 제공자 규율)


if __name__ == "__main__":
    raise SystemExit(main())
