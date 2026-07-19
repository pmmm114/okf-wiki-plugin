"""§9 컨포먼스 검사 (T-P2-2, 규칙 데이터화 T-P2-6).

계약(F-3):
- 종료코드: 0 컨포먼트 / 1 비컨포먼트 / 2 실행 오류
- ``--format json``: 발견 1건당 1객체 ``{"file","rule","level","msg"}``
- 규칙 ID: OKF9.1 frontmatter 파싱 불가 / OKF9.2 필수 필드 부재·빈 값 /
  OKF9.3 예약 파일 구조 위반 — 이 3개만 error(F-4)
- §9 "거부 금지" 항목(깨진 링크, 권장 필드 부재)은 warn까지만. ``--strict``는
  규칙 데이터의 strict_warn_set만 error로 승격하며, warn 메시지에 거부 금지
  판정 문구를 명시한다.

판정에 쓰는 상수(예약 파일명·필수/권장 필드·헤딩 형식·strict 승격 집합)는
코드에 두지 않고 ``rules/v<major>_<minor>.json``에서 로드한다(T-P2-6). 버전은
루트 인덱스의 ``okf_version`` 선언으로 선택하고, 미지 버전은 기본 규칙으로
최선 소비하며 warn을 남긴다(§11).

파이프 순서(T-P2-3): 파스 1회(walk_bundle) → §9 → 정책(policy.run_policies,
같은 ParsedDoc 재사용) — 재파싱하지 않는다.

참고: 벤더 오라클(okf_validate.py)은 예약 파일 구조 위반을 §6/§7/§11
warning으로만 보고하고 §9.3 error를 내지 않는다 — 우리는 F-4(§9 조건 3)
그대로 OKF9.3을 error로 판정한다(차이는 T-P3-3 어댑터가 매핑).
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from okf_core.parser import FORM_EXTERNAL, ParsedDoc, walk_bundle

RULE_FRONTMATTER = "OKF9.1"
RULE_TYPE = "OKF9.2"
RULE_RESERVED = "OKF9.3"
POL_BROKEN_LINK = "POL.broken-link"
POL_DESCRIPTION = "POL.description"
POL_UNKNOWN_VERSION = "POL.okf-version"

RULES_DIR = Path(__file__).parent / "rules"
DEFAULT_RULES_VERSION = "0.1"
REJECT_NOTE = "§9 거부 금지 항목 — 컨포먼스 판정에 영향 없음"


@dataclass
class Finding:
    file: str  # 번들 상대경로(posix)
    rule: str  # OKF9.1 | OKF9.2 | OKF9.3 | POL.<id>
    level: str  # error | warn
    msg: str

    def to_dict(self) -> dict:
        return asdict(self)


def is_conformant(findings: list[Finding]) -> bool:
    return not any(f.level == "error" for f in findings)


def _rules_path(version: str) -> Path:
    return RULES_DIR / f"v{version.replace('.', '_')}.json"


def load_rules(version: str | None = None) -> tuple[dict, str | None]:
    """(규칙, 미지 버전 경고 메시지|None). 미지 버전은 기본 규칙으로 최선 소비(§11)."""
    requested = str(version).strip() if version is not None else DEFAULT_RULES_VERSION
    path = _rules_path(requested)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8")), None
    default = json.loads(_rules_path(DEFAULT_RULES_VERSION).read_text(encoding="utf-8"))
    return default, (
        f"미지 okf_version `{requested}` — {DEFAULT_RULES_VERSION} 규칙으로 최선 소비(§11, "
        "컨포먼스 판정에 영향 없음)"
    )


def _declared_version(parsed: list[tuple[str, ParsedDoc]], default_rules: dict) -> str | None:
    """루트 인덱스 frontmatter의 okf_version 선언 값(없으면 None)."""
    docs = dict(parsed)
    version_key = default_rules["root_index_frontmatter_keys"][0]
    for rel in default_rules["index_frontmatter_allowed_at"]:
        doc = docs.get(rel)
        if doc is not None and isinstance(doc.frontmatter, dict):
            declared = doc.frontmatter.get(version_key)
            if declared is not None:
                return str(declared)
    return None


def _check_concept(rel: str, doc: ParsedDoc, rules: dict, findings: list[Finding]) -> None:
    if doc.fm_error is not None:
        findings.append(
            Finding(rel, RULE_FRONTMATTER, "error", f"frontmatter 파싱 불가: {doc.fm_error}")
        )
        return
    if doc.frontmatter is None:
        findings.append(
            Finding(rel, RULE_FRONTMATTER, "error", "YAML frontmatter 블록 없음")
        )
        return
    for key in rules["required_frontmatter"]:
        val = doc.frontmatter.get(key)
        if not (isinstance(val, str) and val.strip()):
            findings.append(
                Finding(rel, RULE_TYPE, "error", f"필수 `{key}` 필드 부재 또는 빈 값")
            )


def _check_index(rel: str, doc: ParsedDoc, allowed: bool, findings: list[Finding]) -> None:
    has_block = doc.frontmatter is not None or doc.fm_error is not None
    if not has_block:
        return
    if not allowed:
        findings.append(
            Finding(
                rel,
                RULE_RESERVED,
                "error",
                f"`{rel}`은(는) frontmatter를 가질 수 없음 (§6; 루트 인덱스만 예외 — §11)",
            )
        )
    elif doc.fm_error is not None:
        findings.append(
            Finding(
                rel,
                RULE_RESERVED,
                "error",
                f"루트 인덱스 frontmatter 구조 위반: {doc.fm_error} (§11)",
            )
        )


def _check_log(rel: str, doc: ParsedDoc, date_re: re.Pattern, findings: list[Finding]) -> None:
    for line in doc.body.split("\n"):
        if line.startswith("## "):
            heading = line[3:].strip()
            if not date_re.match(heading):
                findings.append(
                    Finding(
                        rel,
                        RULE_RESERVED,
                        "error",
                        f"날짜 헤딩 `{heading}`이 ISO 8601(YYYY-MM-DD) 형식이 아님 (§7)",
                    )
                )


def _check_links(
    parsed: list[tuple[str, ParsedDoc]], existing: set[str], findings: list[Finding]
) -> None:
    """번들 내부 크로스링크의 대상 존재 검사 — 깨져도 warn까지만(§5.3, §9)."""
    for rel, doc in parsed:
        for link in doc.links:
            if link.form == FORM_EXTERNAL:
                continue
            target = link.target.split("#", 1)[0]
            if not target or target.endswith("/") or not target.endswith(".md"):
                continue
            if target.startswith("/"):
                resolved = target.lstrip("/")
            else:
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), target)
                )
            if resolved.startswith("..") or resolved not in existing:
                findings.append(
                    Finding(
                        rel,
                        POL_BROKEN_LINK,
                        "warn",
                        f"크로스링크 대상 없음: `{link.target}` ({REJECT_NOTE}, §5.3)",
                    )
                )


def validate_bundle(root: str | Path, strict: bool = False) -> list[Finding]:
    """번들 디렉터리를 §9 기준으로 검사하고 Finding 목록을 반환한다."""
    from okf_core.policy import run_policies  # 순환 참조 회피(런타임 로드)

    parsed = walk_bundle(root)
    existing = {rel for rel, _ in parsed}

    default_rules, _ = load_rules()
    rules, version_warn = load_rules(_declared_version(parsed, default_rules))

    findings: list[Finding] = []
    if version_warn is not None:
        findings.append(
            Finding(default_rules["index_file"], POL_UNKNOWN_VERSION, "warn", version_warn)
        )

    allowed_at = set(rules["index_frontmatter_allowed_at"])
    date_re = re.compile(rules["log_date_heading_pattern"])
    for rel, doc in parsed:
        name = posixpath.basename(rel)
        if name == rules["index_file"]:
            _check_index(rel, doc, allowed=rel in allowed_at, findings=findings)
        elif name == rules["log_file"]:
            _check_log(rel, doc, date_re, findings)
        else:
            _check_concept(rel, doc, rules, findings)
    _check_links(parsed, existing, findings)
    findings.extend(run_policies(parsed, rules))

    if strict:
        promote = set(rules["strict_warn_set"])
        for f in findings:
            if f.rule in promote:
                f.level = "error"
    findings.sort(key=lambda f: (f.file, f.rule))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf validate", description="OKF §9 컨포먼스 검사")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--strict", action="store_true", help="거부 금지 warn(깨진 링크·권장 필드 부재)을 error로 승격")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}", file=sys.stderr)
        return 2
    findings = validate_bundle(bundle, strict=args.strict)

    if args.format == "json":
        print(json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"{f.level:5} {f.file}  {f.rule}  {f.msg}")
        errors = sum(1 for f in findings if f.level == "error")
        warns = len(findings) - errors
        verdict = "컨포먼트" if errors == 0 else "비컨포먼트"
        print(f"{verdict}: error {errors}건, warn {warns}건")
    return 0 if is_conformant(findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
