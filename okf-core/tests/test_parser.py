"""T-P2-1 단일 파서 — 엣지 6종 + appendix-a 무예외 파싱 (완료 기준 매핑)."""

from pathlib import Path

from okf_core.parser import FORM_ABSOLUTE, FORM_RELATIVE, parse

FIXTURES = Path(__file__).parent / "fixtures"
EDGE = FIXTURES / "edge"


def test_crlf():
    doc = parse(EDGE / "crlf.md")
    assert doc.fm_error is None
    assert doc.frontmatter == {"type": "concept", "description": "CRLF fixture"}
    assert [(lk.target, lk.form) for lk in doc.links] == [("/other.md", FORM_ABSOLUTE)]


def test_bom():
    doc = parse(EDGE / "bom.md")
    assert doc.fm_error is None
    assert doc.frontmatter == {"type": "concept"}
    assert [(lk.target, lk.form) for lk in doc.links] == [("sibling.md", FORM_RELATIVE)]


def test_fence_dashes_not_frontmatter_boundary():
    doc = parse(EDGE / "fence-dashes.md")
    assert doc.fm_error is None
    assert doc.frontmatter["type"] == "concept"
    targets = [lk.target for lk in doc.links]
    assert "/real.md" in targets  # 펜스 앞 링크 수집
    assert "https://example.com/x" in targets  # 펜스 뒤 링크 수집
    assert "/fake.md" not in targets  # 펜스 내부 링크 제외
    assert "---" in doc.body  # 펜스 안 '---'는 본문에 그대로 남음


def test_empty_and_blank_type_parse_ok():
    empty = parse(EDGE / "empty-type.md")
    blank = parse(EDGE / "blank-type.md")
    assert empty.fm_error is None and blank.fm_error is None
    assert empty.frontmatter.get("type") == ""  # 판정은 validate 몫
    assert blank.frontmatter.get("type") is None  # 판정은 validate 몫


def test_no_closing_fence_reports_fm_error():
    doc = parse(EDGE / "no-close.md")
    assert doc.fm_error is not None
    assert doc.frontmatter is None
    assert "Body never closed" in doc.body  # 최선 반환


def test_yaml_error_reports_fm_error_with_body():
    doc = parse(EDGE / "bad-yaml.md")
    assert doc.fm_error is not None
    assert doc.frontmatter is None
    assert "best-effort body" in doc.body
    assert [lk.target for lk in doc.links] == ["/v.md"]


def test_appendix_a_all_files_parse_without_exception():
    bundle = FIXTURES / "appendix-a"
    md_files = sorted(bundle.rglob("*.md"))
    assert len(md_files) == 6
    for path in md_files:
        doc = parse(path)  # 무예외가 계약
        assert isinstance(doc.body, str)
    root = parse(bundle / "index.md")
    assert root.frontmatter == {"okf_version": "0.1"}
    sales = parse(bundle / "datasets" / "sales.md")
    assert sales.fm_error is None
    assert {lk.target for lk in sales.links} == {"/tables/orders.md", "/tables/customers.md"}
    orders = parse(bundle / "tables" / "orders.md")
    forms = {lk.target: lk.form for lk in orders.links}
    assert forms["/tables/customers.md"] == FORM_ABSOLUTE
    assert forms["/datasets/sales.md"] == FORM_ABSOLUTE


def test_bytes_input_and_line_numbers():
    doc = parse(b"---\ntype: t\n---\nline4 [a](/x.md)\nline5 [b](y.md)\n")
    assert [(lk.line, lk.form) for lk in doc.links] == [(4, FORM_ABSOLUTE), (5, FORM_RELATIVE)]
