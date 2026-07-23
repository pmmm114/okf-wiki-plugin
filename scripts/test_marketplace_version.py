"""마켓플레이스 버전 핀 게이트 (플러그인 버저닝 시스템).

`.claude-plugin/marketplace.json`의 플러그인 엔트리가 항상 유효한 배포 형태를 유지하도록
CI에서 강제한다(docs/releasing.md 플러그인 채널). 무git·stdlib — **내부 정합만** 본다.
"최신 태그와 일치하는가"는 git이 필요하므로 `marketplace_version.py`(릴리스 헬퍼)가 맡는다
— `test_version_sync.py`(무git 게이트) ↔ `next_version.py`(git 도출)와 같은 계층 분리다.

두 유효 형태:
- **Phase P**(첫 릴리스 전): 상대경로 소스 + version 없음(SHA 추적).
- **Phase R**(릴리스 핀): git-subdir 자기 참조 + `ref=vX.Y.Z` + `version=X.Y.Z`(둘 동기).

또한 버전 해석 순서(plugin.json → marketplace → SHA)의 전제인 "plugin.json엔 version 없음"
불변식을 여기서 함께 못박는다 — 지금까진 관례·비-strict validate뿐 자동 게이트가 없었다.

`pytest scripts`(CI `core` 잡)가 이 파일을 자동 수집하므로 위반 시 잡이 red가 된다.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import marketplace_version as mv

_ROOT = Path(__file__).resolve().parent.parent
_MARKETPLACE = _ROOT / ".claude-plugin" / "marketplace.json"
_PLUGIN_JSON = _ROOT / "plugins" / "okf" / ".claude-plugin" / "plugin.json"


def _doc() -> dict:
    return json.loads(_MARKETPLACE.read_text(encoding="utf-8"))


# --- 커밋된 파일 불변식 (실제 marketplace.json / plugin.json을 검사) ---


def test_plugin_json_has_no_version():
    pj = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))
    assert "version" not in pj, (
        "plugin.json에 version 금지 — 해석 순서상 plugin.json이 marketplace 엔트리를 조용히 "
        "덮어 버전 핀을 무력화한다(CLAUDE.md 불변식)."
    )


def test_committed_marketplace_entries_valid_form():
    doc = _doc()
    url = mv.self_url(doc)
    probs = [p for e in doc["plugins"] for p in mv.problems(e, url)]
    assert not probs, "marketplace 엔트리 형태 위반:\n" + "\n".join(probs)


# --- 순수 함수 단위 테스트 (무git 픽스처) ---


def test_classify_forms():
    assert mv.classify({"source": "./plugins/okf"}) == "pre-release"
    assert mv.classify({"source": {"source": "git-subdir"}}) == "pinned"
    assert mv.classify({"source": {"source": "github"}}) == "unknown"


def test_prerelease_static_version_is_footgun():
    # 상대경로 소스에 정적 version → 자동 업데이트 동결. 게이트가 막아야 하는 대표 실수.
    probs = mv.problems({"name": "x", "source": "./x", "version": "0.1.0"}, "o/r")
    assert any("자동 업데이트가" in p for p in probs)


def test_prerelease_clean_form_ok():
    assert mv.problems({"name": "x", "source": "./plugins/x"}, "o/r") == []


def _pinned(url="o/r", ref="v0.1.0", ver="0.1.0"):
    return {
        "name": "x",
        "source": {"source": "git-subdir", "url": url, "path": "plugins/x", "ref": ref},
        "version": ver,
    }


def test_pinned_clean_form_ok():
    assert mv.problems(_pinned(), "o/r") == []


def test_pinned_version_ref_must_match():
    assert any("불일치" in p for p in mv.problems(_pinned(ref="v0.2.0", ver="0.1.0"), "o/r"))


def test_pinned_ref_must_be_release_tag():
    assert any("릴리스 태그" in p for p in mv.problems(_pinned(ref="main"), "o/r"))


def test_pinned_url_must_be_self_reference():
    # 제3자·소비처 repo를 소스로 박지 않는다(자기 참조만).
    assert any("자기 참조" in p for p in mv.problems(_pinned(url="third/party"), "o/r"))


def test_unknown_source_rejected():
    assert any("알 수 없는" in p for p in mv.problems({"name": "x", "source": 123}, "o/r"))


def test_pinned_entry_builder_roundtrips_through_gate():
    entry = {"name": "okf", "description": "keep me", "source": "./plugins/okf"}
    out = mv.pinned_entry(entry, "0.1.0", "o/r")
    assert out["version"] == "0.1.0"
    assert out["source"] == {
        "source": "git-subdir",
        "url": "o/r",
        "path": "plugins/okf",
        "ref": "v0.1.0",
    }
    assert out["description"] == "keep me"  # 다른 키 보존
    assert mv.problems(out, "o/r") == []  # 도출 결과는 게이트를 통과해야


# --- check(): 태그 상태별 단계 정합 (git 배관은 주입한 픽스처로 대체) ---


def test_check_no_tags_requires_prerelease():
    doc = {"name": "m", "owner": {"name": "o"}, "plugins": [{"name": "x", "source": "./x"}]}
    assert mv.check(doc, tags=[]) == []  # 태그 0개 + Phase P → OK
    pinned_doc = {"name": "m", "owner": {"name": "o"}, "plugins": [_pinned(url="o/m")]}
    assert any("태그가 없는데 핀됨" in p for p in mv.check(pinned_doc, tags=[]))


def test_check_with_tag_requires_latest_pin():
    base = {"name": "m", "owner": {"name": "o"}}
    # 태그 있는데 아직 Phase P → 핀 필요
    pre = {**base, "plugins": [{"name": "x", "source": "./x"}]}
    assert any("Phase R로 핀해야" in p for p in mv.check(pre, tags=["v0.1.0"]))
    # 최신 태그로 핀 → OK
    ok = {**base, "plugins": [_pinned(url="o/m", ref="v0.2.0", ver="0.2.0")]}
    assert mv.check(ok, tags=["v0.1.0", "v0.2.0"]) == []
    # 옛 태그에 핀(드리프트) → 실패
    drift = {**base, "plugins": [_pinned(url="o/m", ref="v0.1.0", ver="0.1.0")]}
    assert any("드리프트" in p for p in mv.check(drift, tags=["v0.1.0", "v0.2.0"]))


# --- 무의존 계약 (next_version.py와 같은 AST 검사) ---


def test_imports_stdlib_and_sibling_only():
    src = (_ROOT / "scripts" / "marketplace_version.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__", "release_notes"}
    extra = imported - allowed
    assert not extra, f"허용 밖 import: {extra}"
