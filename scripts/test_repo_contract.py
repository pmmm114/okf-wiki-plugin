"""repo 구조 계약 게이트 — CLAUDE.md의 불변식 중 검사가 없던 둘을 막는다.

CLAUDE.md의 "어겨서는 안 되는 것"은 대부분 `(게이트: ...)`로 강제 수단이 붙어 있지만,
아래 둘은 문장만 있고 검사가 없었다:

1. **`ci.yml` job 이름 `core` 불변** — `main` 브랜치 룰셋의 required status check
   컨텍스트가 이 이름이다. 이름을 바꾸면 required check가 영영 pending으로 남아
   게이트가 통째로 풀린다(잡이 red가 되는 게 아니라 **아무것도 막지 않게 된다**).
   같은 이유로 검사는 새 잡이 아니라 이 잡의 스텝으로 붙인다 — 그래서 "잡이 정확히
   하나이고 그 이름이 core"를 함께 고정한다.
2. **`plugin.json`에 version 필드 금지** — 플러그인 버전은 커밋 SHA로 추적하고,
   소비처가 고정하는 태그가 곧 버전이다. 필드를 넣으면 태그와 파일이 갈려 두 개의
   버전 원천이 생긴다.

무네트워크·stdlib다. YAML 파서를 쓰지 않는 이유는 `pytest scripts`가 CI에서
`uv run --no-project --with pytest`로 도는 의존 없는 환경이기 때문이다 — 잡 키만
읽으면 되므로 들여쓰기 스캐너로 충분하고, 그 스캐너 자체를 아래에서 검증한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 브랜치 룰셋 required status check 컨텍스트 — 이 파일이 이름 불변의 단일 원천.
REQUIRED_JOB = "core"

_ROOT = Path(__file__).resolve().parent.parent
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_PLUGIN_JSON = _ROOT / "plugins" / "okf" / ".claude-plugin" / "plugin.json"

_TOP_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.\-]+):")


def job_names(text: str) -> list[str]:
    """`jobs:` 블록 바로 아래 단계의 키 목록.

    `jobs:` 줄을 찾고, 들여쓰기가 그보다 깊은 동안만 훑으면서 **가장 얕은 깊이**의
    키만 모은다. 그 깊이가 곧 잡 이름 층이다. 주석·빈 줄은 건너뛴다.
    """
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*$", ln)), None)
    if start is None:
        return []

    body = []
    for ln in lines[start + 1 :]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln.startswith((" ", "\t")):  # 들여쓰기가 풀리면 jobs 블록의 끝
            break
        body.append(ln)

    matches = [(len(m.group("indent")), m.group("key")) for ln in body if (m := _TOP_KEY.match(ln))]
    if not matches:
        return []
    depth = min(indent for indent, _ in matches)
    return [key for indent, key in matches if indent == depth]


# --- 불변식 ------------------------------------------------------------------


def test_ci_has_single_job_named_core():
    names = job_names(_CI.read_text(encoding="utf-8"))
    assert names == [REQUIRED_JOB], (
        f"ci.yml의 잡이 {names}입니다 — 정확히 [{REQUIRED_JOB!r}]여야 합니다. "
        f"이 이름은 main 브랜치 룰셋의 required status check 컨텍스트라, 바꾸거나 잡을 "
        f"늘리면 required check가 매칭되지 않아 게이트가 풀립니다. 검사를 추가할 때는 "
        f"새 잡이 아니라 {REQUIRED_JOB} 잡의 스텝으로 붙입니다."
    )


def test_plugin_json_has_no_version_field():
    data = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))
    assert "version" not in data, (
        "plugin.json에 version 필드가 있습니다 — 플러그인 버전은 커밋 SHA로 추적하고 "
        "소비처가 고정하는 태그가 곧 버전입니다. 필드를 두면 버전 원천이 둘로 갈립니다."
    )


# --- 스캐너 자체 검증 (게이트가 진짜 잡는지) -----------------------------------


def test_scanner_reads_real_workflow():
    assert job_names(_CI.read_text(encoding="utf-8")) == ["core"]


def test_scanner_detects_renamed_job():
    """이름을 바꾸면 잡아야 한다 — 이 게이트의 존재 이유."""
    assert job_names("name: ci\njobs:\n  build:\n    runs-on: x\n") == ["build"]


def test_scanner_detects_added_job():
    """잡을 늘려도 잡아야 한다 — required check는 컨텍스트 하나만 본다."""
    text = "jobs:\n  core:\n    runs-on: x\n  extra:\n    runs-on: y\n"
    assert job_names(text) == ["core", "extra"]


def test_scanner_ignores_comments_blank_lines_and_nested_keys():
    text = (
        "name: ci\n"
        "on:\n"
        "  pull_request:\n"
        "    types: [opened]\n"
        "jobs:\n"
        "\n"
        "  # 주석\n"
        "  core:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: 스텝\n"
        "        run: echo hi\n"
    )
    assert job_names(text) == ["core"]


def test_scanner_stops_at_end_of_jobs_block():
    text = "jobs:\n  core:\n    runs-on: x\ntrailing:\n  other: 1\n"
    assert job_names(text) == ["core"]


def test_scanner_returns_empty_without_jobs_block():
    assert job_names("name: ci\non: [push]\n") == []
