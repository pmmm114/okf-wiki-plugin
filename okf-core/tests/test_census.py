"""okf census — 관측 계약을 잠근다.

이 연산의 가치는 "무엇을 보여주는가"가 아니라 **무엇을 하지 않는가**에 있다:
판정하지 않고(exit 1 없음), 절단하지 않고(개념 전량), 축 어휘를 선언하지 않는다
(값 목록은 번들에서 귀납). 아래 테스트는 그 셋을 각각 회귀로 고정한다.
"""

from pathlib import Path

from okf_core.bundle import load_rules, partition
from okf_core.census import build_census, render
from okf_core.index import generate_indexes
from okf_core.parser import walk_bundle
from okf_core.validate import validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"
VIOLATIONS = FIXTURES / "violations"


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
    """키는 있는데 문자열 축이 아닌 경우를 미기재와 구분한다."""
    (tmp_path / "a.md").write_text("---\ntype: T\nts: 2026-07-25\n---\n# A\n", encoding="utf-8")
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
