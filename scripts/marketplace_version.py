"""마켓플레이스 버전 핀 — 플러그인 배포 메타(.claude-plugin/marketplace.json)를 릴리스
태그에 고정한다 (플러그인 버저닝 시스템).

## 왜

`/plugin marketplace add`로 이 repo를 추가한 소비처는 marketplace.json을 읽어 플러그인을
가져온다. Claude Code의 버전 해석 순서는 **plugin.json → marketplace 엔트리 → 커밋 SHA**다.
이 repo는 plugin.json에 version을 두지 않으므로(불변식), marketplace 엔트리가 유일한
"사람이 읽는 버전" 자리다.

## 두 단계 수명주기

- **Phase P (첫 릴리스 전, 태그 0개)**: 엔트리에 version 없음 + 상대경로 소스(`./plugins/okf`)
  → 커밋 SHA 추적. main HEAD를 따라 매 커밋 자동 업데이트. 아직 핀할 릴리스가 없으니 정상.
- **Phase R (태그 ≥1개)**: 엔트리를 최신 릴리스 태그에 핀 —
  `source`를 git-subdir(자기 참조)로 바꾸고 `ref=vX.Y.Z` + `version=X.Y.Z`를 동기로 단다.
  소비처는 main HEAD가 아니라 큐레이션된 릴리스를 받고, 다음 컷에서 version이 바뀔 때만
  업데이트된다(docs/releasing.md의 태그-핀 철학과 일치).

## 계층 (기존 버전 도구와 대칭)

- 내부 정합(version↔ref 동기·clean SemVer·자기 참조 url)은 git 없이 `test_marketplace_
  version.py`가 CI에서 강제한다(회귀 계약) — `test_version_sync.py`와 같은 계층.
- "최신 태그와 일치하는가"는 git이 필요하므로 이 CLI가 릴리스 때 검증·도출한다
  — `next_version.py`와 같은 계층(게이트=무git, 도출/검증=git).

무의존(stdlib)·오프라인·결정론. git 배관(`_tags`)은 release_notes와 공유(단일 원천).
stdout은 도출 결과(핀된 marketplace.json), 근거·검증 결과는 stderr로 낸다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# git 배관은 release_notes와 공유 — 태그 열거를 재사용(단일 원천).
from release_notes import _tags

_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = _ROOT / ".claude-plugin" / "marketplace.json"

# 모노레포 내 플러그인을 태그에 핀할 때 쓰는 소스 타입(git-subdir: sparse partial clone).
SELF_SOURCE = "git-subdir"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")


def load(path: Path = MARKETPLACE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def self_url(doc: dict) -> str:
    """이 마켓플레이스의 자기 참조 슬러그 — owner.name/name. 제3자·소비처 repo가 아니라
    자기 자신을 git-subdir url로 가리킨다(공개 repo 목적지-무참조와 무관: 자기 참조)."""
    return f"{doc['owner']['name']}/{doc['name']}"


def classify(entry: dict) -> str:
    """엔트리 형태 판별 — pre-release(상대경로) / pinned(git-subdir) / unknown."""
    src = entry.get("source")
    if isinstance(src, str):
        return "pre-release"
    if isinstance(src, dict) and src.get("source") == SELF_SOURCE:
        return "pinned"
    return "unknown"


def problems(entry: dict, url: str) -> list[str]:
    """한 플러그인 엔트리의 내부 정합 위반 목록(빈 리스트 = 정상). git 불필요."""
    name = entry.get("name", "<이름없음>")
    src = entry.get("source")
    kind = classify(entry)
    out: list[str] = []
    if kind == "pre-release":
        if not src.startswith("./"):
            out.append(f"{name}: 문자열 소스는 상대경로(./...)여야 함 — {src!r}")
        if "version" in entry:
            out.append(
                f"{name}: 상대경로(SHA 추적) 소스에 정적 version을 박으면 자동 업데이트가 "
                "멈춘다 — 핀하려면 source를 git-subdir로 바꿔라(Phase R)"
            )
    elif kind == "pinned":
        if src.get("url") != url:
            out.append(f"{name}: source.url은 자기 참조({url})여야 함 — {src.get('url')!r}")
        if not src.get("path"):
            out.append(f"{name}: git-subdir 소스에 path 필요")
        ref = src.get("ref", "")
        m = _TAG.match(ref)
        if not m:
            out.append(f"{name}: source.ref는 릴리스 태그 vX.Y.Z여야 함 — {ref!r}")
        ver = entry.get("version", "")
        if not _SEMVER.match(ver):
            out.append(f"{name}: version은 clean SemVer X.Y.Z여야 함 — {ver!r}")
        if m and _SEMVER.match(ver) and m.group(1) != ver:
            out.append(f"{name}: version({ver})과 ref({ref})가 불일치 — 둘은 같은 릴리스여야 함")
    else:
        out.append(
            f"{name}: 알 수 없는 소스 형식 — 상대경로(Phase P) 또는 git-subdir(Phase R)만 허용"
        )
    return out


def pinned_entry(entry: dict, version: str, url: str) -> dict:
    """엔트리를 주어진 릴리스 버전으로 핀한 형태로 반환(name·description 등 다른 키는 보존)."""
    if not _SEMVER.match(version):
        raise ValueError(f"버전은 X.Y.Z여야 함 — {version!r}")
    src = entry.get("source")
    if isinstance(src, str) and src.startswith("./"):
        path = src[2:]
    elif isinstance(src, dict):
        path = src.get("path")
    else:
        path = None
    if not path:
        raise ValueError(f"{entry.get('name')}: path를 도출할 수 없음 — {src!r}")
    out = dict(entry)
    out["source"] = {"source": SELF_SOURCE, "url": url, "path": path, "ref": f"v{version}"}
    out["version"] = version
    return out


def check(doc: dict, tags: list[str]) -> list[str]:
    """커밋된 marketplace.json + git 태그로 배포 정합을 검증 → 문제 목록(빈 = 정상).

    내부 정합(problems)에 더해, 태그 상태에 맞는 단계인지 본다:
    태그 0개면 모든 엔트리가 Phase P여야, 태그 ≥1개면 최신 태그로 핀된 Phase R여야 한다.
    """
    url = self_url(doc)
    entries = doc.get("plugins", [])
    out = [p for e in entries for p in problems(e, url)]
    latest = tags[-1] if tags else None
    if latest is None:
        for e in entries:
            if classify(e) != "pre-release":
                out.append(
                    f"{e.get('name')}: 태그가 없는데 핀됨 — 첫 릴리스 전엔 SHA 추적(Phase P)이어야"
                )
    else:
        want_ver = _TAG.match(latest).group(1)
        for e in entries:
            kind = classify(e)
            if kind == "pre-release":
                out.append(
                    f"{e.get('name')}: 태그 {latest} 존재 — Phase R로 핀해야"
                    f"(version {want_ver}, ref {latest})"
                )
            elif kind == "pinned" and (
                e.get("version") != want_ver or e["source"].get("ref") != latest
            ):
                out.append(
                    f"{e.get('name')}: 최신 태그 {latest}와 드리프트 — 현재 "
                    f"version={e.get('version')} ref={e['source'].get('ref')} "
                    f"(기대: {want_ver}/{latest})"
                )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="marketplace 버전 핀 검증·도출 (stdlib·무의존)")
    p.add_argument(
        "version",
        nargs="?",
        help="이 버전으로 핀한 marketplace.json을 stdout에 출력(예: 0.1.0). 생략 시 검증 모드.",
    )
    args = p.parse_args(argv)
    doc = load()

    if args.version:
        # 도출(릴리스 PR용) — 엔트리를 태그에 핀한 전체 marketplace.json을 낸다. 사람이 검토·기입.
        url = self_url(doc)
        doc["plugins"] = [pinned_entry(e, args.version, url) for e in doc.get("plugins", [])]
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0

    # 검증(릴리스 후) — 최신 태그와 정합 확인.
    issues = check(doc, _tags())
    if issues:
        for msg in issues:
            print(f"  ✗ {msg}", file=sys.stderr)
        print(f"marketplace 버전 검증 실패 ({len(issues)}건)", file=sys.stderr)
        return 1
    tags = _tags()
    state = "Phase P(태그 없음 — SHA 추적)" if not tags else f"Phase R(핀 {tags[-1]})"
    print(f"marketplace 버전 정합 OK — {state}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
