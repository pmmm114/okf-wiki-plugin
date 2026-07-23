"""마켓플레이스 배포 형태 게이트 (플러그인 버저닝 시스템).

`.claude-plugin/marketplace.json`의 플러그인 엔트리를 이 repo에서 **유일하게 동작하는
형태**로 고정한다: 상대경로 소스(`./...`) + 엔트리 version 없음. `pytest scripts`
(CI `core` 잡)가 자동 수집하므로 위반 시 잡이 red가 된다.

## 왜 이 형태만 허용하나

이 repo는 모노레포다 — 플러그인(`plugins/okf`)이 엔진을 **심링크**로 공유한다
(`plugins/okf/core → ../../okf-core`, `bin/okf`가 이를 통해 엔진 실행). Claude Code 공식
문서(plugin-marketplaces)에 근거해:

- **상대경로 소스**만 심링크를 해소한다 — git 마켓플레이스는 상대경로일 때 **repo 전체를
  클론**하므로 형제 디렉터리 `okf-core`가 함께 와서 심링크가 산다.
- **git-subdir 소스는 금지** — 하위 디렉터리만 **sparse 클론**해 `okf-core`가 빠지고
  심링크가 dangling → 플러그인이 깨진다.
- **엔트리 version 금지** — 상대경로(SHA 추적) 소스에 정적 version을 박으면 소비처 자동
  업데이트가 동결되고 라벨이 내용과 어긋난다. 릴리스 고정은 소비처가 마켓플레이스 add 시
  `pmmm114/okf-wiki-plugin@vX.Y.Z`처럼 **ref로** 한다(공식 방식).

또한 버전 해석 순서(plugin.json → marketplace 엔트리 → 커밋 SHA)의 전제인 "plugin.json엔
version 없음" 불변식도 여기서 함께 못박는다 — 정본은 `docs/plugin-versioning.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MARKETPLACE = _ROOT / ".claude-plugin" / "marketplace.json"
_PLUGIN_JSON = _ROOT / "plugins" / "okf" / ".claude-plugin" / "plugin.json"


def entry_problems(entry: dict) -> list[str]:
    """한 플러그인 엔트리의 배포 형태 위반 목록(빈 리스트 = 정상). git 불필요."""
    name = entry.get("name", "<이름없음>")
    src = entry.get("source")
    out: list[str] = []
    if not isinstance(src, str):
        out.append(
            f"{name}: source는 상대경로 문자열이어야 함 — 엔진을 심링크로 공유하는 모노레포라 "
            "전체-repo 클론(상대경로)만 심링크를 해소한다. git-subdir 등 객체 소스는 하위 "
            "디렉터리만 sparse 클론해 심링크가 깨진다"
        )
    elif not src.startswith("./"):
        out.append(f"{name}: 상대경로 소스는 './'로 시작해야 함 — {src!r}")
    if "version" in entry:
        out.append(
            f"{name}: 엔트리에 version 금지 — 상대경로(SHA 추적) 소스에 정적 version을 박으면 "
            "자동 업데이트가 동결된다. 릴리스 고정은 소비처가 add 시 @vX.Y.Z로 한다"
        )
    return out


def _doc() -> dict:
    return json.loads(_MARKETPLACE.read_text(encoding="utf-8"))


# --- 커밋된 파일 불변식 ---


def test_committed_marketplace_entries_valid_form():
    probs = [p for e in _doc()["plugins"] for p in entry_problems(e)]
    assert not probs, "marketplace 엔트리 형태 위반:\n" + "\n".join(probs)


def test_plugin_json_has_no_version():
    pj = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))
    assert "version" not in pj, (
        "plugin.json에 version 금지 — 해석 순서상 plugin.json이 marketplace 엔트리를 조용히 "
        "덮는다(CLAUDE.md 불변식)."
    )


# --- 순수 함수 단위 테스트 (무git 픽스처) ---


def test_relative_no_version_ok():
    assert entry_problems({"name": "okf", "source": "./plugins/okf"}) == []


def test_object_source_rejected_breaks_symlink():
    entry = {
        "name": "okf",
        "source": {"source": "git-subdir", "url": "o/r", "path": "plugins/okf", "ref": "v0.5.1"},
    }
    assert any("심링크" in p for p in entry_problems(entry))


def test_version_field_rejected_freezes_updates():
    probs = entry_problems({"name": "okf", "source": "./x", "version": "0.5.1"})
    assert any("자동 업데이트가 동결" in p for p in probs)


def test_non_dotslash_relative_rejected():
    assert any("'./'로 시작" in p for p in entry_problems({"name": "okf", "source": "plugins/okf"}))
