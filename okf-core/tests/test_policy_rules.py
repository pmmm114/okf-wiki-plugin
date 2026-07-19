"""T-P2-3·T-P2-6 — 완료 기준 매핑: 파이프의 파서 호출 파일당 1회(호출 카운터) /
규칙 상수가 코드에 없음(그렙 검사) / 루트 인덱스 okf_version으로 규칙 선택."""
from pathlib import Path

import okf_core.parser as parser_mod
from okf_core.policy import run_policies
from okf_core.validate import (
    POL_UNKNOWN_VERSION,
    is_conformant,
    load_rules,
    validate_bundle,
)

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"
SRC = Path(__file__).resolve().parents[1] / "src" / "okf_core"


def test_pipeline_parses_each_file_once(monkeypatch):
    real = parser_mod.parse
    calls = {"n": 0}

    def counting(source):
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(parser_mod, "parse", counting)
    findings = validate_bundle(APPENDIX_A, strict=True)  # §9 + 정책 전 파이프 통과
    assert findings == []
    assert calls["n"] == len(list(APPENDIX_A.rglob("*.md"))) == 6


def test_policy_emits_recommended_field_warn(tmp_path):
    (tmp_path / "a.md").write_text("---\ntype: concept\n---\n# A\n", encoding="utf-8")
    rules, _ = load_rules()
    findings = run_policies([("a.md", parser_mod.parse(tmp_path / "a.md"))], rules)
    assert [(f.file, f.rule, f.level) for f in findings] == [
        ("a.md", "POL.description", "warn")
    ]


def test_no_rule_constants_in_validate_or_policy_code():
    rules, _ = load_rules()
    forbidden = [f'"{name}"' for name in rules["reserved_files"]]
    forbidden += [f"'{name}'" for name in rules["reserved_files"]]
    forbidden += [f'"{key}"' for key in rules["required_frontmatter"]]
    forbidden += [f"'{key}'" for key in rules["required_frontmatter"]]
    for module in ("validate.py", "policy.py"):
        text = (SRC / module).read_text(encoding="utf-8")
        for literal in forbidden:
            assert literal not in text, f"{module}에 규칙 상수 {literal} 하드코딩"


def test_rules_selected_by_declared_okf_version(tmp_path):
    # 알려진 버전(appendix-a 루트가 "0.1" 선언) → 버전 warn 없음
    assert all(f.rule != POL_UNKNOWN_VERSION for f in validate_bundle(APPENDIX_A))

    # 미지 버전 → 기본 규칙으로 최선 소비 + warn(§11), 컨포먼스에는 영향 없음
    (tmp_path / "index.md").write_text('---\nokf_version: "9.9"\n---\n# C\n', encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "---\ntype: concept\ndescription: ok\n---\n# A\n", encoding="utf-8"
    )
    findings = validate_bundle(tmp_path)
    version_warns = [f for f in findings if f.rule == POL_UNKNOWN_VERSION]
    assert len(version_warns) == 1
    assert "9.9" in version_warns[0].msg and version_warns[0].level == "warn"
    assert is_conformant(findings)
    # 미지 버전 warn은 strict 승격 대상이 아님
    assert all(f.level == "warn" for f in validate_bundle(tmp_path, strict=True))


def test_load_rules_shape():
    rules, warn = load_rules("0.1")
    assert warn is None
    assert set(rules) >= {
        "reserved_files",
        "index_file",
        "log_file",
        "index_frontmatter_allowed_at",
        "required_frontmatter",
        "recommended_frontmatter",
        "log_date_heading_pattern",
        "strict_warn_set",
    }
