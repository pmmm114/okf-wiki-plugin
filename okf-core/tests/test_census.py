"""okf census — 관측 계약을 잠근다.

이 연산의 가치는 "무엇을 보여주는가"가 아니라 **무엇을 하지 않는가**에 있다:
판정하지 않고(exit 1 없음), 절단하지 않고(개념 전량), 축 어휘를 선언하지 않는다
(값 목록은 번들에서 귀납). 아래 테스트는 그 셋을 각각 회귀로 고정한다.
"""

import json
import unicodedata
from pathlib import Path

from okf_core.bundle import load_rules, partition
from okf_core.census import DEFAULT_TEMPLATE, build_census, render, template_errors
from okf_core.index import generate_indexes
from okf_core.parser import walk_bundle
from okf_core.validate import validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"
VIOLATIONS = FIXTURES / "violations"
TAXONOMY = FIXTURES / "taxonomy"  # 본문·요약 원문에 스펙 조항 인용이 없는 번들


def _dirs(payload: dict) -> dict[str, dict]:
    return {row["path"]: row for row in payload["dirs"]}


def _axis(payload: dict, name: str) -> dict:
    return next(row for row in payload["axes"] if row["axis"] == name)


def test_census_is_deterministic():
    """같은 입력 → 바이트 동일 출력. 관측이 흔들리면 판정 근거가 될 수 없다."""
    first, second = build_census(APPENDIX_A), build_census(APPENDIX_A)
    assert first == second
    assert render(first) == render(second)


def test_concept_universe_equals_section9_pass_set():
    """census의 개념 우주 == §9 통과 집합 == index 소비 집합.

    세 소비자가 같은 술어(bundle.partition)를 쓰는지 잠근다 — 갈리면 관측이
    보여준 개념과 index가 싣는 개념이 달라진다.
    """
    for name in ("appendix-a", "violations", "strict-warns"):
        root = FIXTURES / name
        payload = build_census(root)
        listed = {item["path"] for row in payload["dirs"] for item in row["items"]}
        rules, _ = load_rules()
        expected = set(partition(walk_bundle(root), rules).concepts)
        assert listed == expected, name
        assert payload["bundle"]["concepts"] == len(expected), name
        # §9 error를 받은 파일은 개념 우주에 없다
        errored = {
            f.file
            for f in validate_bundle(root)
            if f.level == "error" and f.rule.startswith("OKF9.")
        }
        assert listed & errored == set(), name


def test_dirs_include_pass_through_directories():
    """직속 개념 0개인 통과 디렉터리도 반드시 나온다.

    잎만 보이면 "새 디렉터리를 파도 되는가"의 근거가 사라진다 — 기존 층 맵이
    즉시 부모 한 단계만 보느라 놓치던 바로 그 노드다.
    """
    payload = build_census(APPENDIX_A)
    dirs = _dirs(payload)
    assert "." in dirs and dirs["."]["depth"] == 0
    # 루트는 하위에 개념을 갖지만 직속은 0일 수 있다 — 그 사실이 표에 남는다
    assert dirs["."]["subtree"] == payload["bundle"]["concepts"]
    assert set(dirs) == {row["path"] for row in payload["dirs"]}
    assert [row["path"] for row in payload["dirs"]] == sorted(dirs)


def test_every_concept_appears_with_full_summary(tmp_path):
    """절단 없음 — 모든 개념이 원문 요약과 함께 실린다.

    값별로 앞 N개만 싣는 설계는 한 디렉터리의 개념을 통째로 감춰 배치 판정을
    반대 방향으로 유도할 수 있다. 그래서 상한 플래그 자체를 두지 않는다.
    """
    long_desc = "가" * 400
    (tmp_path / "index.md").write_text('---\nokf_version: "0.1"\n---\n# C\n', encoding="utf-8")
    (tmp_path / "a.md").write_text(
        f"---\ntype: T\ndescription: {long_desc}\n---\n# A\n", encoding="utf-8"
    )
    payload = build_census(tmp_path)
    item = payload["dirs"][0]["items"][0]
    assert item["summary"] == long_desc  # 원문 그대로
    assert item["summary_from"] == "frontmatter"


def test_summary_falls_back_to_body_and_says_so(tmp_path):
    """권장 필드가 없으면 본문에서 뽑되 **출처를 밝힌다**(짧은 것과 잘린 것의 구분)."""
    (tmp_path / "a.md").write_text("---\ntype: T\n---\n# A\n본문 첫 문장.\n", encoding="utf-8")
    item = build_census(tmp_path)["dirs"][0]["items"][0]
    assert item["summary_from"] == "body"
    assert item["summary"]


def test_default_axis_comes_from_rules_not_code():
    """무플래그 기본 축은 규칙 데이터의 필수 필드 — 코드가 축 이름을 갖지 않는다."""
    rules, _ = load_rules()
    payload = build_census(APPENDIX_A)
    assert payload["bundle"]["axes"] == list(rules["required_frontmatter"])


def test_axis_vocabulary_is_induced_not_declared():
    """축 값 목록은 번들에서 귀납한다 — 규칙에도 코드에도 어휘 선언이 없다."""
    rules, _ = load_rules()
    axis_name = rules["required_frontmatter"][0]
    values = {v["value"] for v in _axis(build_census(APPENDIX_A), axis_name)["values"]}
    assert values  # 픽스처가 실제로 쓰는 값이 잡힌다
    source = (Path(__file__).resolve().parents[1] / "src" / "okf_core" / "census.py").read_text(
        encoding="utf-8"
    )
    for value in values:
        assert value not in source, f"census.py가 축 값 어휘 {value!r}를 알고 있다"


def test_list_axis_expands_to_members(tmp_path):
    """리스트 값 축은 멤버로 전개한다 — 통째로 미기재로 접히면 채워진 어휘가 사라진다."""
    (tmp_path / "a.md").write_text("---\ntype: T\ntags: [x, y, x]\n---\n# A\n", encoding="utf-8")
    axis = _axis(build_census(tmp_path, axes=["tags"]), "tags")
    assert [v["value"] for v in axis["values"]] == ["x", "y"]  # 중복 제거·정렬
    assert axis["present"] == 1 and axis["missing"] == 0


def test_non_string_axis_is_counted_apart_from_missing(tmp_path):
    """키는 있는데 값을 못 내는 타입(bool 등)을 미기재와 구분한다.

    #330 이전엔 날짜(date)가 이 사례였으나 이제 값을 내므로, 판정 표가 값 0개로
    유지하는 타입(bool)로 의도를 보존한다.
    """
    (tmp_path / "a.md").write_text("---\ntype: T\nts: true\n---\n# A\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\ntype: T\n---\n# B\n", encoding="utf-8")
    axis = _axis(build_census(tmp_path, axes=["ts"]), "ts")
    assert (axis["present"], axis["valueless"], axis["missing"]) == (0, 1, 1)


def test_links_count_concept_to_concept_only(tmp_path):
    """링크 우주는 개념 집합과의 교집합 — 예약 파일·외부·자기 링크는 세지 않는다."""
    (tmp_path / "index.md").write_text("# C\n[a](a.md)\n", encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "---\ntype: T\n---\n# A\n[b](sub/b.md) [self](a.md) [idx](index.md) [x](https://e.com)\n",
        encoding="utf-8",
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("---\ntype: T\n---\n# B\n", encoding="utf-8")
    payload = build_census(tmp_path)
    assert payload["bundle"]["links"] == 1  # a → sub/b 하나뿐
    dirs = _dirs(payload)
    assert dirs["."]["links"]["outbound"] == 1 and dirs["sub"]["links"]["inbound"] == 1
    assert dirs["sub"]["items"][0]["refs"] == 1  # 백링크 판정 재료


def test_census_never_judges(capsys):
    """§9 탈락이 있는 번들에서도 종료코드 0 — 관측은 판정으로 승격되지 않는다."""
    from okf_core.census import main

    assert main([str(VIOLATIONS)]) == 0
    assert main([str(VIOLATIONS), "--json"]) == 0
    capsys.readouterr()
    assert main([str(FIXTURES / "no-such-bundle")]) == 2  # 실행 오류만 2


def _visual_width(text: str) -> int:
    """터미널에서 실제로 차지하는 칸 수 — 전각은 2칸(``len``과 다르다)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _table_block(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    return lines[start : lines.index("", start)]


def test_render_labels_close_without_the_spec():
    """렌더 라벨은 화면 안에서 뜻이 닫힌다 — 스펙 조항 번호는 읽는 사람의 주소가 아니다.

    번들 원문(요약)에 든 조항 인용은 관측 대상이라 그대로 실린다. 그래서 원문에
    인용이 없는 번들로 **렌더가 스스로 만든 문구만** 본다.
    """
    text = render(build_census(TAXONOMY))
    assert "§" not in text
    assert "탈락" not in text


def test_render_aligns_full_width_labels():
    """한글 라벨이 든 표도 열이 맞는다 — ``len``으로 채우면 전각만큼 밀린다.

    번들 요약 표는 마지막 열이 우측 정렬 수치라, 열이 맞으면 모든 행의 표시 폭이 같다.
    """
    block = _table_block(render(build_census(TAXONOMY)), "## 번들")
    assert len(block) == 7  # 헤더 + 구분선 + 5개 항목
    assert len({_visual_width(line) for line in block}) == 1


def test_render_keeps_summaries_unabridged():
    """표가 원문을 자르지 않는다 — 요약은 열이 아니라 행에 딸린 줄로 실린다."""
    text = render(build_census(TAXONOMY))
    for _rel, doc in walk_bundle(TAXONOMY):
        description = (doc.frontmatter or {}).get("description")
        if isinstance(description, str) and description.strip():
            assert description.strip() in text


def _concepts_only(columns: list[dict]) -> dict:
    """개념 섹션 하나만 둔 최소 템플릿 — 표시를 최대한 줄여도 관측이 남는지 보는 도구."""
    return {"sections": [{"kind": "concepts", "heading": "## {path}", "columns": columns}]}


def test_default_template_satisfies_its_own_contract():
    """엔진 기본 템플릿이 계약 검사를 통과한다 — 계약을 어긴 기본값은 예시가 못 된다."""
    assert template_errors(json.loads(DEFAULT_TEMPLATE.read_text(encoding="utf-8"))) == []


def test_template_customizes_sections_labels_and_columns():
    """템플릿이 바꾸는 것: 섹션 선택·헤딩 문구·열 라벨·열 순서."""
    text = render(
        build_census(TAXONOMY),
        _concepts_only(
            [
                {"label": "Kind", "cell": "axis:type"},
                {"label": "File", "cell": "file"},
            ]
        ),
    )
    assert "## cluster" in text
    header = next(line for line in text.splitlines() if "File" in line)
    assert header.index("Kind") < header.index("File")  # 기본 템플릿은 파일이 먼저다
    assert "frontmatter 필드" not in text and "## 디렉터리" not in text  # 고르지 않은 섹션
    assert "받은 링크" not in text  # 고르지 않은 열


def test_template_cannot_drop_rows():
    """어떤 템플릿으로도 관측은 줄지 않는다 — 열을 하나만 남겨도 개념은 전량 실린다.

    템플릿에 행 필터·상한 문법이 아예 없다는 것이 census의 "절단 없음" 계약이다.
    """
    payload = build_census(TAXONOMY)
    text = render(payload, _concepts_only([{"label": "F", "cell": "file"}]))
    listed = [item["path"] for row in payload["dirs"] for item in row["items"]]
    assert len(listed) == payload["bundle"]["concepts"]
    for path in listed:
        assert Path(path).name in text


def test_template_cannot_hide_summaries():
    """요약 표시는 템플릿 소관이 아니다 — 원문을 감추면 관측이 거짓말이 된다."""
    text = render(build_census(TAXONOMY), _concepts_only([{"label": "F", "cell": "file"}]))
    assert "한 번에 들어온 묶음의 첫 개념." in text
    assert "(본문 발췌)" in text  # 잘린 요약의 출처 표시도 템플릿이 못 끈다


def test_template_errors_name_what_can_be_used():
    """오류가 곧 계약 문서다 — 커스텀하는 쪽은 엔진 코드를 읽지 않는다."""
    unknown_cell = template_errors(
        {"sections": [{"kind": "fields", "heading": "x", "columns": [{"cell": "nope"}]}]}
    )
    assert len(unknown_cell) == 1
    assert "nope" in unknown_cell[0] and "present_of_total" in unknown_cell[0]

    unknown_kind = template_errors({"sections": [{"kind": "nope", "heading": "x"}]})
    assert "bundle" in unknown_kind[0] and "concepts" in unknown_kind[0]

    unknown_value = template_errors(
        {
            "sections": [
                {
                    "kind": "bundle",
                    "heading": "x",
                    "columns": [{"label": "a"}, {"label": "b"}],
                    "rows": [{"label": "L", "value": "nope"}],
                }
            ]
        }
    )
    assert "nope" in unknown_value[0] and "reserved" in unknown_value[0]

    unknown_field = template_errors(
        {"sections": [{"kind": "fields", "heading": "{oops}", "columns": [{"cell": "field"}]}]}
    )
    assert "oops" in unknown_field[0]

    # 축 열은 축을 가진 섹션(dirs·concepts)에서만 — fields 행에는 축이 없다
    assert template_errors(
        {"sections": [{"kind": "fields", "heading": "x", "columns": [{"cell": "axes"}]}]}
    )


def test_main_rejects_unusable_template(tmp_path, capsys):
    """템플릿 문제는 실행 오류(2)다 — 판정 실패(1)와 뭉개지 않는다."""
    from okf_core.census import main

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"sections": [{"kind": "nope"}]}), encoding="utf-8")
    assert main([str(TAXONOMY), "--template", str(broken)]) == 2
    assert "nope" in capsys.readouterr().err

    assert main([str(TAXONOMY), "--template", str(tmp_path / "absent.json")]) == 2
    capsys.readouterr()


def test_render_does_not_recompute_payload():
    """렌더는 payload의 수치를 옮기기만 한다 — 이중 원천이면 두 출력이 갈린다."""
    payload = build_census(APPENDIX_A)
    payload["bundle"]["concepts"] = 999
    payload["dirs"][0]["links"]["internal"] = 888
    text = render(payload)
    assert "999" in text and "888" in text


def test_census_matches_index_consumption():
    """index가 싣는 개념과 census가 보여주는 개념이 같다(같은 술어 공유의 관측 증거)."""
    payload = build_census(APPENDIX_A)
    listed = {item["path"] for row in payload["dirs"] for item in row["items"]}
    generated = generate_indexes(APPENDIX_A)
    assert generated  # 색인이 실제로 생성됐다
    assert len(listed) == payload["bundle"]["concepts"]


def test_date_axis_yields_value_distribution(tmp_path):
    """날짜 축(#330): 쿼우팅 없는 datetime이 isoformat 값으로 분포·kinds에 잡힌다.

    표기 분열(쿼우팅 str vs datetime)은 관측이 그대로 비춘다 — 번들이 표기를 통일하지
    않았다는 사실이지 엔진이 통일할 대상이 아니다(#330 (a) 분열 수용, ``Z`` 되돌림은
    taxonomy-neutral 위반이라 기각).
    """
    (tmp_path / "a.md").write_text(
        "---\ntype: T\nts: 2026-07-19T00:00:00Z\n---\n# A\n", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        '---\ntype: T\nts: "2026-07-19T00:00:00Z"\n---\n# B\n', encoding="utf-8"
    )
    axis = _axis(build_census(tmp_path, axes=["ts"]), "ts")
    assert (axis["present"], axis["valueless"], axis["missing"]) == (2, 0, 0)
    assert [v["value"] for v in axis["values"]] == [
        "2026-07-19T00:00:00+00:00",
        "2026-07-19T00:00:00Z",
    ]
    fields = {row["field"]: row for row in build_census(tmp_path, axes=["ts"])["fields"]}
    assert fields["ts"]["kinds"] == {"date": 1, "str": 1}
