"""브랜치·커밋·PR 정책 게이트 테스트 — 판정 계약과 실제 사고 회귀를 함께 고정한다.

여기 적힌 "실제로 착륙한" 사례는 만들어 낸 예시가 아니라 `main` 이력에서 실측한
것이다(정비 시점 103커밋 기준). 게이트가 막아야 할 대상이 가설이 아님을 남긴다.
"""

from __future__ import annotations

import subprocess

import branch_policy as bp
import pytest
from release_notes import CATEGORIES, EXCLUDED

# --- 어휘 단일 원천 ---------------------------------------------------------


def test_known_types_covers_release_notes_vocabulary():
    """릴리스 노트가 분류하는 타입은 전부 게이트 어휘 안에 있어야 한다.

    갈리면 오타 아닌 정상 타입이 막히거나, 반대로 릴리스 노트가 모르는 타입이
    통과해 `next_version`의 범프 판정을 조용히 바꾼다.
    """
    release_types = {t for _, types in CATEGORIES for t in types} | set(EXCLUDED)
    assert release_types <= bp.KNOWN_TYPES


def test_documented_types_are_allowed():
    """docs/branching.md가 어휘로 드는 타입은 전부 통과해야 한다."""
    for t in ("feat", "fix", "docs", "ci", "quality", "refactor", "test"):
        assert bp.check_subject(f"{t}: 요약", kind="제목") == [], t


# --- 브랜치 이름 -------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "feat/fixture-suite",
        "fix/vendor-sync-path",
        "docs/branching-strategy",
        "ci/release-tag-workflow",
        "claude/branch-strategy-docs-qg1xpz",  # 에이전트 세션 접두는 어휘 예외
        "refactor/study-scope",
        "release/v0-4-0-cut",
    ],
)
def test_branch_name_accepts_conforming(name):
    assert bp.check_branch_name(name) == []


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("fixture-suite", "타입 접두가 없"),
        ("feature/x", "어휘 밖"),
        ("feat/T-P3-2-fixture-suite", "소문자"),  # 대문자 태스크 코드는 문서 규칙 위반
        ("feat/두 단어", "소문자"),
        ("feat/snake_case", "소문자"),
        ("main", "겹칩니다"),
        ("v0.4.0", "겹칩니다"),
        ("", "비어"),
    ],
)
def test_branch_name_rejects_violations(name, hint):
    problems = bp.check_branch_name(name)
    assert problems, name
    assert any(hint in p for p in problems), problems


# --- 제목 --------------------------------------------------------------------


def test_subject_accepts_scope_and_breaking():
    assert bp.check_subject("feat(study): 후보 승격", kind="제목") == []
    assert bp.check_subject("feat!: 계약 파괴", kind="제목") == []
    assert bp.check_subject("docs(readme): 구성 표 추가", kind="제목") == []


def test_subject_rejects_leading_space_regression():
    """실제 사고 회귀 — 전역 prepare-commit-msg 훅이 넣은 선두 공백이 main에 2건 착륙했다.

    (`docs(readme): Getting Started 5단계 재편 (#154)`, `feat: 스킬 §2 배치 판단 스텝 (#68)`)
    """
    landed = " docs(readme): Getting Started 5단계 재편 — 소비자 실행 경로로 정정"
    problems = bp.check_subject(landed, kind="제목")
    assert any("공백" in p for p in problems), problems


def test_subject_rejects_unknown_type_typo():
    """오타 타입은 릴리스 범프를 조용히 바꾸므로 막는다(`fet`은 feat로 안 세어진다)."""
    problems = bp.check_subject("fet: 기능 추가", kind="제목")
    assert any("어휘 밖" in p for p in problems), problems


def test_subject_rejects_missing_prefix():
    assert bp.check_subject("픽스처 스위트 추가", kind="제목")


def test_subject_rejects_manual_pr_number():
    """스쿼시 때 GitHub이 `(#NN)`을 붙이므로 제목에 미리 넣으면 두 번 붙는다."""
    problems = bp.check_subject("docs: 문서 정비 (#199)", kind="제목")
    assert any("(#NN)" in p for p in problems), problems


# --- PR 본문 -----------------------------------------------------------------


def test_pr_sections_come_from_template():
    """필수 섹션 목록은 하드코딩이 아니라 실제 템플릿에서 읽는다."""
    sections = bp.required_pr_sections()
    assert sections, "PR 템플릿에서 `## ` 헤딩을 읽지 못했습니다"
    normalized = {bp._norm_heading(s) for s in sections}
    assert {"요약", "관련 이슈", "검증", "체크리스트"} <= normalized


def test_pr_body_accepts_template_structure():
    body = "\n".join(f"## {s}\n\n내용\n" for s in bp.required_pr_sections())
    assert bp.check_pr_body(body) == []


def test_pr_body_tolerates_dropped_parenthetical():
    """템플릿의 `## 검증 (완료 기준 매핑)`을 `## 검증`으로 적어도 통과시킨다."""
    body = "\n".join(f"## {bp._norm_heading(s)}\n\n내용\n" for s in bp.required_pr_sections())
    assert bp.check_pr_body(body) == []


def test_pr_body_rejects_missing_section():
    body = "## 요약\n\n내용\n"
    problems = bp.check_pr_body(body)
    assert any("템플릿 섹션이 없습니다" in p for p in problems), problems


def test_pr_body_rejects_empty():
    assert bp.check_pr_body("")


# --- 공개 repo 유출 (참조 방향 정책) ------------------------------------------


def test_model_identifier_rejected_regression():
    """실제 사고 회귀 — `Co-Authored-By: Claude Opus 4.8`이 main에 7건 착륙했다."""
    landed = "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
    assert bp.check_no_model_identifier(landed, where="커밋")


@pytest.mark.parametrize(
    "text",
    [
        "Co-authored-by: Claude <noreply@anthropic.com>",  # 버전 없는 표기는 허용
        "Claude Code 플러그인",
        "claude/branch-x 접두",
        "엔진은 Claude를 모른다",
    ],
)
def test_model_identifier_no_false_positive(text):
    assert bp.check_no_model_identifier(text, where="커밋") == []


def test_session_link_rejected_regression():
    """실제 사고 회귀 — 세션 공유 링크가 main 103커밋 중 55건에 착륙했다."""
    landed = "Claude-Session: https://claude.ai/code/session_01RA8Yr9yHfSSauYdnNpFzyZ"
    assert bp.check_no_session_link(landed, where="커밋")


def test_session_link_rejects_trailer_key_alone():
    """값이 비어도 트레일러 키만으로 흔적이 남으므로 막는다."""
    assert bp.check_no_session_link("Claude-Session:", where="커밋")


def test_session_link_rejects_bare_url():
    assert bp.check_no_session_link("참고 https://claude.ai/code/session_abc123", where="커밋")


@pytest.mark.parametrize(
    "text",
    [
        "https://claude.ai/code 문서",  # session 경로가 아니면 대상 아님
        "https://github.com/pmmm114/okf-wiki-plugin/issues/72",
        "세션 종료 후 다시 확인",
    ],
)
def test_session_link_no_false_positive(text):
    assert bp.check_no_session_link(text, where="커밋") == []


# --- 전수 판정 ---------------------------------------------------------------


def _good_body():
    return "\n".join(f"## {s}\n\n내용\n" for s in bp.required_pr_sections())


def test_check_pr_passes_conforming():
    assert bp.check_pr("ci/branch-policy-gate", "ci: 정책 게이트 추가", _good_body()) == []


def test_check_pr_scans_commit_messages():
    """스쿼시가 커밋 본문을 main에 합치므로 커밋 메시지도 유출 검사 대상이다."""
    commits = ["ci: 게이트\n\nClaude-Session: https://claude.ai/code/session_abc\n"]
    problems = bp.check_pr("ci/x", "ci: 게이트", _good_body(), commits)
    assert any("커밋 메시지 #1" in p and "세션 공유 링크" in p for p in problems), problems


def test_check_pr_accumulates_all_violations():
    """한 번에 전부 보여 준다 — 고치고 다시 돌리는 왕복을 줄인다."""
    problems = bp.check_pr("Feature/Bad_Name", "잘못된 제목", "")
    assert len(problems) >= 3, problems


# --- CLI 계약 ----------------------------------------------------------------


def test_cli_exit_codes(tmp_path, monkeypatch):
    assert bp.main(["--branch", "feat/ok"]) == 0
    assert bp.main(["--branch", "Bad_Branch"]) == 1

    monkeypatch.delenv("PR_HEAD_REF", raising=False)
    monkeypatch.delenv("PR_TITLE", raising=False)
    assert bp.main(["--check-pr"]) == 2  # 컨텍스트 없음 = 실행 오류


def test_cli_subject_file_strips_comments(tmp_path):
    """commit-msg 훅은 메시지 전문을 넘긴다 — git 주석 줄은 제목이 아니다."""
    f = tmp_path / "COMMIT_EDITMSG"
    f.write_text("# 주석\ndocs: 제목\n\n본문\n", encoding="utf-8")
    assert bp.main(["--subject-file", str(f)]) == 0

    f.write_text("# 주석\n제목만 있고 프리픽스 없음\n", encoding="utf-8")
    assert bp.main(["--subject-file", str(f)]) == 1


# --- git 연동 (실제 호출) -----------------------------------------------------


def _repo(tmp_path):
    """커밋 2개짜리 임시 repo. 훅은 끈다 — 전역 core.hooksPath가 제목을 건드릴 수 있다."""

    def git(*a, **kw):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            **kw,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "docs: 베이스")
    base = git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    git("add", "-A")
    git(
        "commit",
        "-m",
        "ci: 게이트 추가\n\nClaude-Session: https://claude.ai/code/session_abc123\n",
    )
    return git, base, git("rev-parse", "HEAD").stdout.strip()


def test_commit_messages_reads_range(tmp_path, monkeypatch):
    """실제 git 호출 계약 — NUL 구분자를 --format에 직접 넣으면 argv에 실리지 않는다."""
    _, base, head = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    msgs = bp.commit_messages(base, head)
    assert len(msgs) == 1, msgs
    assert "Claude-Session" in msgs[0]


def test_check_pr_end_to_end_catches_commit_trailer(tmp_path, monkeypatch):
    """PR 제목·본문이 깨끗해도 커밋 트레일러의 세션 링크를 잡아야 한다."""
    _, base, head = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_HEAD_REF", "ci/gate")
    monkeypatch.setenv("PR_TITLE", "ci: 게이트 추가")
    monkeypatch.setenv("PR_BODY", _good_body())
    monkeypatch.setenv("PR_BASE_SHA", base)
    monkeypatch.setenv("PR_HEAD_SHA", head)
    assert bp.main(["--check-pr"]) == 1


def test_check_pr_reports_error_on_unreadable_range(tmp_path, monkeypatch):
    """범위를 못 읽으면 조용히 통과시키지 않고 실행 오류로 세운다."""
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_HEAD_REF", "ci/gate")
    monkeypatch.setenv("PR_TITLE", "ci: 게이트 추가")
    monkeypatch.setenv("PR_BODY", _good_body())
    monkeypatch.setenv("PR_BASE_SHA", "0" * 40)
    monkeypatch.setenv("PR_HEAD_SHA", "HEAD")
    assert bp.main(["--check-pr"]) == 2


# --- Epic 통합 브랜치 이름 (U1) ----------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["epic/189-study-accretion", "epic/72-writable-vault", "epic/1-x"],
)
def test_epic_branch_name_accepts(name):
    assert bp.check_branch_name(name) == []


@pytest.mark.parametrize(
    "name",
    [
        "epic/study-accretion",  # 번호 없음
        "epic/189-Study",  # 대문자
        "epic/189_study",  # snake_case
        "epic/189",  # 번호만, 슬러그 없음
    ],
)
def test_epic_branch_name_rejects(name):
    problems = bp.check_branch_name(name)
    assert problems, name
    assert any("이슈번호" in p for p in problems), problems


def test_epic_is_branch_prefix_not_a_commit_type():
    """`epic`은 브랜치 접두일 뿐 커밋 타입 어휘가 아니다 — `epic:` 제목은 막힌다."""
    assert "epic" not in bp.KNOWN_TYPES
    assert any("어휘 밖" in p for p in bp.check_subject("epic: 뭔가", kind="제목"))


# --- 닫는 이슈 파싱 (U1) -----------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Closes #12", [12]),
        ("closes #12 · fixes #13 · resolves #14", [12, 13, 14]),
        ("Fixed #7, Closed #7", [7]),  # 중복 제거
        ("Refs #11 · Ref #9", []),  # Refs는 닫지 않으므로 세지 않는다
        ("관련 없음 #99 텍스트", []),  # 키워드 없는 번호는 대상 아님
        ("", []),
    ],
)
def test_closing_issues_parsing(body, expected):
    assert bp.closing_issues(body) == expected


# --- 닫는 이슈 base 3분기 (U1) -----------------------------------------------


def test_closing_normal_to_main_allows_zero_or_one():
    assert bp.check_closing_issues("본문만", "main", "docs/x") == []
    assert bp.check_closing_issues("Closes #12", "main", "docs/x") == []


def test_closing_normal_to_main_rejects_bundling():
    problems = bp.check_closing_issues("Closes #12 Closes #13", "main", "feat/x")
    assert any("2개를 닫습니다" in p for p in problems), problems


def test_closing_bundling_escape_marker():
    """정말 쪼갤 수 없는 원자 변경만 마커로 예외 — docs/branching.md가 인정하는 유일한 예외."""
    body = "Closes #12 Closes #13\n\n<!-- policy:multi-unit: 한 파일 원자 변경 -->"
    assert bp.check_closing_issues(body, "main", "feat/x") == []


def test_closing_unit_to_epic_rejects_closing_the_epic():
    """유닛 PR이 통합 브랜치의 Epic 자신을 닫으면 조기 Epic close가 된다."""
    problems = bp.check_closing_issues("Closes #189", "epic/189-study", "feat/u1")
    assert any("Epic(`#189`)을 닫지 않습니다" in p for p in problems), problems


def test_closing_unit_to_epic_allows_subissue():
    assert bp.check_closing_issues("Closes #190", "epic/189-study", "feat/u1") == []


def test_closing_integration_requires_its_epic():
    """통합 PR은 자기 Epic(브랜치 이름의 번호)을 닫아야 한다."""
    problems = bp.check_closing_issues("Refs #190 #191", "main", "epic/189-study")
    assert any("자기 Epic(`#189`)을 닫아야" in p for p in problems), problems


def test_closing_integration_exempt_from_count():
    """통합 PR은 Epic + 유닛들을 함께 참조하므로 개수 제한 면제."""
    assert bp.check_closing_issues("Closes #189\nRefs #190, #191", "main", "epic/189-study") == []


# --- 템플릿 선택 (U1) --------------------------------------------------------


def test_template_for_integration_uses_epic_template():
    assert bp.template_for("main", "epic/189-study") == bp.EPIC_TEMPLATE


def test_template_for_others_use_default():
    assert bp.template_for("main", "feat/x") == bp.PR_TEMPLATE
    assert bp.template_for("epic/189-study", "feat/u1") == bp.PR_TEMPLATE  # 유닛→통합은 기본


def test_epic_template_exists_and_has_sections():
    sections = {bp._norm_heading(s) for s in bp.required_pr_sections(bp.EPIC_TEMPLATE)}
    assert {"epic 요약", "닫는 epic", "구성 유닛", "통합 검증", "체크리스트"} <= sections


def test_integration_pr_body_requires_epic_sections():
    """통합 PR에 기본 템플릿 본문을 내면 Epic 섹션 누락으로 걸린다(게이트가 템플릿을 강제)."""
    default_body = "\n".join(f"## {s}\n\n내용\n" for s in bp.required_pr_sections())
    problems = bp.check_pr_body(default_body, base_ref="main", head_ref="epic/189-study")
    assert any("Epic 통합 템플릿 섹션이 없습니다" in p for p in problems), problems


def test_check_pr_threads_base_ref():
    """전수 판정이 base를 받아 유닛→통합 규칙을 적용한다."""
    body = "\n".join(f"## {s}\n\n내용\n" for s in bp.required_pr_sections())
    problems = bp.check_pr("feat/u1", "feat: u1", body + "\nCloses #189", base_ref="epic/189-study")
    assert any("Epic(`#189`)을 닫지 않습니다" in p for p in problems), problems


def test_cli_check_pr_reads_base_ref(monkeypatch):
    """--check-pr가 PR_BASE_REF를 읽어 base 인지 판정을 적용한다."""
    good = "\n".join(f"## {s}\n\n내용\n" for s in bp.required_pr_sections())
    monkeypatch.setenv("PR_HEAD_REF", "feat/u1")
    monkeypatch.setenv("PR_TITLE", "feat: u1")
    monkeypatch.setenv("PR_BODY", good + "\nCloses #189")
    monkeypatch.setenv("PR_BASE_REF", "epic/189-study")
    monkeypatch.delenv("PR_BASE_SHA", raising=False)
    monkeypatch.delenv("PR_HEAD_SHA", raising=False)
    assert bp.main(["--check-pr"]) == 1  # 유닛이 자기 Epic을 닫음 → 위반
