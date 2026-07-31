"""#333 okf query — 인메모리 sqlite 스키마·보안·종료코드·재료 제공자 규율.

스키마 정본은 Epic #331. 여기서는 (1) 적재가 공유 표면(axis_values)의 판정 표를
그대로 옮기는지, (2) 규격 미달이 conforms=0으로 담기고 valid가 거르는지, (3) wide
승격 술어가 kind 기반인지(#329와 동일 — 단일 멤버 리스트 축 승격 금지), (4) 쓰기·
ATTACH가 차단되는지, (5) 종료코드가 재료 제공자 규율을 따르는지를 잠근다.
"""

import json
from pathlib import Path

from okf_core.query import build_db, main

FIXTURES = Path(__file__).parent / "fixtures"
TAXONOMY = FIXTURES / "taxonomy"
VIOLATIONS = FIXTURES / "violations"


def _rows(root, sql):
    conn = build_db(root)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _bundle(tmp_path, docs: dict) -> Path:
    for name, text in docs.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


# --- 적재: 공유 표면 그대로 ----------------------------------------------------


def test_multivalue_axis_expands_to_rows(tmp_path):
    """리스트 축은 axis_value 여러 행 — #329의 전개 규칙이 저장 레벨에서 유지된다."""
    _bundle(
        tmp_path,
        {"a.md": "---\ntype: T\ntags: [x, y, x]\n---\n# A\n"},
    )
    rows = _rows(
        tmp_path, "SELECT axis, value, kind FROM axis_value WHERE axis='tags' ORDER BY value"
    )
    assert [(r["value"], r["kind"]) for r in rows] == [("x", "list"), ("y", "list")]


def test_date_axis_loaded_as_isoformat(tmp_path):
    """날짜 축은 isoformat 값·kind date — #330 판정 표가 DB에 그대로 온다."""
    _bundle(tmp_path, {"a.md": "---\ntype: T\nts: 2026-07-19T00:00:00Z\n---\n# A\n"})
    rows = _rows(tmp_path, "SELECT value, kind FROM axis_value WHERE axis='ts' ORDER BY value")
    assert rows == [{"value": "2026-07-19T00:00:00+00:00", "kind": "date"}]


def test_conforms_and_valid_view(tmp_path):
    """규격 미달 문서는 conforms=0으로 담기고 valid 뷰에서 빠진다 — 진단 질의 가능."""
    _bundle(
        tmp_path,
        {
            "ok.md": "---\ntype: T\ndescription: d.\n---\n# ok\n",
            "bad.md": "본문만 있고 frontmatter가 없다.\n",
        },
    )
    all_rows = _rows(tmp_path, "SELECT path, conforms FROM concept ORDER BY path")
    assert all_rows == [{"path": "bad.md", "conforms": 0}, {"path": "ok.md", "conforms": 1}]
    assert _rows(tmp_path, "SELECT path FROM valid ORDER BY path") == [{"path": "ok.md"}]


def test_violations_fixture_all_nonconformant():
    rows = _rows(VIOLATIONS, "SELECT count(*) AS n FROM valid ORDER BY n")
    assert rows == [{"n": 0}]
    rows = _rows(VIOLATIONS, "SELECT count(*) AS n FROM concept WHERE conforms=0 ORDER BY n")
    assert rows[0]["n"] > 0  # 감도: 미달이 실제로 담긴다


def test_reserved_files_are_not_concepts():
    rows = _rows(TAXONOMY, "SELECT path FROM concept WHERE path LIKE '%index.md' ORDER BY path")
    assert rows == []


# --- edge: 본문 링크 + 축 값 해소 귀납 ------------------------------------------


def test_edges_from_body_links_and_resolving_axis_values(tmp_path):
    """본문 링크는 via NULL, 개념 경로로 해소되는 축 값은 via=<축> — 축 이름을 코드에
    적지 않고 값이 실재 개념으로 해소되는지로 귀납한다(taxonomy-neutral)."""
    _bundle(
        tmp_path,
        {
            "info.md": "---\ntype: T\n---\n# info\n",
            "know.md": "---\ntype: T\nderived_from:\n  - /info.md\n  - /missing.md\n---\n"
            "# k\n[본문링크](info.md)\n",
        },
    )
    rows = _rows(tmp_path, "SELECT src, dst, via FROM edge ORDER BY src, dst, COALESCE(via,'')")
    assert rows == [
        {"src": "know.md", "dst": "info.md", "via": None},
        {"src": "know.md", "dst": "info.md", "via": "derived_from"},
    ]  # dangling(/missing.md)은 edge에 없다
    # 원문은 axis_value에 남는다 — dangling 진단은 SQL 몫
    axis = _rows(tmp_path, "SELECT value FROM axis_value WHERE axis='derived_from' ORDER BY value")
    assert [r["value"] for r in axis] == ["/info.md", "/missing.md"]


# --- wide 뷰: kind 기반 승격 술어 ----------------------------------------------


def test_wide_promotes_converging_single_value_axis(tmp_path):
    _bundle(
        tmp_path,
        {
            "a.md": "---\ntype: T\nlayer: wisdom\n---\n# a\n",
            "b.md": "---\ntype: T\nlayer: wisdom\n---\n# b\n",
            "c.md": "---\ntype: T\nlayer: information\n---\n# c\n",
        },
    )
    rows = _rows(tmp_path, "SELECT path, layer FROM wide WHERE layer='wisdom' ORDER BY path")
    assert [r["path"] for r in rows] == ["a.md", "b.md"]


def test_wide_excludes_list_kind_axis_even_if_single_member(tmp_path):
    """단일 멤버 리스트만 있는 축은 승격하지 않는다 — #329의 다중값 술어(kind 기반)와
    동일. 관측 기반만 쓰면 CLI는 그룹핑을 거부하는데 wide는 승격하는 두 답이 생긴다."""
    _bundle(
        tmp_path,
        {
            "a.md": "---\ntype: T\ncategory: [alpha]\n---\n# a\n",
            "b.md": "---\ntype: T\ncategory: [alpha]\n---\n# b\n",
            "c.md": "---\ntype: T\ncategory: [beta]\n---\n# c\n",
        },
    )
    conn = build_db(tmp_path)
    try:
        cols = {d[1] for d in conn.execute("PRAGMA table_info(wide)")}
    finally:
        conn.close()
    assert "category" not in cols
    # 승격에서 빠져도 axis_value 조인으로는 접근 가능
    rows = _rows(
        tmp_path,
        "SELECT path FROM axis_value WHERE axis='category' AND value='alpha' ORDER BY path",
    )
    assert [r["path"] for r in rows] == ["a.md", "b.md"]


def test_wide_excludes_free_text_axis(tmp_path):
    """값이 수렴하지 않는 축(자유 서술)은 승격하지 않는다."""
    _bundle(
        tmp_path,
        {
            "a.md": "---\ntype: T\ntitle: 하나\n---\n# a\n",
            "b.md": "---\ntype: T\ntitle: 둘\n---\n# b\n",
        },
    )
    conn = build_db(tmp_path)
    try:
        cols = {d[1] for d in conn.execute("PRAGMA table_info(wide)")}
    finally:
        conn.close()
    assert "title" not in cols


# --- 보안: 읽기 전용 ------------------------------------------------------------


def test_write_and_attach_are_blocked(tmp_path, capsys):
    _bundle(tmp_path, {"a.md": "---\ntype: T\n---\n# a\n"})
    assert main([str(tmp_path), "INSERT INTO concept(path) VALUES ('x')"]) == 2
    assert "오류" in capsys.readouterr().err
    assert main([str(tmp_path), f"ATTACH DATABASE '{tmp_path}/x.db' AS x"]) == 2
    assert "오류" in capsys.readouterr().err
    assert not (tmp_path / "x.db").exists()


# --- CLI: 종료코드·출력 형식·재료 규율 ------------------------------------------


def test_empty_result_is_exit_zero(tmp_path, capsys):
    """결과 0건은 판정이 아니라 사실 — exit 0, 무출력(재료 제공자 규율)."""
    _bundle(tmp_path, {"a.md": "---\ntype: T\n---\n# a\n"})
    assert main([str(tmp_path), "SELECT path FROM valid WHERE path='none.md' ORDER BY path"]) == 0
    assert capsys.readouterr().out == ""


def test_sql_error_is_exit_two(tmp_path, capsys):
    _bundle(tmp_path, {"a.md": "---\ntype: T\n---\n# a\n"})
    assert main([str(tmp_path), "SELECT nope FROM nowhere"]) == 2
    assert "오류" in capsys.readouterr().err


def test_not_a_dir_is_exit_two(tmp_path, capsys):
    assert main([str(tmp_path / "없는곳"), "SELECT 1"]) == 2
    assert "오류" in capsys.readouterr().err


def test_json_output_is_object_array(tmp_path, capsys):
    """--json은 객체 배열 — 컬럼명이 값과 함께 있어야 소비자가 파싱한다(계약 표면)."""
    _bundle(tmp_path, {"a.md": "---\ntype: T\nlayer: wisdom\n---\n# a\n"})
    assert main([str(tmp_path), "SELECT path, type FROM valid ORDER BY path", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"path": "a.md", "type": "T"}]


def test_human_output_has_header_and_rows(tmp_path, capsys):
    _bundle(tmp_path, {"a.md": "---\ntype: T\n---\n# a\n"})
    assert main([str(tmp_path), "SELECT path FROM valid ORDER BY path"]) == 0
    lines = capsys.readouterr().out.strip().split("\n")
    assert lines[0].split() == ["path"] and lines[1].split() == ["a.md"]


def test_sql_from_stdin(tmp_path, capsys, monkeypatch):
    """`-`는 stdin에서 SQL을 읽는다 — 작은따옴표 리터럴이 argv 인용과 충돌하지 않게."""
    import io

    _bundle(tmp_path, {"a.md": "---\ntype: T\nlayer: wisdom\n---\n# a\n"})
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            "SELECT path FROM axis_value WHERE axis='layer' AND value='wisdom' ORDER BY path"
        ),
    )
    assert main([str(tmp_path), "-"]) == 0
    assert "a.md" in capsys.readouterr().out


def test_repo_shipped_queries_declare_order_by():
    """repo가 싣는 SQL(스냅샷 케이스·레시피 문서)은 ORDER BY를 명시한다 — 인덱스
    유무만으로 순서가 반전되는 것을 실측했다(#333 그렙 게이트)."""
    import ast
    import re

    root = Path(__file__).resolve().parents[2]
    suite = (root / "okf-core" / "scripts" / "run_fixture_suite.py").read_text(encoding="utf-8")
    stmts = [
        node.value
        for node in ast.walk(ast.parse(suite))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lstrip().startswith("SELECT")
    ]
    assert stmts, "스냅샷 스위트에 query 케이스가 없다 — 게이트 감도 상실"
    for stmt in stmts:
        assert "ORDER BY" in stmt, f"ORDER BY 없는 스냅샷 질의: {stmt}"
    reference = root / "plugins" / "okf" / "skills" / "okf" / "reference"
    for doc in sorted(reference.glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for block in re.findall(r"```sql\n(.*?)```", text, flags=re.S):
            assert "ORDER BY" in block, f"{doc.name}의 SQL 블록에 ORDER BY 없음:\n{block}"
