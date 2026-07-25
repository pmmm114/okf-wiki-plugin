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
3. **`ci.yml` ↔ composite action 참조 정합** — 잡을 하나로 묶어 두는 대가로 검사는
   `.github/actions/<카테고리>/`로 흩어졌다. 그래서 참조가 끊기는 새 실패 모드가
   생긴다: 아무도 부르지 않는 액션(죽은 검사 — 지운 줄 알았는데 안 돌고 있는 것도,
   돈다고 믿었는데 안 도는 것도 여기 해당)과 없는 디렉터리를 가리키는 `uses:`.

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
_ACTIONS = _ROOT / ".github" / "actions"
_PLUGIN_JSON = _ROOT / "plugins" / "okf" / ".claude-plugin" / "plugin.json"

_TOP_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.\-]+):")
# 로컬 composite action 참조 — `uses: ./.github/actions/<이름>`.
# `- uses:`(이름 없는 스텝)와 `uses:`(앞줄에 name이 있는 스텝) 둘 다 유효한 YAML이라
# 양쪽을 읽는다. 한쪽만 읽으면 멀쩡한 참조를 못 봐서 아래 고아 검사가 오탐한다.
_LOCAL_USES = re.compile(
    r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*\./\.github/actions/(?P<name>[A-Za-z0-9_.\-]+)"
    r"[ \t]*(?:#.*)?$",
    re.M,
)


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


def referenced_actions(text: str) -> list[str]:
    """`ci.yml`이 `uses:`로 부르는 로컬 composite action 이름."""
    return [m.group("name") for m in _LOCAL_USES.finditer(text)]


def defined_actions() -> list[str]:
    """`.github/actions/<이름>/action.yml`로 존재하는 액션 이름."""
    return sorted(p.parent.name for p in _ACTIONS.glob("*/action.yml"))


def test_ci_references_every_defined_action():
    """정의됐는데 아무도 안 부르는 액션 = 안 도는 검사. 조용해서 위험하다."""
    orphans = sorted(set(defined_actions()) - set(referenced_actions(_CI.read_text("utf-8"))))
    assert not orphans, (
        f"ci.yml이 부르지 않는 액션이 있습니다: {orphans} — 정의만 있고 안 도는 검사입니다. "
        f"부르거나 지우세요."
    )


def test_every_referenced_action_exists():
    """없는 디렉터리를 가리키는 `uses:`는 CI 런타임에야 터진다 — 여기서 먼저 잡는다."""
    missing = sorted(set(referenced_actions(_CI.read_text("utf-8"))) - set(defined_actions()))
    assert not missing, (
        f"ci.yml이 없는 액션을 부릅니다: {missing} — "
        f".github/actions/<이름>/action.yml이 있어야 합니다."
    )


def test_composite_actions_keep_the_job_count_at_one():
    """액션 파일에 `jobs:`가 없어야 한다 — 있으면 워크플로를 잘못 만든 것이다.

    composite action은 스텝 묶음이지 워크플로가 아니다. `jobs:`를 적어 넣으면 그건
    reusable workflow를 액션 자리에 둔 것이고, 그 순간 이 구조가 지키려던 "잡은 core
    하나" 전제가 무너진다.
    """
    for path in sorted(_ACTIONS.glob("*/action.yml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^jobs:", text, re.M), (
            f"{path.relative_to(_ROOT)}에 `jobs:`가 있습니다 — composite action은 스텝 "
            f"묶음입니다. 잡을 늘리면 required check 컨텍스트 {REQUIRED_JOB!r}가 갈립니다."
        )
        assert "using: composite" in text, (
            f"{path.relative_to(_ROOT)}가 composite action이 아닙니다 — "
            f"`runs: using: composite`여야 스텝 레벨로 합쳐집니다."
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


def test_uses_scanner_reads_real_workflow():
    """실제 ci.yml에서 액션 참조를 실제로 뽑는지 — 빈 리스트끼리 비교해 통과하는
    공허한 정합 검사가 되지 않게 한다."""
    assert referenced_actions(_CI.read_text(encoding="utf-8")), (
        "ci.yml에서 로컬 액션 참조를 하나도 못 읽었습니다 — 참조 형식이 바뀌었다면 "
        "_LOCAL_USES도 함께 고치세요."
    )
    assert defined_actions(), ".github/actions/에 액션이 하나도 없습니다"


def test_uses_scanner_ignores_marketplace_actions():
    """`actions/checkout@…` 같은 외부 액션은 로컬 참조가 아니다."""
    text = (
        "      - uses: actions/checkout@abc123 # v4\n"
        "      - uses: ./.github/actions/lint\n"
        "      - uses: astral-sh/setup-uv@def456\n"
    )
    assert referenced_actions(text) == ["lint"]
