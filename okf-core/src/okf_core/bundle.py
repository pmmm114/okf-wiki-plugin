"""bundle — 우주: 규칙 세대·개념/예약/미달 3분할·디렉터리 트리의 단일 진입점.

``walk_bundle``이 만든 ParsedDoc 목록 위에서 소비자들이 **같은 술어**를 쓰도록 모은
계층이다. 여기 모으는 이유는 코드 재사용이 아니라 **갈림 방지**다 — 아래 셋은 어느
소비자가 물어도 같은 답이어야 한다:

- **규칙 세대** — 루트 인덱스의 ``okf_version`` 선언으로 규칙을 고르는 절차(§11)가
  소비자마다 따로 있으면, 한 번들을 서로 다른 세대의 규칙으로 읽는 소비자가 생긴다.
- **개념 우주** — "무엇이 개념인가"(§9 파일 단위 통과)가 갈리면 index가 소비하는
  집합과 validate가 통과시킨 집합이 어긋난다(T-P2-7 불변식).
- **디렉터리 트리** — 조상 체인 등록 규칙이 갈리면 개념 0개인 통과 디렉터리가
  소비자에 따라 있기도 없기도 하다.

판정 상수는 여기에도 두지 않는다 — 예약 파일명·필수 필드는 전부
``rules/v<major>_<minor>.json``에서 로드한다(T-P2-6).
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from pathlib import Path

from okf_core.parser import ParsedDoc

RULES_DIR = Path(__file__).parent / "rules"
DEFAULT_RULES_VERSION = "0.1"

# walk_bundle의 반환 형태 — (번들 상대경로 posix, ParsedDoc) 목록.
ParsedBundle = list[tuple[str, ParsedDoc]]


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


def declared_version(parsed: ParsedBundle, rules: dict) -> str | None:
    """루트 인덱스 frontmatter의 okf_version 선언 값(없으면 None)."""
    docs = dict(parsed)
    version_key = rules["root_index_frontmatter_keys"][0]
    for rel in rules["index_frontmatter_allowed_at"]:
        doc = docs.get(rel)
        if doc is not None and isinstance(doc.frontmatter, dict):
            value = doc.frontmatter.get(version_key)
            if value is not None:
                return str(value)
    return None


def rules_for(parsed: ParsedBundle) -> tuple[dict, str | None]:
    """번들이 선언한 세대의 (규칙, 미지 버전 warn) — 모든 소비자의 규칙 진입점.

    선언 값을 찾는 데 쓰는 키 자체가 규칙 데이터에 있으므로 기본 규칙을 먼저 읽어
    선언을 해석하고, 그 선언으로 실제 규칙을 고른다(§11 최선 소비).
    """
    default_rules, _ = load_rules()
    return load_rules(declared_version(parsed, default_rules))


def concept_conforms(doc: ParsedDoc, rules: dict) -> bool:
    """개념 파일의 §9 파일 단위 통과 여부(9.1 파싱 + 9.2 필수 필드).

    "무엇이 개념인가"의 단일 술어다 — index의 소비 판단과 census의 개념 우주가
    이것을 공유해야 §9 통과 집합과 갈리지 않는다(T-P2-7 불변식).
    """
    if doc.fm_error is not None or doc.frontmatter is None:
        return False
    return all(
        isinstance(doc.frontmatter.get(key), str) and doc.frontmatter.get(key).strip()
        for key in rules["required_frontmatter"]
    )


@dataclass(frozen=True)
class Partition:
    """번들 문서의 3분할. 각 목록은 번들 상대경로 정렬(결정적 순회)."""

    concepts: tuple[str, ...]  # 예약이 아니고 §9 파일 단위를 통과한 개념
    reserved: tuple[str, ...]  # 예약 파일(규칙 데이터의 reserved_files)
    failing: tuple[str, ...]  # 예약이 아니지만 §9 파일 단위 탈락


def partition(parsed: ParsedBundle, rules: dict) -> Partition:
    """문서를 (개념 / 예약 / §9 탈락)으로 가른다 — 개념 우주의 단일 정의.

    예약 판정이 먼저다: 예약 파일은 필수 필드를 갖지 않는 것이 정상이므로(§6·§7)
    개념 통과 여부를 묻지 않는다.
    """
    reserved_names = set(rules["reserved_files"])
    concepts: list[str] = []
    reserved: list[str] = []
    failing: list[str] = []
    for rel, doc in parsed:
        if posixpath.basename(rel) in reserved_names:
            reserved.append(rel)
        elif concept_conforms(doc, rules):
            concepts.append(rel)
        else:
            failing.append(rel)
    return Partition(tuple(sorted(concepts)), tuple(sorted(reserved)), tuple(sorted(failing)))


def dir_tree(parsed: ParsedBundle) -> dict[str, set[str]]:
    """{디렉터리: 직속 하위 디렉터리 집합} — 문서를 품은 디렉터리와 그 조상 전부.

    조상 체인을 등록하므로 **직속 문서가 0개인 중간 디렉터리도 키로 존재한다**.
    루트는 빈 문자열 ``""``이다. 이 등록 규칙이 곧 "번들에 어떤 디렉터리가 있는가"의
    정의이고, index의 하위 디렉터리 링크 대상이 항상 실재한다는 보증이기도 하다.
    """
    tree: dict[str, set[str]] = {}
    for rel, _doc in parsed:
        d = posixpath.dirname(rel)
        tree.setdefault(d, set())
        while d:
            parent = posixpath.dirname(d)
            tree.setdefault(parent, set()).add(d)
            d = parent
    return tree
