"""문서간 링크 이식성 게이트 — 원격 정적 웹뷰에서 깨지는 링크 형태 차단.

이 repo의 브라우징 대상 문서(.okf 번들·README·docs·플러그인 문서)는 GitHub 같은
정적 웹뷰에서 그대로 읽힌다. 거기서 깨지는 두 링크 형태를 실측으로 특정해 금지한다:

- **선두 `/` 절대 링크**(`](/foo.md)`): 정적 웹뷰는 이를 번들 루트가 아니라 도메인
  루트로 해소한다 → 깨짐(스펙 §5.1의 번들-상대 절대형은 OKF-인지 소비처에서만 성립).
- **말미 `/` 베어 디렉터리 링크**(`](dir/)`): `blob/<ref>/…/dir/`은 파일이 아니라
  400이다(무슬래시 `dir`은 tree로 301 리다이렉트되어 열리므로 대상 아님).

계층 분리: 이 게이트는 **형태(이식성)** 만 본다. **대상 존재**(dangling)는 엔진
`okf validate --strict`가 §5.3 관용과 함께 판정하며 CI가 이미 `.okf`에 실행한다(ci.yml).
엔진은 목적지를 모르므로(무참조 불변식) 이 판정은 엔진 밖 repo-메타 계층인 여기
(`scripts/`, `pytest scripts`가 CI `core` 잡에서 자동 수집)에 둔다.

제외: `okf-core/vendor/**`(바이트 프리즈 스펙 — 링크는 형식 예시), `okf-core/tests/**`
(엔진의 §5.3 관용을 실증하는 계약 입력), 심볼릭 링크(reference/SPEC.md → 벤더 스펙),
가상환경·캐시. 인라인 링크만 본다(엔진 파서의 인라인·펜스제외 판단과 정합). stdlib 전용.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# `](<url>)` 또는 `](url "title")`의 url 토큰. 이미지 `![](…)`도 부분일치로 함께 걸린다.
_LINK = re.compile(r"\]\(\s*(<[^>]+>|[^)\s]+)")
_FENCE = re.compile(r"(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"`[^`]*`")
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _internal_link_targets(text: str):
    """(lineno, url) 스트림 — 펜스·인라인코드 제외, 외부/앵커 제외한 내부 링크만.

    펜스(``` / ~~~) 내부는 렌더링 시 링크가 아니라 코드라 제외한다(엔진 파서가 본문
    링크에서 펜스를 빼는 것과 같은 판단). 외부 스킴·프로토콜상대·순수 앵커는 이식성
    대상이 아니라 건너뛴다.
    """
    fence = None
    for lineno, line in enumerate(text.split("\n"), 1):
        m = _FENCE.match(line.lstrip())
        if m:
            marker = m.group(1)[0]
            if fence is None:
                fence = marker
                continue
            if marker == fence:
                fence = None
                continue
        if fence is not None:
            continue
        clean = _INLINE_CODE.sub("", line)
        for mt in _LINK.finditer(clean):
            url = mt.group(1).strip("<>").strip()
            if not url or url.startswith("#"):
                continue
            if url.startswith("//") or _SCHEME.match(url):
                continue
            yield lineno, url


def _nonportable(url: str) -> str | None:
    """이식성 위반 사유(없으면 None) — 선두 `/`(절대) · 말미 `/`(베어 디렉터리)."""
    if url.startswith("/"):
        return "절대 링크(선두 /) → 정적 웹뷰에서 도메인 루트로 해소. 상대 링크로 바꿀 것"
    if url.split("#", 1)[0].endswith("/"):
        return "베어 디렉터리 링크(말미 /) → blob 경로에서 400. 그 디렉터리의 index.md로 바꿀 것"
    return None


def _in_scope_docs() -> list[Path]:
    """브라우징 대상 실문서(.md)만 — 벤더·픽스처·심볼릭·캐시·가상환경 제외."""
    out = []
    for p in sorted(_ROOT.rglob("*.md")):
        if p.is_symlink():
            continue
        rel = p.relative_to(_ROOT)
        if set(rel.parts) & {".git", ".venv", "node_modules", ".pytest_cache", "__pycache__"}:
            continue
        if rel.as_posix().startswith(("okf-core/vendor/", "okf-core/tests/")):
            continue
        out.append(p)
    return out


def test_docs_have_no_nonportable_internal_links():
    problems = []
    for p in _in_scope_docs():
        rel = p.relative_to(_ROOT).as_posix()
        for lineno, url in _internal_link_targets(p.read_text(encoding="utf-8")):
            reason = _nonportable(url)
            if reason:
                problems.append(f"{rel}:{lineno}  `{url}`  — {reason}")
    assert not problems, "원격 정적 웹뷰에서 깨지는 문서간 링크:\n  " + "\n  ".join(problems)


def test_scan_flags_both_forms_and_skips_safe_ones():
    """스캔 로직 자기검증 — 두 위반형만 잡고 외부·앵커·상대·펜스·인라인코드는 통과."""
    sample = "\n".join(
        [
            "* [abs](/tables/customers.md)",  # 위반: 절대
            "* [dir](datasets/)",  # 위반: 베어 디렉터리
            "* [rel](orders.md)",  # 안전: 상대 파일
            "* [sub](datasets/index.md)",  # 안전: 디렉터리의 index
            "* [ext](https://example.com/)",  # 안전: 외부(말미 / 있어도)
            "* [anchor](#section)",  # 안전: 순수 앵커
            "* [frag](orders.md#schema)",  # 안전: 상대+프래그먼트
            "```",
            "* [fenced-abs](/ignored.md)",  # 안전: 펜스 내부(코드)
            "```",
            "인라인 `[x](/also-ignored.md)` 코드",  # 안전: 인라인 코드
        ]
    )
    flagged = [url for _ln, url in _internal_link_targets(sample) if _nonportable(url)]
    assert flagged == ["/tables/customers.md", "datasets/"]


def test_scope_boundary():
    """스코프 감도 — 실문서 포함·계약/벤더/심볼릭 제외를 계약으로 고정."""
    rels = {p.relative_to(_ROOT).as_posix() for p in _in_scope_docs()}
    assert {"README.md", ".okf/architecture.md"} <= rels  # 브라우징 문서 포함
    assert not any(r.startswith("okf-core/vendor/") for r in rels)  # 벤더 제외
    assert not any("fixtures" in r for r in rels)  # 픽스처 계약 제외
    assert "plugins/okf/skills/okf/reference/SPEC.md" not in rels  # 심볼릭(벤더) 제외
