"""로컬 훅 계약 게이트 — lefthook 배선이 지켜야 할 것을 고정한다.

두 가지가 이 배선의 전제다. 둘 다 조용히 어긋날 수 있어 테스트로 못 박는다.

1. **유저 전역 훅 디렉터리를 건드리지 않는다.** `lefthook install --force`는 그 시점에
   해소되는 `core.hooksPath`에 훅을 쓴다. 로컬을 먼저 잡지 않으면 전역 디렉터리에
   써서 유저의 다른 repo까지 바꾼다. 그래서 배선 스크립트가 git 설정을 **로컬로만**
   쓰는지, 전역 훅 디렉터리를 읽지도 않는지 본다.
2. **판정을 복제하지 않는다.** 훅에 규칙을 직접 적으면 CI와 다른 코드로 판정하게 되고,
   그 순간 로컬 훅은 안심이 아니라 오해가 된다. 모든 job이 `scripts/`나 CI와 같은
   명령을 부르는지 본다.

여기에 소비처 격리도 함께 본다. 루트 `pyproject.toml`은 `pip install <루트>`와
pre-commit이 소비하는 셔틀이라, 훅 매니저가 런타임 의존으로 새면 이 repo를 쓰는 쪽까지
개발 도구를 받는다.

lefthook 실행이나 네트워크는 필요 없다 — 선언 파일과 소스만 읽는다. YAML 파서를 쓰지
않는 이유는 이 테스트가 의존 없는 환경(`uv run --no-project --with pytest`)에서 돌기
때문이다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import install_hooks

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / install_hooks.CONFIG
_INSTALLER = _ROOT / "scripts" / "install_hooks.py"

_TOP_KEY = re.compile(r"^(?P<key>[a-z][a-z0-9-]*):\s*$")
_RUN = re.compile(r"^\s*run:\s*(?P<cmd>.+?)\s*$")


def declared_hooks(text: str) -> list[str]:
    """최상위 키 = 훅 이름. 주석·빈 줄은 건너뛴다."""
    return [m.group("key") for ln in text.split("\n") if (m := _TOP_KEY.match(ln))]


def run_commands(text: str) -> list[str]:
    """모든 job의 `run:` 명령."""
    return [m.group("cmd") for ln in text.split("\n") if (m := _RUN.match(ln))]


def _config_text() -> str:
    return _CONFIG.read_text(encoding="utf-8")


# --- 훅 선언 -----------------------------------------------------------------


def test_config_exists():
    assert _CONFIG.is_file(), f"{install_hooks.CONFIG}이 없습니다"


def test_declares_expected_hooks():
    assert set(declared_hooks(_config_text())) == {"commit-msg", "pre-push"}


def test_hook_names_are_valid_git_hooks():
    """오타(`pre_push`)는 git이 부르지 않아 조용히 안 도는 훅이 된다."""
    valid = {"commit-msg", "pre-commit", "pre-push", "prepare-commit-msg", "post-merge"}
    for name in declared_hooks(_config_text()):
        assert name in valid, f"{name}은 git 훅 이름이 아닙니다"


def test_commit_msg_receives_message_file():
    """git이 넘기는 메시지 파일 경로를 job이 실제로 받아야 한다."""
    cmds = [c for c in run_commands(_config_text()) if "--subject-file" in c]
    assert cmds, "commit-msg job이 --subject-file을 쓰지 않습니다"
    assert all("{1}" in c for c in cmds), f"메시지 파일 자리표시자가 없습니다: {cmds}"


def test_scanner_reads_the_real_config():
    """스캐너 자기검증 — 선언 파일에서 훅과 명령을 실제로 뽑아낸다."""
    text = _config_text()
    assert declared_hooks(text), "훅 선언을 하나도 읽지 못했습니다"
    assert len(run_commands(text)) >= 4, "job 명령을 제대로 읽지 못했습니다"


# --- 전역 훅 불가침 -----------------------------------------------------------


def _installer_source() -> str:
    return _INSTALLER.read_text(encoding="utf-8")


def test_installer_writes_git_config_only_locally():
    """git 설정 쓰기는 반드시 --local. 전역을 쓰면 유저의 다른 repo가 바뀐다."""
    src = _installer_source()
    writes = re.findall(r'_git\(\s*"config"\s*,\s*"(?P<scope>--[a-z]+)"', src)
    assert writes, "설정 쓰기 호출을 찾지 못했습니다 — 패턴이 바뀌었는지 확인하세요"
    assert set(writes) == {"--local"}, f"로컬이 아닌 스코프로 씁니다: {sorted(set(writes))}"


def test_installer_never_touches_global_hooks_directory():
    """전역 훅 디렉터리는 읽지도 쓰지도 않는다 — 설정값은 안내 문구로만 쓴다.

    repo 자기 `.githooks/`를 훑는 것은 정당하므로 순회 자체를 막지 않는다. 막는 것은
    **전역 설정값에서 파생된 경로를 파일시스템으로 다루는 것**이다.
    """
    src = _installer_source()
    for line in src.split("\n"):
        if "global_path" not in line or line.strip().startswith("#"):
            continue
        allowed = (
            "global_path = _config" in line  # 설정값 조회
            or line.strip() == "if global_path:"  # 있는지만 확인
            or "print(" in line  # 안내 문구
        )
        assert allowed, (
            f"전역 설정값을 안내 외의 용도로 씁니다: {line.strip()!r} — "
            f"이 값으로 경로를 만들어 읽거나 쓰면 유저 전역 훅을 건드립니다."
        )


def test_installer_only_traverses_paths_under_repo():
    """디렉터리 순회는 repo 루트 아래에서만 한다."""
    tree = ast.parse(_installer_source())
    traversers = [
        fn for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) and "iterdir" in ast.dump(fn)
    ]
    assert traversers, "순회 함수를 찾지 못했습니다 — 패턴이 바뀌었는지 확인하세요"
    for fn in traversers:
        assert "_ROOT" in ast.dump(fn), (
            f"{fn.name}()가 repo 루트 밖을 순회할 수 있습니다 — 경로를 _ROOT에서 만드세요."
        )


def test_installer_sets_local_pointer_before_calling_lefthook():
    """순서가 안전의 핵심 — 로컬을 먼저 잡아야 --force가 전역에 쓰지 않는다."""
    src = _installer_source()
    set_local = src.index('"--local", "core.hooksPath"')
    verify = src.index("resolved != HOOKS_DIR")
    call_lefthook = src.index('"lefthook", "install", "--force"')
    assert set_local < verify < call_lefthook, (
        "로컬 포인터 설정 → 해소값 확인 → lefthook 호출 순서가 깨졌습니다. "
        "확인 전에 --force를 부르면 전역 훅 디렉터리에 쓸 수 있습니다."
    )


# --- 판정 위임 ---------------------------------------------------------------


def test_every_job_delegates_to_shared_judgment():
    """훅이 스스로 판정하지 않고 scripts/·CI와 같은 명령을 부른다."""
    allowed = (
        "scripts/branch_policy.py",
        "scripts/security_scan.py",
        "pytest scripts",
        "ruff",
    )
    for cmd in run_commands(_config_text()):
        assert any(a in cmd for a in allowed), (
            f"job이 공유 판정을 부르지 않습니다: {cmd!r} — 훅에 규칙을 복제하지 않습니다."
        )


def ci_command_surface() -> str:
    """CI가 실제로 실행하는 명령이 적힌 파일 전체의 본문.

    `ci.yml`은 순서만 정하고 명령은 `.github/actions/<카테고리>/action.yml`로 내려갔다
    (잡을 나누면 required check 컨텍스트 `core`가 갈리므로 composite action을 쓴다).
    "CI와 같은 명령인가"를 판정하려면 두 곳을 함께 봐야 한다 — `ci.yml`만 보면 스텝을
    카테고리로 옮긴 것만으로 이 게이트가 빨개진다.
    """
    files = [_ROOT / ".github" / "workflows" / "ci.yml"]
    files += sorted((_ROOT / ".github" / "actions").glob("*/action.yml"))
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_meta_test_command_matches_ci():
    """repo-메타 테스트는 CI `core` 잡과 같은 명령이어야 한다."""
    hook_cmd = next(c for c in run_commands(_config_text()) if "pytest scripts" in c)
    assert hook_cmd in ci_command_surface(), (
        f"훅의 명령이 CI에 없습니다: {hook_cmd!r} — 로컬과 CI가 다른 것을 돌리면 "
        f"로컬 통과가 CI 통과를 뜻하지 않습니다. CI 명령은 ci.yml과 "
        f".github/actions/*/action.yml에 있습니다."
    )


def test_security_gate_command_matches_ci():
    """보안 게이트도 훅과 CI가 같은 명령이어야 한다.

    이 게이트는 로컬-CI 동형이 설계의 일부다 — 무네트워크·무의존으로 만든 이유가
    push 전에 같은 판정을 받자는 것이라, 명령이 갈리면 그 이유가 사라진다.
    """
    hook_cmd = next(c for c in run_commands(_config_text()) if "security_scan.py" in c)
    assert hook_cmd in ci_command_surface(), (
        f"훅의 명령이 CI에 없습니다: {hook_cmd!r} — 보안 게이트는 "
        f".github/actions/security/action.yml에서 같은 명령으로 돌아야 합니다."
    )


def test_ci_command_surface_covers_composite_actions():
    """탐색면이 composite action까지 닿는지 — 이 게이트가 진짜 보는 범위의 자기 검증.

    `ci.yml`만 읽던 시절의 좁은 탐색면으로 회귀하면 여기서 잡힌다. `pytest scripts`가
    액션 파일에만 있고 `ci.yml`에는 없다는 사실 자체가 확장이 필요했던 이유다.
    """
    ci_only = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest scripts" not in ci_only, (
        "ci.yml이 다시 명령을 직접 들고 있습니다 — 그러면 이 자기 검증이 무의미해집니다. "
        "구조가 바뀐 것이라면 위 ci_command_surface()의 설명도 함께 고치세요."
    )
    assert "pytest scripts" in ci_command_surface(), (
        "탐색면이 .github/actions/*/action.yml에 닿지 않습니다 — 훅과 CI가 갈려도 "
        "test_meta_test_command_matches_ci가 통과해버립니다."
    )


def test_no_machine_specific_paths_in_config():
    """선언 파일에 설치자의 절대경로가 박히면 다른 머신에서 무효다."""
    hits = re.findall(r"(?:/Users/|/home/|/root/)[^\s\"']+", _config_text())
    assert not hits, f"머신 고유 경로가 있습니다: {hits}"


# --- 소비처 격리 -------------------------------------------------------------


def _array(text: str, key: str) -> str:
    """pyproject에서 `key = [...]` 배열 본문을 뽑는다.

    `tomllib`은 3.11+라 쓰지 않는다 — 이 repo는 py310을 타깃하고, 같은 이유로
    `test_version_sync`도 pyproject를 텍스트로 읽는다.
    """
    m = re.search(rf"^{re.escape(key)}\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def test_hook_manager_is_dev_only():
    """루트 셔틀의 런타임 의존에 훅 매니저가 새면 소비처까지 따라간다."""
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    runtime = _array(text, "dependencies")
    assert runtime, "루트 pyproject에서 dependencies를 읽지 못했습니다"
    assert "lefthook" not in runtime, (
        "lefthook이 [project.dependencies]에 있습니다 — 루트 pyproject는 "
        "`pip install <루트>`·pre-commit 소비 셔틀이라 개발 도구가 따라가면 안 됩니다."
    )

    dev = _array(text, "dev")
    assert "lefthook" in dev, "lefthook이 개발 그룹에 선언돼 있어야 의존성 설치에 따라옵니다"


# --- 생성물 취급 -------------------------------------------------------------


def test_generated_hooks_are_gitignored():
    """생성된 훅 셸에는 설치한 머신의 경로가 박히므로 커밋하지 않는다."""
    ignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert f"{install_hooks.HOOKS_DIR}/" in ignore, (
        f"{install_hooks.HOOKS_DIR}/가 .gitignore에 없습니다"
    )


def test_install_hooks_check_reports_state():
    """--check는 아무것도 바꾸지 않고 상태만 알린다(0 또는 1)."""
    assert install_hooks.main(["--check"]) in (0, 1)
