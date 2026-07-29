"""브랜치·커밋·PR 정책 게이트 — docs/branching.md의 규약을 기계 판정으로 강제한다.

문서에만 있고 검사가 없던 계층(브랜치 이름·제목 프리픽스·PR 본문 구조·모델 식별자)을
막는다. 코드 계층 불변식(벤더 바이트·판정 상수·파스 1회·무참조)은 이미 각자 게이트가
있으므로 여기서 중복하지 않는다.

판정 대상은 **스쿼시 때 main에 남는 것 전부**다. repo 설정이
``squash_merge_commit_title=PR_TITLE`` + ``squash_merge_commit_message=COMMIT_MESSAGES``
이므로 main에 새겨지는 것은 PR 제목 **과 브랜치 커밋 메시지 본문**이다. 그래서 제목만
보면 부족하다 — 실제로 세션 공유 링크는 PR 본문이 아니라 커밋 트레일러로 흘러들었다.

판정은 PR의 **base로 3분기**한다(docs/branching.md §Epic과 유닛 분해). 한 PR이 닫는
이슈 개수·Epic 통합 브랜치 정합·본문 템플릿이 base에 따라 달라지기 때문이다.

- 일반·유닛 → ``main``: 닫는 이슈 ≤1(여러 유닛 뭉침 차단), 기본 템플릿.
- 유닛 → ``epic/<n>``: 닫는 이슈 ≤1이고 그 Epic 자신은 닫지 않음.
- ``epic/<n>`` → ``main``(통합): 개수 제한 면제, 그 Epic을 닫아야 함, Epic 템플릿.

판정 상수를 여기서 새로 만들지 않는다:

- 타입 어휘는 ``release_notes``의 CATEGORIES·EXCLUDED에서 파생한다. 릴리스 노트가
  아는 타입과 게이트가 허용하는 타입이 갈리면 오타(``fet:``)가 조용히 "기타"로 빠져
  ``next_version``의 범프 판정을 바꾼다 — 어휘를 한 곳에 두어 그 실수를 막는다.
- PR 본문의 필수 섹션은 ``.github`` 아래 PR 템플릿에서 읽는다. 템플릿을 고치면
  게이트가 따라온다. 통합 PR은 ``PULL_REQUEST_TEMPLATE/epic.md``, 나머지는 기본
  ``pull_request_template.md``를 본다.

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
_GH = _ROOT / ".github"
# 기본 PR 템플릿(GitHub이 새 PR에 자동 채움). Epic 통합 PR만 별도 템플릿을 쓴다.
PR_TEMPLATE = _GH / "pull_request_template.md"
EPIC_TEMPLATE = _GH / "PULL_REQUEST_TEMPLATE" / "epic.md"

# 통합 브랜치가 최종 착지하는 기본 브랜치. base가 이것이면 스쿼시가 main에 남는다.
DEFAULT_BRANCH = "main"

# 릴리스 노트가 분류하는 타입 = 게이트가 아는 타입의 토대(단일 원천).
_RELEASE_TYPES = frozenset(t for _, types in CATEGORIES for t in types) | frozenset(EXCLUDED)
# 릴리스 노트는 "기타"로 묶지만 docs/branching.md가 어휘로 인정하는 타입.
EXTRA_TYPES = frozenset({"quality", "refactor"})
KNOWN_TYPES = _RELEASE_TYPES | EXTRA_TYPES

# 에이전트 세션이 자동 생성하는 브랜치 접두(docs/branching.md) — 타입 어휘 밖의 예외.
AGENT_PREFIX = "claude"
# Epic 통합 브랜치 접두. 커밋 타입 어휘가 아니라 브랜치 이름 전용이다(KNOWN_TYPES 불침)
# — `epic:` 커밋 제목은 여전히 어휘 밖으로 막힌다.
EPIC_PREFIX = "epic"

_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?: (?P<rest>.+)$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")
# Epic 통합 브랜치 슬러그 — 이름에 Epic 이슈 번호를 박는다: `epic/<번호>-<슬러그>`.
_EPIC_SLUG = re.compile(r"^\d+-[a-z0-9]+(?:[-.][a-z0-9]+)*$")
_EPIC_REF = re.compile(r"^epic/(\d+)-")
_PR_SUFFIX = re.compile(r"\(#\d+\)\s*$")
_HEADING = re.compile(r"^##\s+(?P<text>.+?)\s*$", re.MULTILINE)

# GitHub이 머지 때 이슈를 닫는 키워드 + 이슈 참조. `Refs`는 닫지 않으므로 세지 않는다.
#
# 참조는 GitHub이 인정하는 세 꼴 전부를 읽는다 — `#N` · `owner/repo#N` · 이슈 URL.
# 앞의 두 꼴만 읽던 시절, URL로 적은 진짜 Closes를 게이트도 머지 후처리도 못 봤다.
_CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?:"
    r"https?://github\.com/(?P<uowner>[\w.-]+)/(?P<urepo>[\w.-]+)/issues/(?P<unum>\d+)"
    r"|(?P<qowner>[\w.-]+)/(?P<qrepo>[\w.-]+)#(?P<qnum>\d+)"
    r"|#(?P<num>\d+)"
    r")",
    re.IGNORECASE,
)

# 판정 **대상이 아닌** 구간 — GitHub이 여기 있는 `#N`을 이슈 참조로 링크하지 않는다.
# 실제 사고: 기본 PR 템플릿의 주석 예시(`<!-- 예: Closes #12 -->`)가 세어져 정상 PR이
# red가 됐고, 그 red를 푸는 유일한 처방(`policy:multi-unit`)을 따르면 머지 후처리가
# 주석의 `#12` — 이 repo에 실재하는 무관한 이슈 — 를 닫는 데까지 이어졌다.
#
# 인용(`> …`)은 **지우지 않는다**. GitHub은 인용 안 참조도 링크하므로, 지우면 게이트가
# GitHub보다 관대해져 진짜 뭉침이 새어 나간다. 판정면은 GitHub보다 넓어도 좁아도 안 된다.
# 닫는 펜스가 없으면 문서 끝까지 코드블록이다 — CommonMark/GFM 규격이고 `\Z` 대안이
# 그것을 그대로 옮긴 것이다. 그 뒤의 `Closes #N`은 GitHub도 링크하지 않으므로 세지
# 않는 것이 맞다. "뒤가 통째로 사라진다"고 여기를 고치면 판정면이 GitHub보다 넓어진다.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCED_CODE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[^\n]*$|\Z)", re.MULTILINE | re.DOTALL
)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
# 정말로 쪼갤 수 없는 원자적 변경의 탈출구(docs/branching.md). 본문에 사유와 함께 단다.
MULTI_UNIT_MARKER = "policy:multi-unit"

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


def template_for(base_ref: str, head_ref: str) -> Path:
    """PR의 base·head로 볼 템플릿을 고른다 — 통합 PR만 Epic 템플릿, 나머지는 기본."""
    if head_ref.startswith(f"{EPIC_PREFIX}/") and base_ref == DEFAULT_BRANCH:
        return EPIC_TEMPLATE
    return PR_TEMPLATE


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


def _epic_number(ref: str) -> int | None:
    """`epic/<n>-...` 브랜치 이름에서 Epic 이슈 번호를 뽑는다(아니면 None)."""
    m = _EPIC_REF.match(ref or "")
    return int(m.group(1)) if m else None


def check_branch_name(name: str) -> list[str]:
    """`<타입>/<슬러그>` · `claude/<슬러그>` · `epic/<번호>-<슬러그>` 판정."""
    out: list[str] = []
    if not name:
        return ["브랜치 이름이 비어 있습니다"]
    if name == DEFAULT_BRANCH or re.match(r"^v\d", name):
        return [f"`{name}`은 main·릴리스 태그 이름과 겹칩니다"]
    if "/" not in name:
        return [f"`{name}`에 타입 접두가 없습니다 — `<타입>/<슬러그>` 형식을 씁니다"]

    prefix, slug = name.split("/", 1)
    if prefix == EPIC_PREFIX:
        # Epic 통합 브랜치는 이름에 이슈 번호를 박아 통합 PR의 Closes와 대조된다.
        if not _EPIC_SLUG.match(slug):
            out.append(
                f"epic 브랜치는 `epic/<이슈번호>-<슬러그>` 형식입니다"
                f"(번호로 시작, 소문자 슬러그) — `{slug}`"
            )
        return out
    if prefix != AGENT_PREFIX and prefix not in KNOWN_TYPES:
        allowed = ", ".join(sorted(KNOWN_TYPES | {AGENT_PREFIX, EPIC_PREFIX}))
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


def check_pr_body(body: str, base_ref: str = DEFAULT_BRANCH, head_ref: str = "") -> list[str]:
    """PR 본문이 템플릿 구조를 갖췄는지 판정(섹션 목록은 base에 맞는 템플릿에서 읽는다)."""
    if not (body or "").strip():
        return ["PR 본문이 비어 있습니다 — 템플릿 구조를 채웁니다"]

    template = template_for(base_ref, head_ref)
    present = {_norm_heading(m.group("text")) for m in _HEADING.finditer(body)}
    missing = [s for s in required_pr_sections(template) if _norm_heading(s) not in present]
    if missing:
        which = "Epic 통합" if template == EPIC_TEMPLATE else "기본"
        joined = ", ".join(f"`## {s}`" for s in missing)
        return [f"PR 본문에 {which} 템플릿 섹션이 없습니다: {joined}"]
    return []


def strip_noncounting(body: str) -> str:
    """GitHub이 이슈 참조로 해석하지 **않는** 구간을 지운 본문.

    HTML 주석·펜스 코드블록·인라인 코드. 인용은 지우지 않는다(위 상수 주석 참조).
    """
    text = _HTML_COMMENT.sub("", body or "")
    text = _FENCED_CODE.sub("", text)
    return _INLINE_CODE.sub("", text)


def closing_issues(body: str, repo: str | None = None) -> list[int]:
    """본문의 GitHub closing 키워드가 **이 repo에서** 닫는 이슈 번호들(중복 제거, 등장 순서).

    ``repo``(``owner/name``)는 한정 참조(``owner/repo#N``·이슈 URL)가 이 repo를
    가리키는지 판정하는 기준이다. 기본값은 ``GITHUB_REPOSITORY`` 환경변수.

    다른 repo를 가리키는 한정 참조는 **세지 않는다** — 이 repo의 이슈를 닫지 않기
    때문이다. 세면 유닛 뭉침 오탐(red)과, 머지 후처리가 같은 번호의 *이 repo* 이슈를
    닫는 오작동이 동시에 난다.

    repo를 알 수 없으면(로컬 훅 등) 한정 참조를 **센다** — fail-closed. 로컬이 CI보다
    관대해지면 "로컬 통과 → CI red"가 되므로 방향을 반대로 둔다.
    """
    known = repo if repo is not None else os.environ.get("GITHUB_REPOSITORY") or None
    seen: dict[int, None] = {}
    for m in _CLOSING.finditer(strip_noncounting(body)):
        if m.group("num"):
            seen.setdefault(int(m.group("num")), None)
            continue
        if m.group("unum"):
            owner, name, num = m.group("uowner"), m.group("urepo"), m.group("unum")
        else:
            owner, name, num = m.group("qowner"), m.group("qrepo"), m.group("qnum")
        if known is None or f"{owner}/{name}".lower() == known.lower():
            seen.setdefault(int(num), None)
    return list(seen)


def _bundling_problem(closes: list[int]) -> str:
    refs = ", ".join(f"#{n}" for n in closes)
    return (
        f"한 PR이 이슈 {len(closes)}개를 닫습니다({refs}) — 유닛당 PR이 기본입니다. "
        f"정말 쪼갤 수 없는 원자적 변경이면 사유와 함께 `{MULTI_UNIT_MARKER}` 마커를 답니다"
    )


def check_closing_issues(body: str, base_ref: str, head_ref: str) -> list[str]:
    """PR이 닫는 이슈를 base로 3분기 판정 — 유닛 뭉침과 Epic 오조작을 막는다."""
    body = body or ""
    closes = closing_issues(body)
    marker = MULTI_UNIT_MARKER in body

    # 통합 PR(epic/<n> → main): 그 Epic을 닫아야 하고, 개수 제한은 면제(유닛들을 함께 참조).
    if head_ref.startswith(f"{EPIC_PREFIX}/") and base_ref == DEFAULT_BRANCH:
        n = _epic_number(head_ref)
        if n is not None and n not in closes:
            msg = f"통합 PR은 자기 Epic(`#{n}`)을 닫아야 합니다 — 본문에 `Closes #{n}`을 적습니다"
            return [msg]
        return []

    out: list[str] = []
    # 유닛 → 통합 브랜치: 통합 브랜치의 Epic 자신은 닫지 않는다(조기 Epic close 방지).
    if base_ref.startswith(f"{EPIC_PREFIX}/"):
        n = _epic_number(base_ref)
        if n is not None and n in closes:
            msg = (
                f"유닛 PR은 통합 브랜치의 Epic(`#{n}`)을 닫지 않습니다 — 자기 sub-issue를 닫습니다"
            )
            out.append(msg)

    # 일반·유닛 공통: 여러 이슈를 한 PR로 닫으면 유닛 경계가 사라진다.
    if len(closes) > 1 and not marker:
        out.append(_bundling_problem(closes))
    return out


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


def check_pr(
    head_ref: str,
    title: str,
    body: str,
    commits: list[str] | None = None,
    base_ref: str = DEFAULT_BRANCH,
) -> list[str]:
    """PR 하나에 대한 전수 판정.

    ``base_ref``는 PR이 머지될 대상 브랜치다. 닫는 이슈 규칙과 본문 템플릿이 base로
    갈리므로(§Epic과 유닛 분해) 함께 넘긴다. ``commits``는 브랜치의 커밋 메시지 전문
    목록이다. 스쿼시가 이 본문들을 main에 합쳐 넣으므로 유출 검사 대상이다.
    """
    out = check_branch_name(head_ref)
    out += check_subject(title, kind="PR 제목")
    out += check_pr_body(body, base_ref=base_ref, head_ref=head_ref)
    out += check_closing_issues(body, base_ref, head_ref)
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
        base_ref = os.environ.get("PR_BASE_REF", "") or DEFAULT_BRANCH
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
        problems = check_pr(head_ref, title, body, commits, base_ref=base_ref)
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
