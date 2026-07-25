"""보안 게이트 — 올라가면 안 되는 것이 추적되고 있는지 판정한다.

이 repo는 **public**이다. 한 번 push된 것은 지워도 포크·캐시·이벤트 API에 남으므로,
방어선은 "새어 나간 뒤 지우기"가 아니라 "추적되기 전에 막기"에 있어야 한다.

검사 항목 — 위반 시 exit 1:

1. **`.gitignore` 대상인데 추적 중인 파일.** `.gitignore`는 *추적되지 않은* 파일에만
   듣는다. ``git add -f`` 한 번이면 뚫리고, 그 뒤로는 같은 규칙이 그 파일을 영영
   보호하지 못한다 — 무시 규칙과 실제 추적 상태가 어긋난 순간을 여기서 잡는다.
2. **크리덴셜 파일.** 개인키·인증서·환경파일처럼 이름·확장자만으로 판별되는 것.
   `.gitignore`에 없으면 1번이 못 잡으므로 규칙을 따로 둔다.
3. **워크플로 잡의 `permissions:` 선언.** 없으면 `GITHUB_TOKEN` 권한이 repo·org
   기본값에 위임된다. 지금 기본값이 read라도 그것은 워크플로가 보장한 게 아니라 설정이
   그럴 뿐이고, 설정은 조용히 바뀐다. 최상위든 잡 레벨이든 **적힌 곳이 있으면** 통과다
   — 잡마다 필요한 권한이 달라서, 한 잡이 넓은 권한을 필요로 한다고 최상위로 올리면
   그 권한이 나머지 잡까지 퍼진다.
4. **개발 머신 절대경로.** 로컬 vault·홈 디렉터리 경로가 커밋에 섞이면 그 자체로
   사용자명이 공개되고, 소비처에서는 재현되지 않는 경로가 된다.

**시크릿 "값" 탐지는 여기서 하지 않는다** — gitleaks가 CI에서 본다
(`.github/actions/security/action.yml`). 역할을 나눈 이유는 둘의 성질이 다르기
때문이다. 엔트로피·룰 기반 값 탐지는 계속 갱신되는 룰셋이 곧 품질이라 전용 도구가
낫고, 반대로 "이 repo에서 어떤 **파일**이 올라오면 안 되는지"는 이 repo만 아는
규칙이라 도구가 대신 알 수 없다. 그래서 이 스크립트는 무네트워크·무의존으로 남고,
로컬 pre-push에서 CI와 **같은 코드**가 돈다.

    python3 scripts/security_scan.py

종료코드: 0 통과 / 1 위반 / 2 실행 오류.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# --- 판정 상수 ---------------------------------------------------------------

# 확장자만으로 크리덴셜인 파일.
CREDENTIAL_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk"})

# 이름 전체가 정확히 일치할 때만 크리덴셜인 파일. 확장자 규칙으로는 못 잡고,
# `credentials.py` 같은 멀쩡한 소스를 오탐하지 않으려면 정확 일치여야 한다.
CREDENTIAL_NAMES = frozenset(
    {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc", "credentials"}
)

# `.env`·`.env.local` 등은 막고 예제만 통과시킨다 — 예제는 값이 아니라 키 목록이라
# 오히려 커밋되어야 소비처가 무엇을 채울지 안다.
ENV_ALLOWED_SUFFIXES = frozenset({".example", ".sample", ".template", ".dist"})

# 개발 머신 홈 경로. macOS 표기(`/Users/<이름>`)만 본다 — 이 repo의 테스트 픽스처가
# 리눅스 표기(`/home/u`, `/home/user`)를 **가짜 경로**로 쓰고 있어서 그쪽을 규칙에
# 넣으면 게이트가 곧바로 오탐한다. 개발이 리눅스로 넘어가면 그때 추가한다.
_MACHINE_PATH = re.compile(r"/Users/(?P<user>[A-Za-z0-9._-]+)")

# 명백한 자리표시자는 실제 경로가 아니다 — 문서가 경로 예시를 들 수 있어야 한다.
PLACEHOLDER_USERS = frozenset({"you", "me", "user", "username", "someone", "your-name"})

# 업스트림 바이트 그대로여야 하는 곳(CLAUDE.md 불변식). 우리가 고칠 수 없는 파일을
# 이 게이트로 막으면 벤더 갱신 자체가 막힌다 — 그쪽은 vendor_sync_check가 본다.
MACHINE_PATH_EXEMPT_PREFIXES = ("okf-core/vendor/",)

_WORKFLOWS = Path(".github") / "workflows"
_PERMISSIONS = re.compile(r"^permissions:", re.M)  # 최상위 — 들여쓰기가 없다
_JOB_PERMISSIONS = re.compile(r"^[ \t]+permissions:")
_TOP_KEY = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z0-9_.\-]+):")


# --- 검사 --------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def tracked_files(root: Path = _ROOT) -> list[str]:
    return [ln for ln in _git(root, "ls-files").stdout.split("\n") if ln]


def tracked_but_ignored(root: Path = _ROOT) -> list[str]:
    """무시 대상인데 추적 중인 파일.

    `--exclude-standard`가 아니라 `--exclude-from=.gitignore`를 쓴다. 전자는 전역
    `core.excludesFile`과 `.git/info/exclude`까지 읽어서 **판정이 그 머신의 개인
    설정에 따라 달라진다** — 로컬에서만 붉어지거나 CI에서만 붉어지는 게이트가 된다.
    후자는 repo에 커밋된 `.gitignore` 하나만 보므로 어디서 돌려도 같은 답이 나온다.
    """
    proc = _git(root, "ls-files", "--cached", "--ignored", "--exclude-from=.gitignore")
    return [ln for ln in proc.stdout.split("\n") if ln]


def is_credential(rel: str) -> bool:
    name = Path(rel).name
    if name.lower() in CREDENTIAL_NAMES or Path(rel).suffix.lower() in CREDENTIAL_SUFFIXES:
        return True
    if name == ".env" or name.startswith(".env."):
        return Path(name).suffix.lower() not in ENV_ALLOWED_SUFFIXES
    return False


def credential_files(paths: list[str]) -> list[str]:
    return sorted(p for p in paths if is_credential(p))


def _job_blocks(text: str) -> list[tuple[str, list[str]]]:
    """`jobs:` 아래 (잡 이름, 그 잡의 본문 줄) 목록.

    YAML 파서를 쓰지 않는다 — 이 게이트는 무의존이어야 로컬 pre-push에서 CI와 같은
    코드로 돌 수 있다. 잡 이름 층은 `jobs:` 블록에서 **가장 얕은 들여쓰기**이고,
    그 스캐너 자체는 테스트가 검증한다(`test_hooks_contract`의 잡 스캐너와 같은 방식).
    """
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*$", ln)), None)
    if start is None:
        return []

    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.strip() and not ln.startswith((" ", "\t")):  # 들여쓰기가 풀리면 블록의 끝
            break
        body.append(ln)

    heads = [
        (len(m.group("indent")), m.group("key"), i)
        for i, ln in enumerate(body)
        if ln.strip() and not ln.lstrip().startswith("#") and (m := _TOP_KEY.match(ln))
    ]
    if not heads:
        return []

    depth = min(indent for indent, _, _ in heads)
    tops = [(key, i) for indent, key, i in heads if indent == depth]
    return [
        (key, body[i + 1 : tops[n + 1][1] if n + 1 < len(tops) else len(body)])
        for n, (key, i) in enumerate(tops)
    ]


def jobs_without_permissions(text: str) -> list[str]:
    """`permissions:`로 덮이지 않은 잡 이름.

    최상위 선언이 있으면 모든 잡이 덮이므로 통과다. 없으면 **각 잡이 자기 선언을**
    가져야 한다. 최상위를 강요하지 않는 이유는 잡마다 필요한 권한이 다르기 때문이다 —
    한 잡이 `issues: write`를 필요로 한다고 최상위로 올리면 그 권한이 나머지 잡까지
    퍼진다. 그래서 "최소권한이 어딘가에 적혀 있는가"를 잡 단위로 본다. 선언 없는 잡이
    새로 추가되면 그 잡만 걸린다.
    """
    if _PERMISSIONS.search(text):
        return []
    return [
        name
        for name, block in _job_blocks(text)
        if not any(_JOB_PERMISSIONS.match(ln) for ln in block)
    ]


def workflows_without_permissions(root: Path = _ROOT) -> list[str]:
    """`<파일>: <잡>` 형식의, 권한 선언이 없는 잡 목록."""
    out = []
    for path in sorted((root / _WORKFLOWS).glob("*.yml")):
        rel = str(path.relative_to(root))
        out.extend(
            f"{rel}: {job}" for job in jobs_without_permissions(path.read_text(encoding="utf-8"))
        )
    return out


def machine_paths_in_text(text: str) -> list[tuple[int, str]]:
    """`(줄번호, 발견한 경로)` 목록. 자리표시자 사용자명은 세지 않는다."""
    return [
        (lineno, m.group(0))
        for lineno, line in enumerate(text.split("\n"), 1)
        for m in _MACHINE_PATH.finditer(line)
        if m.group("user") not in PLACEHOLDER_USERS
    ]


def machine_paths(paths: list[str], root: Path = _ROOT) -> list[str]:
    """`<파일>:<줄>: <경로>` 형식의 개발 머신 경로 발견 목록."""
    out = []
    for rel in paths:
        if rel.startswith(MACHINE_PATH_EXEMPT_PREFIXES):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # 바이너리·심링크는 경로 문자열 검사 대상이 아니다
        out.extend(f"{rel}:{lineno}: {found}" for lineno, found in machine_paths_in_text(text))
    return out


# --- 진입점 ------------------------------------------------------------------


def collect(root: Path = _ROOT) -> list[tuple[str, list[str]]]:
    """(위반 종류, 위반 목록) 쌍. 통과면 목록이 빈다."""
    paths = tracked_files(root)
    return [
        (
            "무시 대상인데 추적 중 (.gitignore는 이미 추적된 파일을 보호하지 못한다 — "
            "`git rm --cached <경로>`로 추적에서 빼세요)",
            tracked_but_ignored(root),
        ),
        ("크리덴셜 파일 (키·인증서·환경파일은 커밋하지 않는다)", credential_files(paths)),
        (
            "권한 선언이 없는 워크플로 잡 (최소권한을 최상위나 그 잡에 명시하세요 — "
            "예: `permissions:` 아래 `contents: read`)",
            workflows_without_permissions(root),
        ),
        (
            "개발 머신 절대경로 (사용자명이 공개되고 소비처에서 재현되지 않는다)",
            machine_paths(paths, root),
        ),
    ]


def main() -> int:
    try:
        results = collect()
    except OSError as exc:  # git 부재·파일 접근 실패는 판정이 아니라 실행 오류다
        print(f"보안 게이트 실행 오류: {exc}", file=sys.stderr)
        return 2

    violations = [(label, items) for label, items in results if items]
    if violations:
        print("보안 게이트 실패:")
        for label, items in violations:
            print(f" [{label}]")
            for item in items:
                print(f"  - {item}")
        return 1

    print(f"보안 게이트 통과: 추적 파일 {len(tracked_files())}개, 검사 {len(results)}종")
    return 0


if __name__ == "__main__":
    sys.exit(main())
