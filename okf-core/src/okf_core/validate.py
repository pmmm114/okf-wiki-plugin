"""§9 컨포먼스 검사 (T-P2-2).

계약(F-3):
- 종료코드: 0 컨포먼트 / 1 비컨포먼트 / 2 실행 오류
- ``--format json``: 발견 1건당 1객체 ``{"file","rule","level","msg"}``
- 규칙 ID: OKF9.1 frontmatter 파싱 불가 / OKF9.2 `type` 부재·빈 값 /
  OKF9.3 예약 파일(index.md·log.md) 구조 위반 — 이 3개만 error(F-4)
- §9 "거부 금지" 항목(깨진 링크, description 부재)은 warn까지만. ``--strict``는
  그 두 warn만 error로 승격하며, warn 메시지에 거부 금지 판정 문구를 명시한다.

판정 원천은 vendor/spec/SPEC.md §9. 참고: 벤더 오라클(okf_validate.py)은
예약 파일 구조 위반을 §6/§7/§11 warning으로만 보고하고 §9.3 error를 내지 않는다
— 우리는 F-4(§9 조건 3) 그대로 OKF9.3을 error로 판정한다(차이는 T-P3-3
어댑터가 매핑). POL.* warn 2종은 T-P2-3에서 policy.py로 이관될 수 있는
v1 내장 정책이다.

파스는 parser.parse() 결과(ParsedDoc)를 파일당 1회 소비하며 재파싱하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from okf_core.parser import FORM_EXTERNAL, ParsedDoc, parse

RULE_FRONTMATTER = "OKF9.1"
RULE_TYPE = "OKF9.2"
RULE_RESERVED = "OKF9.3"
POL_BROKEN_LINK = "POL.broken-link"
POL_DESCRIPTION = "POL.description"

# --strict가 error로 승격하는 warn 규칙 — v1은 §9 거부 금지 항목 중 이 둘만
STRICT_PROMOTE = frozenset({POL_BROKEN_LINK, POL_DESCRIPTION})

RESERVED = frozenset({"index.md", "log.md"})
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REJECT_NOTE = "§9 거부 금지 항목 — 컨포먼스 판정에 영향 없음"


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


def _check_concept(rel: str, doc: ParsedDoc, findings: list[Finding]) -> None:
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
    type_val = doc.frontmatter.get("type")
    if not (isinstance(type_val, str) and type_val.strip()):
        findings.append(
            Finding(rel, RULE_TYPE, "error", "필수 `type` 필드 부재 또는 빈 값")
        )
    desc = doc.frontmatter.get("description")
    if not (isinstance(desc, str) and desc.strip()):
        findings.append(
            Finding(
                rel,
                POL_DESCRIPTION,
                "warn",
                f"권장 필드 `description` 부재 ({_REJECT_NOTE})",
            )
        )


def _check_index(rel: str, doc: ParsedDoc, is_root: bool, findings: list[Finding]) -> None:
    has_block = doc.frontmatter is not None or doc.fm_error is not None
    if not has_block:
        return
    if not is_root:
        findings.append(
            Finding(
                rel,
                RULE_RESERVED,
                "error",
                "index.md는 frontmatter를 가질 수 없음 (§6; 루트 index.md만 예외 — §11)",
            )
        )
    elif doc.fm_error is not None:
        findings.append(
            Finding(
                rel,
                RULE_RESERVED,
                "error",
                f"루트 index.md frontmatter 구조 위반: {doc.fm_error} (§11)",
            )
        )


def _check_log(rel: str, doc: ParsedDoc, findings: list[Finding]) -> None:
    for line in doc.body.split("\n"):
        if line.startswith("## "):
            heading = line[3:].strip()
            if not _ISO_DATE.match(heading):
                findings.append(
                    Finding(
                        rel,
                        RULE_RESERVED,
                        "error",
                        f"log.md 날짜 헤딩 `{heading}`이 ISO 8601(YYYY-MM-DD) 형식이 아님 (§7)",
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
                        f"크로스링크 대상 없음: `{link.target}` ({_REJECT_NOTE}, §5.3)",
                    )
                )


def validate_bundle(root: str | Path, strict: bool = False) -> list[Finding]:
    """번들 디렉터리를 §9 기준으로 검사하고 Finding 목록을 반환한다."""
    root = Path(root)
    md_files = sorted(p for p in root.rglob("*.md") if p.is_file())
    parsed = [(p.relative_to(root).as_posix(), parse(p)) for p in md_files]
    existing = {rel for rel, _ in parsed}

    findings: list[Finding] = []
    for rel, doc in parsed:
        name = posixpath.basename(rel)
        if name == "index.md":
            _check_index(rel, doc, is_root=(rel == "index.md"), findings=findings)
        elif name == "log.md":
            _check_log(rel, doc, findings)
        else:
            _check_concept(rel, doc, findings)
    _check_links(parsed, existing, findings)

    if strict:
        for f in findings:
            if f.rule in STRICT_PROMOTE:
                f.level = "error"
    findings.sort(key=lambda f: (f.file, f.rule))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf validate", description="OKF §9 컨포먼스 검사")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--strict", action="store_true", help="거부 금지 warn(깨진 링크·description 부재)을 error로 승격")
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
