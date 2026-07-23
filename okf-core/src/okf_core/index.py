"""index.md 재생성 (T-P2-4, §6 형식).

디렉터리마다 섹션 헤딩 + ``* [Title](url) - description`` 목록으로 index.md를
생성한다. 루트 index.md만 ``okf_version`` frontmatter를 유지한다(§11) — 기존
루트 index의 선언 값을 보존하고, 없으면 "0.1"을 쓴다. 생성 결과는 다시
파싱했을 때 §9를 통과해야 한다(자기 출력 컨포먼트).

하위 디렉터리 항목은 베어 디렉터리(``<name>/``)가 아니라 그 ``<name>/index.md``를
링크한다 — 디렉터리→index 자동 해소를 하는 로컬 뷰어에선 둘 다 열리지만, 정적
웹뷰(원격 repo 브라우징)에선 디렉터리 링크가 파일이 아니라 깨진다. index.md를
직접 가리키면 로컬·원격 양쪽에서 동일하게 해소된다(문서간 링크 이식성).
"""

from __future__ import annotations

import argparse
import posixpath
from pathlib import Path

from okf_core.parser import ParsedDoc, walk_bundle
from okf_core.validate import concept_conforms, load_rules

RESERVED = frozenset({"index.md", "log.md"})
DEFAULT_OKF_VERSION = "0.1"


def _title(rel: str, doc: ParsedDoc) -> str:
    fm = doc.frontmatter or {}
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return posixpath.basename(rel).removesuffix(".md")


def _description(doc: ParsedDoc) -> str:
    fm = doc.frontmatter or {}
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return ""


def _entry(title: str, url: str, desc: str) -> str:
    return f"* [{title}]({url}) - {desc}" if desc else f"* [{title}]({url})"


def generate_indexes(root: str | Path) -> dict[str, str]:
    """{index.md 상대경로: 생성 내용}을 반환한다. .md를 가진 모든 디렉터리 대상."""
    root = Path(root)
    docs = dict(walk_bundle(root))
    rules, _ = load_rules()

    # 디렉터리 → (직속 개념 문서, .md를 품은 직속 하위 디렉터리)
    # 개념은 §9 파일 단위 통과분만 소비한다(불변식: == validate §9 통과 집합)
    dirs: dict[str, tuple[list[str], set[str]]] = {}
    for rel in docs:
        d = posixpath.dirname(rel)
        dirs.setdefault(d, ([], set()))
        if posixpath.basename(rel) not in RESERVED and concept_conforms(docs[rel], rules):
            dirs[d][0].append(rel)
        while d:  # 조상 디렉터리마다 자식 디렉터리 체인 등록
            parent = posixpath.dirname(d)
            dirs.setdefault(parent, ([], set()))[1].add(d)
            d = parent

    # 기존 루트 index의 okf_version 보존
    root_doc = docs.get("index.md")
    okf_version = DEFAULT_OKF_VERSION
    if root_doc is not None and isinstance(root_doc.frontmatter, dict):
        declared = root_doc.frontmatter.get("okf_version")
        if isinstance(declared, str) and declared.strip():
            okf_version = declared.strip()

    out: dict[str, str] = {}
    for d, (concepts, subdirs) in dirs.items():
        lines: list[str] = ["# Contents", ""]
        for sub in sorted(subdirs):
            name = posixpath.basename(sub)
            # 하위 디렉터리는 그 index.md로 링크한다 — 베어 `<name>/`(디렉터리 링크)는
            # 로컬 뷰어에선 index로 해소되지만 정적 웹뷰(원격 repo)에선 파일이 아니라
            # 깨진다. 모든 하위 디렉터리는 index.md를 갖도록 위 dirs에 등록되므로
            # (조상 체인 불변식) 대상은 항상 실재한다.
            lines.append(_entry(name, f"{name}/index.md", ""))
        for rel in sorted(concepts):
            doc = docs[rel]
            lines.append(_entry(_title(rel, doc), posixpath.basename(rel), _description(doc)))
        body = "\n".join(lines) + "\n"
        if d == "":
            body = f'---\nokf_version: "{okf_version}"\n---\n\n' + body
        out[posixpath.join(d, "index.md") if d else "index.md"] = body
    return out


def write_indexes(root: str | Path) -> list[str]:
    """생성한 index.md들을 번들에 기록하고 상대경로 목록을 반환한다."""
    root = Path(root)
    generated = generate_indexes(root)
    for rel, text in sorted(generated.items()):
        (root / rel).write_text(text, encoding="utf-8")
    return sorted(generated)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf index", description="§6 형식 index.md 재생성")
    ap.add_argument("bundle", help="번들 디렉터리 경로")
    ap.add_argument("--write", action="store_true", help="번들에 기록(미지정 시 미리보기 출력)")
    args = ap.parse_args(argv)

    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"오류: 번들 디렉터리가 아님: {bundle}")
        return 2
    if args.write:
        for rel in write_indexes(bundle):
            print(rel)
    else:
        for rel, text in sorted(generate_indexes(bundle).items()):
            print(f"=== {rel} ===")
            print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
