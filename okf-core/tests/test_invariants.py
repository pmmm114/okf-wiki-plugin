"""T-P2-7 불변식 — 완료 기준 매핑: 두 불변식이 CI 상시 편입 가능한 형태(pytest)로 통과.

(1) 전 픽스처: `okf index`가 소비하는 파일 집합 == `okf validate`가 §9 통과로
    판정한 비예약 파일 집합
(2) okf-core는 Claude를 모른다 — src/okf_core/ 전체에 CLAUDE_ 환경변수·claude
    참조 없음(의존 방향 보증)
"""

import posixpath
import re
from pathlib import Path

from okf_core.index import RESERVED, generate_indexes
from okf_core.validate import validate_bundle

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLES = ("appendix-a", "violations", "strict-warns")
SRC = Path(__file__).resolve().parents[1] / "src" / "okf_core"

_ENTRY_URL = re.compile(r"^\* \[[^\]]*\]\(([^)]+)\)", re.MULTILINE)


def _consumable_files(root: Path) -> set[str]:
    """생성된 index들이 개념 항목으로 나열한 파일 집합(번들 상대경로)."""
    consumed = set()
    for index_rel, text in generate_indexes(root).items():
        base = posixpath.dirname(index_rel)
        for url in _ENTRY_URL.findall(text):
            if url.endswith(".md"):
                consumed.add(posixpath.normpath(posixpath.join(base, url)))
    return consumed


def _section9_pass_files(root: Path) -> set[str]:
    """validate가 §9 error를 내지 않은 비예약 .md 집합."""
    errored = {
        f.file for f in validate_bundle(root) if f.level == "error" and f.rule.startswith("OKF9.")
    }
    return {
        p.relative_to(root).as_posix() for p in root.rglob("*.md") if p.name not in RESERVED
    } - errored


def test_invariant_index_consumes_exactly_section9_pass_set():
    for name in BUNDLES:
        root = FIXTURES / name
        assert _consumable_files(root) == _section9_pass_files(root), name
    # 감도 확인: violations에는 §9 탈락 개념이 실제로 존재(공집합 동치가 아님을 보증)
    assert _section9_pass_files(FIXTURES / "violations") == set()
    assert len(_section9_pass_files(FIXTURES / "appendix-a")) == 3


def test_invariant_okf_core_knows_no_claude():
    sources = [p for p in SRC.rglob("*") if p.is_file() and p.suffix in (".py", ".json")]
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "CLAUDE_" not in text, path
        assert "claude" not in text.lower(), path
