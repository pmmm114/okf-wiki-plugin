"""단일 파서 — frontmatter·본문·인라인 링크 추출 (T-P2-1).

계약:
- 입력: 파일 경로(str | pathlib.Path) 또는 bytes
- 출력: ParsedDoc{frontmatter: dict|None, fm_error: str|None, body: str, links: [Link]}
- frontmatter: 파일 시작의 ``---`` 줄부터 다음 ``---`` 줄까지를 ``yaml.safe_load``
- links: 본문의 인라인 마크다운 링크만 수집(이미지 제외), 펜스 코드블록 내부는 제외
- 이 모듈이 파이프라인의 유일한 파스 지점이다 — validate/policy/index/graph/context는
  ParsedDoc을 재사용하고 재파싱하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 이미지(![..](..))가 아닌 인라인 링크. 대상은 공백 전까지, 선택적 제목("..")은 무시.
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
# 펜스 여는 줄: 선행 공백 0~3 + 백틱/틸드 3개 이상 (여는 줄은 info string 허용)
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

FORM_ABSOLUTE = "absolute"
FORM_RELATIVE = "relative"
FORM_EXTERNAL = "external"


@dataclass
class Link:
    target: str
    form: str  # absolute | relative | external
    line: int  # 파일 기준 1-시작 줄 번호


@dataclass
class ParsedDoc:
    frontmatter: dict | None
    fm_error: str | None
    body: str
    links: list[Link] = field(default_factory=list)


def _link_form(target: str) -> str:
    if _SCHEME_RE.match(target) or target.startswith("//"):
        return FORM_EXTERNAL
    if target.startswith("/"):
        return FORM_ABSOLUTE
    return FORM_RELATIVE


def _extract_links(lines: list[str], start: int) -> list[Link]:
    links: list[Link] = []
    fence_char = ""
    fence_len = 0
    in_fence = False
    for idx in range(start, len(lines)):
        line = lines[idx]
        if not in_fence:
            m = _FENCE_OPEN_RE.match(line)
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                continue
        else:
            stripped = line.strip()
            if (
                stripped
                and set(stripped) == {fence_char}
                and len(stripped) >= fence_len
            ):
                in_fence = False
            continue
        for lm in _LINK_RE.finditer(line):
            target = lm.group(1)
            links.append(Link(target=target, form=_link_form(target), line=idx + 1))
    return links


def walk_bundle(root: str | Path) -> list[tuple[str, ParsedDoc]]:
    """번들의 모든 .md를 정렬 순회해 (상대경로 posix, ParsedDoc) 목록 반환 — 파일당 1회 파싱."""
    root = Path(root)
    return [
        (p.relative_to(root).as_posix(), parse(p))
        for p in sorted(root.rglob("*.md"))
        if p.is_file()
    ]


def parse(source: str | Path | bytes | bytearray) -> ParsedDoc:
    """파일 경로 또는 bytes를 받아 ParsedDoc을 반환한다. 예외는 던지지 않는 것을 지향하되
    입력 타입 오류만 TypeError."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    else:
        raise TypeError(f"지원하지 않는 입력 타입: {type(source)!r}")

    # BOM은 utf-8-sig로 흡수, 줄끝은 LF로 정규화(CRLF·CR 지원)
    text = data.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    frontmatter: dict | None = None
    fm_error: str | None = None
    body_start = 0  # lines 인덱스(0-시작)

    if lines and lines[0].strip() == "---":
        close = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                close = i
                break
        if close is None:
            fm_error = "frontmatter 닫는 '---' 없음"
            body_start = 1  # 최선 반환: 여는 줄 이후 전부를 본문으로
        else:
            raw = "\n".join(lines[1:close])
            body_start = close + 1
            try:
                loaded = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                fm_error = f"YAML 문법 오류: {exc.__class__.__name__}"
            else:
                if loaded is None:
                    frontmatter = {}
                elif isinstance(loaded, dict):
                    frontmatter = loaded
                else:
                    fm_error = "frontmatter가 매핑(dict)이 아님"

    body = "\n".join(lines[body_start:])
    links = _extract_links(lines, body_start)
    return ParsedDoc(frontmatter=frontmatter, fm_error=fm_error, body=body, links=links)
