"""브랜치·커밋·PR 정책 게이트 — docs/branching.md의 규약을 기계 판정으로 강제한다.

문서에만 있고 검사가 없던 계층(브랜치 이름·제목 프리픽스·PR 본문 구조·모델 식별자)을
막는다. 코드 계층 불변식(벤더 바이트·판정 상수·파스 1회·무참조)은 이미 각자 게이트가
있으므로 여기서 중복하지 않는다.

판정 대상은 **스쿼시 때 main에 남는 것 전부**다. repo 설정이
``squash_merge_commit_title=PR_TITLE`` + ``squash_merge_commit_message=COMMIT_MESSAGES``
이므로 main에 새겨지는 것은 PR 제목 **과 브랜치 커밋 메시지 본문**이다. 그래서 제목만
보면 부족하다 — 실제로 세션 공유 링크는 PR 본문이 아니라 커밋 트레일러로 흘러들었다.

판정 상수를 여기서 새로 만들지 않는다:

- 타입 어휘는 ``release_notes``의 CATEGORIES·EXCLUDED에서 파생한다. 릴리스 노트가
  아는 타입과 게이트가 허용하는 타입이 갈리면 오타(``fet:``)가 조용히 "기타"로 빠져
  ``next_version``의 범프 판정을 바꾼다 — 어휘를 한 곳에 두어 그 실수를 막는다.
- PR 본문의 필수 섹션은 ``.github/pull_request_template.md``에서 읽는다. 템플릿을
  고치면 게이트가 따라온다.

종료코드: 0 통과 / 1 위반 / 2 실행 오류.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from release_notes import CATEGORIES, EXCLUDED

_ROOT = Path(__file__).resolve().parent.parent
PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"

# 릴리스 노트가 분류하는 타입 = 게이트가 아는 타입의 토대(단일 원천).
_RELEASE_TYPES = frozenset(t for _, types in CATEGORIES for t in types) | frozenset(EXCLUDED)
# 릴리스 노트는 "기타"로 묶지만 docs/branching.md가 어휘로 인정하는 타입.
EXTRA_TYPES = frozenset({"quality", "refactor"})
KNOWN_TYPES = _RELEASE_TYPES | EXTRA_TYPES

# 에이전트 세션이 자동 생성하는 브랜치 접두(docs/branching.md) — 타입 어휘 밖의 예외.
AGENT_PREFIX = "claude"

_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?: (?P<rest>.+)$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
_PR_SUFFIX = re.compile(r"\(#\d+\)\s*$")
_HEADING = re.compile(r"^##\s+(?P<text>.+?)\s*$", re.MULTILINE)

# 모델 식별자 = "이름 + 버전"만 노린다. 제품명 'Claude Code'·트레일러 'Co-authored-by:
# Claude'·브랜치 접두 'claude/'는 버전이 없으므로 걸리지 않는다(오탐 0을 이력으로 확인).
_MODEL_ID = re.compile(
    r"\b(?:claude-(?:opus|sonnet|haiku)-[\d.]|(?:opus|sonnet|haiku)\s+\d|gpt-[\d]|gemini-[\d])",
    re.IGNORECASE,
)

# 에이전트 세션 공유 링크 — 개인 세션을 가리키는 사적 URL이라 공개 repo에 남기지 않는다.
# 트레일러 키와 URL을 함께 막는다(키만 남고 값이 비어도 흔적이 남으므로).
_SESSION_LINK = re.compile(
    r"^\s*(?:claude-session|session-link|session-url)\s*:"
    r"|https?://(?:claude\.ai/code/session|chatgpt\.com/share|chat\.openai\.com/share"
    r"|gemini\.google\.com/share|g\.co/gemini/share)\S*",
    re.IGNORECASE | re.MULTILINE,
)


def required_pr_sections(template: Path | None = None) -> list[str]:
    """PR 템플릿의 `## ` 헤딩 목록. 이게 본문 필수 섹션의 단일 원천이다."""
    path = template or PR_TEMPLATE
    return [m.group("text") for m in _HEADING.finditer(path.read_text(encoding="utf-8"))]


def _norm_heading(text: str) -> str:
    """헤딩 비교용 정규화 — 괄호 보충설명과 대소문자·여백 차이를 무시한다.

    템플릿의 `## 검증 (완료 기준 매핑)`을 본문이 `## 검증`으로 적어도 통과시킨다.
    구조를 요구하되 문구까지 복사하도록 강요하지는 않는다.
    """
    return text.split("(")[0].strip().lower()


def check_branch_name(name: str) -> list[str]:
    """`<타입>/<슬러그>` 또는 `claude/<슬러그>` 판정."""
    out: list[str] = []
    if not name:
        return ["브랜치 이름이 비어 있습니다"]
    if name == "main" or re.match(r"^v\d", name):
        return [f"`{name}`은 main·릴리스 태그 이름과 겹칩니다"]
    if "/" not in name:
        return [f"`{name}`에 타입 접두가 없습니다 — `<타입>/<슬러그>` 형식을 씁니다"]

    prefix, slug = name.split("/", 1)
    if prefix != AGENT_PREFIX and prefix not in KNOWN_TYPES:
        allowed = ", ".join(sorted(KNOWN_TYPES | {AGENT_PREFIX}))
        out.append(f"브랜치 타입 `{prefix}`는 어휘 밖입니다 — 허용: {allowed}")
    if not _SLUG.match(slug):
        out.append(f"슬러그 `{slug}`는 소문자·숫자·하이픈만 씁니다(대문자·공백·`_` 불가)")
    return out


def check_subject(subject: str, *, kind: str = "PR 제목") -> list[str]:
    """Conventional Commits 프리픽스 판정. 스쿼시 제목이 main에 남으므로 여기가 핵심이다."""
    out: list[str] = []
    if not subject:
        return [f"{kind}이 비어 있습니다"]
    if subject != subject.strip():
        # 실제 사고: 전역 prepare-commit-msg 훅이 선두 공백을 넣어 main에 2건 착륙했다.
        out.append(f"{kind} 앞뒤에 공백이 있습니다 — `{subject}`")

    stripped = subject.strip()
    m = _SUBJECT.match(stripped)
    if not m:
        out.append(f"{kind}이 `<타입>: <요약>` 형식이 아닙니다 — `{stripped}`")
        return out

    ctype = m.group("type")
    if ctype not in KNOWN_TYPES:
        out.append(f"타입 `{ctype}`는 어휘 밖입니다 — 허용: {', '.join(sorted(KNOWN_TYPES))}")
    if _PR_SUFFIX.search(stripped):
        # 스쿼시 때 GitHub이 `(#NN)`을 붙이므로 제목에 미리 넣으면 두 번 붙는다.
        out.append(f"{kind}에 `(#NN)`을 직접 넣지 않습니다 — 스쿼시 때 자동으로 붙습니다")
    return out


def check_pr_body(body: str, template: Path | None = None) -> list[str]:
    """PR 본문이 템플릿 구조를 갖췄는지 판정(섹션 목록은 템플릿에서 읽는다)."""
    if not (body or "").strip():
        return ["PR 본문이 비어 있습니다 — 템플릿 구조를 채웁니다"]

    present = {_norm_heading(m.group("text")) for m in _HEADING.finditer(body)}
    missing = [s for s in required_pr_sections(template) if _norm_heading(s) not in present]
    if missing:
        return ["PR 본문에 템플릿 섹션이 없습니다: " + ", ".join(f"`## {s}`" for s in missing)]
    return []


def check_no_model_identifier(text: str, *, where: str) -> list[str]:
    """공개 repo 참조 방향 정책 — 모델 식별자(이름+버전)를 남기지 않는다."""
    hits = sorted({m.group(0) for m in _MODEL_ID.finditer(text or "")})
    if hits:
        found = ", ".join(f"`{h}`" for h in hits)
        return [f"{where}에 모델 식별자가 있습니다: {found} — 버전 없는 표기를 씁니다"]
    return []


def check_no_session_link(text: str, *, where: str) -> list[str]:
    """에이전트 세션 공유 링크를 남기지 않는다 — 개인 세션을 가리키는 사적 URL이다."""
    hits = sorted({m.group(0).strip() for m in _SESSION_LINK.finditer(text or "")})
    if hits:
        found = ", ".join(f"`{h}`" for h in hits)
        return [f"{where}에 세션 공유 링크가 있습니다: {found} — 공개 repo에 남기지 않습니다"]
    return []


def _leak_checks(text: str, *, where: str) -> list[str]:
    """공개 repo 참조 방향 정책 묶음 — main에 새겨지는 모든 텍스트에 적용한다."""
    return check_no_model_identifier(text, where=where) + check_no_session_link(text, where=where)


def check_pr(head_ref: str, title: str, body: str, commits: list[str] | None = None) -> list[str]:
    """PR 하나에 대한 전수 판정.

    ``commits``는 브랜치의 커밋 메시지 전문 목록이다. 스쿼시가 이 본문들을 main에
    합쳐 넣으므로(``squash_merge_commit_message=COMMIT_MESSAGES``) 유출 검사 대상이다.
    """
    out = check_branch_name(head_ref)
    out += check_subject(title, kind="PR 제목")
    out += check_pr_body(body)
    out += _leak_checks(title, where="PR 제목")
    out += _leak_checks(body, where="PR 본문")
    for i, msg in enumerate(commits or [], 1):
        out += _leak_checks(msg, where=f"커밋 메시지 #{i}")
    return out


def commit_messages(base: str, head: str) -> list[str]:
    """`base..head` 범위의 커밋 메시지 전문 목록(스쿼시 시 main에 합쳐질 본문).

    구분자는 ``-z``(레코드 NUL 종료)로 git에게 맡긴다. NUL을 ``--format`` 문자열에
    직접 넣으면 argv에 실을 수 없어 ``ValueError: embedded null byte``가 난다.
    """
    out = subprocess.run(
        ["git", "log", "-z", "--format=%B", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [m.strip() for m in out.split("\0") if m.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="branch_policy", description="브랜치·커밋·PR 정책 게이트 (docs/branching.md)"
    )
    ap.add_argument("--check-pr", action="store_true", help="PR 컨텍스트를 env에서 읽어 전수 판정")
    ap.add_argument("--branch", help="브랜치 이름만 판정")
    ap.add_argument("--subject", help="커밋·PR 제목만 판정")
    ap.add_argument("--subject-file", help="제목을 파일에서 읽어 판정(commit-msg 훅용)")
    args = ap.parse_args(argv)

    problems: list[str] = []
    if args.check_pr:
        head_ref = os.environ.get("PR_HEAD_REF", "")
        title = os.environ.get("PR_TITLE", "")
        body = os.environ.get("PR_BODY", "")
        base_sha = os.environ.get("PR_BASE_SHA", "")
        head_sha = os.environ.get("PR_HEAD_SHA", "")
        if not head_ref or not title:
            print("PR_HEAD_REF·PR_TITLE 환경변수가 필요합니다", file=sys.stderr)
            return 2
        commits: list[str] | None = None
        if base_sha and head_sha:
            try:
                commits = commit_messages(base_sha, head_sha)
            except subprocess.CalledProcessError as e:
                # 이력이 얕으면(fetch-depth) 범위를 못 읽는다 — 조용히 넘기지 않는다.
                print(f"커밋 메시지를 읽지 못했습니다: {e}", file=sys.stderr)
                return 2
        problems = check_pr(head_ref, title, body, commits)
    elif args.subject_file:
        try:
            raw = Path(args.subject_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"제목 파일을 읽지 못했습니다: {e}", file=sys.stderr)
            return 2
        # commit-msg 훅은 메시지 전문을 넘긴다 — 주석을 뺀 첫 줄이 제목이다.
        lines = [ln for ln in raw.split("\n") if not ln.startswith("#")]
        subject = lines[0] if lines else ""
        problems = check_subject(subject, kind="커밋 제목")
        problems += _leak_checks(raw, where="커밋 메시지")
    elif args.branch:
        problems = check_branch_name(args.branch)
    elif args.subject:
        problems = check_subject(args.subject, kind="커밋 제목")
    else:
        ap.error("판정 대상을 지정하세요 (--check-pr · --branch · --subject · --subject-file)")

    if problems:
        print("정책 위반:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n규약: docs/branching.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
