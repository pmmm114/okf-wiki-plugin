"""버전 동기·중립 게이트 (#164).

버전 넘버링 전략(docs/releasing.md)의 두 불변식을 CI에서 강제한다:

1. **동기** — 루트 `pyproject.toml`(pre-commit·루트설치 셔틀)과 단일 원천
   `okf-core/pyproject.toml`의 `version`이 항상 같다. 한쪽만 올리는 드리프트를 막는다.
2. **버전-중립** — main은 `0.0.0.dev0` 플레이스홀더로 굴러간다. 사전에 다음 minor를
   박는(`0.(Y+1).0.dev0`) 옛 안티패턴을 금지한다. 릴리스 컷 커밋만 잠깐 dev 없는 실번호
   `X.Y.Z`를 달 수 있으므로, 허용은 {플레이스홀더} ∪ {깔끔한 X.Y.Z}로 둔다.

무네트워크·stdlib(re)로 pyproject 텍스트에서 version 라인을 읽는다(tomllib는 3.11+라 회피).
`pytest scripts`(CI `core` 잡)가 이 파일을 자동 수집하므로 위반 시 잡이 red가 된다.
"""

from __future__ import annotations

import re
from pathlib import Path

# 버전-중립 main의 플레이스홀더 — 이 파일이 이 불변식의 단일 원천(docs/releasing.md와 서술 일치).
PLACEHOLDER = "0.0.0.dev0"

_ROOT_DIR = Path(__file__).resolve().parent.parent
_ROOT_PYPROJECT = _ROOT_DIR / "pyproject.toml"
_CORE_PYPROJECT = _ROOT_DIR / "okf-core" / "pyproject.toml"  # 버전 단일 원천
_VERSION_LINE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)


def _read_version(path: Path) -> str:
    m = _VERSION_LINE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path}에서 version 라인을 찾지 못함"
    return m.group(1)


def test_pyproject_versions_in_sync():
    root = _read_version(_ROOT_PYPROJECT)
    core = _read_version(_CORE_PYPROJECT)
    assert root == core, f"루트({root})·okf-core({core}) pyproject 버전 불일치 — 둘 다 동기해야 함"


def test_version_is_neutral_placeholder_or_clean_release():
    version = _read_version(_CORE_PYPROJECT)
    clean_release = re.fullmatch(r"\d+\.\d+\.\d+", version) is not None
    assert version == PLACEHOLDER or clean_release, (
        f"버전 {version!r} 위반 — main은 {PLACEHOLDER}(버전-중립)여야 하고, 릴리스 커밋만 "
        f"dev 없는 X.Y.Z를 단다. 사전 minor 상향(예: 0.6.0.dev0) 금지(#164)."
    )
