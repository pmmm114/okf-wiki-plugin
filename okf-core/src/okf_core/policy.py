"""정책 검사 뼈대 (T-P2-3).

파이프 순서 강제: 파스 1회 → §9 → 정책. validate.validate_bundle이 넘겨준
ParsedDoc 목록을 그대로 재사용하며 **재파싱하지 않는다**(호출 카운터 테스트로
고정). v1 정책은 1개 — 규칙 데이터의 recommended_frontmatter(기본
``description``) 권장 warn. 정책 규칙 ID는 ``POL.<필드명>`` 형식으로, 판정
상수는 코드가 아니라 규칙 데이터(T-P2-6)에서 온다.
"""

from __future__ import annotations

import posixpath

from okf_core.parser import ParsedDoc


def run_policies(parsed: list[tuple[str, ParsedDoc]], rules: dict) -> list:
    """정책 warn 목록을 반환한다. §9 error 판정은 validate 몫."""
    from okf_core.validate import REJECT_NOTE, Finding  # 순환 참조 회피(런타임 로드)

    reserved = set(rules["reserved_files"])
    findings = []
    for rel, doc in parsed:
        if posixpath.basename(rel) in reserved:
            continue
        if not isinstance(doc.frontmatter, dict):
            continue  # frontmatter 자체 문제는 §9.1이 보고
        for key in rules["recommended_frontmatter"]:
            val = doc.frontmatter.get(key)
            if not (isinstance(val, str) and val.strip()):
                findings.append(
                    Finding(rel, f"POL.{key}", "warn", f"권장 필드 `{key}` 부재 ({REJECT_NOTE})")
                )
    return findings
