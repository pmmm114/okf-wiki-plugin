"""주입용 압축 인덱스 (T-P2-4).

개념 문서당 한 줄 ``<경로> [<type>] — <핵심 값>`` 형식으로 압축한다. 핵심 값은
frontmatter description, 없으면 본문 첫 표 행·첫 문장에서 추출. 결과는
``<okf-context>...</okf-context>``로 감싸고, 절단 기준은 **문자 수만**
(``--max-chars``, 기본 8000 — 훅 10,000자 한도 마진). 개념 수 절단(maxConcepts류)은
재도입 금지 — 폐기 확정 안티패턴.
"""
from __future__ import annotations

import argparse
import posixpath
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


def build_context(root: str | Path, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """max_chars를 넘지 않는 래핑된 압축 인덱스 문자열을 만든다."""
    lines = []
    for rel, doc in walk_bundle(root):
        if posixpath.basename(rel) in RESERVED:
            continue
        fm = doc.frontmatter or {}
        type_val = fm.get("type")
        type_str = type_val.strip() if isinstance(type_val, str) and type_val.strip() else "?"
        gist = _gist(doc)
        head = f"{rel} [{type_str}]"
        lines.append(f"{head} — {gist}" if gist else head)

    out = _OPEN
    budget = max_chars - len(_CLOSE) - 1  # 닫는 래퍼 + 개행 몫 선차감
    for line in lines:
        if len(out) + 1 + len(line) > budget:
            break
        out += "\n" + line
    return out + "\n" + _CLOSE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf context", description="주입용 압축 인덱스")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="출력 상한(문자 수)")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}")
        return 2
    print(build_context(bundle, max_chars=args.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
