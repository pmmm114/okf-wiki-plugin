"""주입용 압축 인덱스 (T-P2-4).

개념 문서당 한 줄 ``<경로> [<type>] — <핵심 값>`` 형식으로 압축한다. 핵심 값은
frontmatter description, 없으면 본문 첫 표 행·첫 문장에서 추출. 결과는
``<okf-context>...</okf-context>``로 감싸고, 절단 기준은 **문자 수만**
(``--max-chars``, 기본 8000 — 훅 10,000자 한도 마진). 개념 수 절단(maxConcepts류)은
재도입 금지 — 폐기 확정 안티패턴.

축 투영(``--group-by KEY``)·필터(``--filter KEY=VALUE``)는 **임의 frontmatter 키**를
받는다 — 엔진은 특정 축 이름·값 어휘를 모른다(taxonomy-neutral). 그룹은 값 알파벳순,
미기재는 ``(unclassified)``로 맨 뒤. 무플래그 출력은 바이트 불변.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from pathlib import Path

from okf_core.parser import ParsedDoc, walk_bundle

RESERVED = frozenset({"index.md", "log.md"})
DEFAULT_MAX_CHARS = 8000
_OPEN = "<okf-context>"
_CLOSE = "</okf-context>"
_GIST_MAX = 160


def _gist(doc: ParsedDoc) -> str:
    fm = doc.frontmatter or {}
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    # 본문에서 추출: 첫 표 행 또는 헤딩이 아닌 첫 문장
    for line in doc.body.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```") or s.startswith("~~~"):
            continue
        if not s.startswith("|"):
            cut = s.find(". ")
            if cut != -1:
                s = s[: cut + 1]
        return s[:_GIST_MAX]
    return ""


_UNCLASSIFIED = "(unclassified)"


def _axis_value(doc: ParsedDoc, key: str) -> str | None:
    """frontmatter[key]의 정규화 문자열 값(비문자열·빈 값·부재는 None)."""
    fm = doc.frontmatter or {}
    val = fm.get(key)
    return val.strip() if isinstance(val, str) and val.strip() else None


def build_context(
    root: str | Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    filter_key: str | None = None,
    filter_value: str | None = None,
    group_by: str | None = None,
) -> str:
    """max_chars를 넘지 않는 래핑된 압축 인덱스 문자열을 만든다.

    filter_key/value가 주어지면 그 frontmatter 축 값이 일치하는 개념만 담고,
    group_by가 주어지면 축 값별 ``## <값>`` 섹션으로 묶는다(값 알파벳순, 미기재는
    맨 뒤 ``## (unclassified)``). 축 키·값은 엔진이 해석하지 않는 임의 frontmatter다.
    """
    entries: list[tuple[str | None, str]] = []  # (group_value|None, line)
    for rel, doc in walk_bundle(root):
        if posixpath.basename(rel) in RESERVED:
            continue
        if filter_key is not None and _axis_value(doc, filter_key) != filter_value:
            continue
        fm = doc.frontmatter or {}
        type_val = fm.get("type")
        type_str = type_val.strip() if isinstance(type_val, str) and type_val.strip() else "?"
        gist = _gist(doc)
        head = f"{rel} [{type_str}]"
        line = f"{head} — {gist}" if gist else head
        entries.append((_axis_value(doc, group_by) if group_by else None, line))

    if group_by:
        groups: dict[str | None, list[str]] = {}
        for group, line in entries:
            groups.setdefault(group, []).append(line)
        # 값 알파벳순, 미기재(None)는 맨 뒤
        ordered = sorted(k for k in groups if k is not None) + ([None] if None in groups else [])
        out_lines: list[str] = []
        for key in ordered:
            out_lines.append(f"## {key}" if key is not None else f"## {_UNCLASSIFIED}")
            out_lines.extend(groups[key])
    else:
        out_lines = [line for _, line in entries]

    out = _OPEN
    budget = max_chars - len(_CLOSE) - 1  # 닫는 래퍼 + 개행 몫 선차감
    for line in out_lines:
        if len(out) + 1 + len(line) > budget:
            break
        out += "\n" + line
    return out + "\n" + _CLOSE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf context", description="주입용 압축 인덱스")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="출력 상한(문자 수)")
    ap.add_argument("--group-by", metavar="KEY", help="frontmatter 축으로 섹션 그룹핑")
    ap.add_argument("--filter", metavar="KEY=VALUE", help="frontmatter 축 값으로 필터")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}")
        return 2

    filter_key = filter_value = None
    if args.filter is not None:
        if "=" not in args.filter:
            print("오류: --filter는 KEY=VALUE 형식이어야 함", file=sys.stderr)
            return 2
        filter_key, filter_value = args.filter.split("=", 1)

    print(
        build_context(
            bundle,
            max_chars=args.max_chars,
            filter_key=filter_key,
            filter_value=filter_value,
            group_by=args.group_by,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
