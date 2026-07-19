"""T-P2-2 §9 검사기 — 완료 기준 매핑: appendix-a 컨포먼트 / 규칙별 위반 픽스처 검출 /
--format json F-3 계약 일치. 추가로 F-3 종료코드와 --strict 승격을 고정한다."""

import json
import re
from pathlib import Path

from okf_core.validate import (
    POL_BROKEN_LINK,
    POL_DESCRIPTION,
    RULE_FRONTMATTER,
    RULE_RESERVED,
    RULE_TYPE,
    is_conformant,
    main,
    validate_bundle,
)

FIXTURES = Path(__file__).parent / "fixtures"
APPENDIX_A = FIXTURES / "appendix-a"
VIOLATIONS = FIXTURES / "violations"
STRICT_WARNS = FIXTURES / "strict-warns"

RULE_PATTERN = re.compile(r"^(OKF9\.[123]|POL\.[a-z-]+)$")


def test_appendix_a_conformant():
    findings = validate_bundle(APPENDIX_A)
    assert findings == []  # error도 warn도 없음
    assert is_conformant(findings)
    assert main([str(APPENDIX_A)]) == 0
    assert main([str(APPENDIX_A), "--strict"]) == 0  # strict에서도 무발견


def test_violation_fixtures_detected_per_rule():
    findings = validate_bundle(VIOLATIONS)
    by_file = {(f.file, f.rule) for f in findings}
    assert ("no-frontmatter.md", RULE_FRONTMATTER) in by_file
    assert ("empty-type.md", RULE_TYPE) in by_file
    assert ("sub/index.md", RULE_RESERVED) in by_file
    errors = [f for f in findings if f.level == "error"]
    assert len(errors) == 3  # 규칙별 정확히 1건씩, 그 외 error 없음
    assert not is_conformant(findings)
    assert main([str(VIOLATIONS)]) == 1


def test_warns_only_and_strict_promotion():
    base = validate_bundle(STRICT_WARNS)
    assert {(f.file, f.rule, f.level) for f in base} == {
        ("a.md", POL_BROKEN_LINK, "warn"),
        ("a.md", POL_DESCRIPTION, "warn"),
    }
    assert all("거부 금지" in f.msg for f in base)  # 판정 문구 명시(F-3)
    assert is_conformant(base)  # index.md 부재 포함, 거부 금지 항목은 무해
    assert main([str(STRICT_WARNS)]) == 0

    strict = validate_bundle(STRICT_WARNS, strict=True)
    assert {f.level for f in strict} == {"error"}
    assert main([str(STRICT_WARNS), "--strict"]) == 1


def test_format_json_matches_f3_contract(capsys):
    exit_code = main([str(VIOLATIONS), "--format", "json"])
    assert exit_code == 1  # --format json에서도 종료코드 계약 유지
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload
    for obj in payload:
        assert set(obj) == {"file", "rule", "level", "msg"}
        assert RULE_PATTERN.match(obj["rule"])
        assert obj["level"] in {"error", "warn"}
        assert not obj["file"].startswith("/")  # 번들 상대경로


def test_missing_bundle_exit_2(tmp_path):
    assert main([str(tmp_path / "no-such-bundle")]) == 2


def test_log_date_heading_rule(tmp_path):
    (tmp_path / "log.md").write_text(
        "# Log\n\n## 2026-07-19\n* **Update**: ok\n\n## July 19\n* bad\n",
        encoding="utf-8",
    )
    findings = validate_bundle(tmp_path)
    assert [(f.rule, f.level) for f in findings] == [(RULE_RESERVED, "error")]
    assert "July 19" in findings[0].msg
