"""로컬 git 훅 배선 — 이 repo에서만 lefthook 훅을 쓰도록 잡는다.

훅 정의는 `lefthook.yml`이고, lefthook은 개발 의존(`[dependency-groups] dev`)이라
의존성을 설치하면 자연스럽게 따라온다. 이 스크립트는 git이 그 훅을 부르도록 포인터를
거는 일만 한다.

    python3 scripts/install_hooks.py            # 배선(여러 번 실행해도 안전)
    python3 scripts/install_hooks.py --check    # 상태만 확인

**전역 설정을 건드리지 않는다.** 거는 것은 이 repo의 로컬 `core.hooksPath`뿐이고
유저 `~/.gitconfig`와 전역 훅 파일은 그대로 둔다. 다만 git은 이 값을 병합하지 않고
**교체**하므로, 전역 훅을 쓰고 있었다면 이 repo 안에서는 그것이 돌지 않는다 — git의
동작이라 우회할 수 없으므로 무엇이 비켜가는지 알려 준다.

순서가 안전의 핵심이다. ``lefthook install --force``는 **그 시점에 해소되는**
``core.hooksPath``에 훅을 쓴다. 로컬을 먼저 잡지 않으면 전역 훅 디렉터리에 써서 유저의
다른 repo까지 바꾼다. 그래서 로컬을 먼저 걸고, 해소값이 기대한 곳인지 확인한 뒤에만
lefthook을 부른다. 전역 훅 디렉터리는 확인 목적으로도 열지 않는다 — 훅이 repo 안
``.githooks/``에 떨어진 것이 곧 다른 곳에 쓰지 않았다는 증거다.

종료코드: 0 정상 / 1 검증 실패 / 2 실행 오류.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = ".githooks"
CONFIG = "lefthook.yml"

_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(_ROOT), *args], capture_output=True, text=True, check=False
    )


def _config(scope: str | None, key: str) -> str:
    cmd = ["config"] + ([scope] if scope else []) + ["--get", key]
    return _git(*cmd).stdout.strip()


def installed_hooks() -> list[str]:
    d = _ROOT / HOOKS_DIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and not p.name.startswith("."))


def install() -> int:
    if not (_ROOT / CONFIG).is_file():
        print(f"{CONFIG}이 없습니다 — 훅 정의가 있어야 배선할 수 있습니다", file=sys.stderr)
        return 2

    # 전역 훅 **디렉터리**는 읽지도 쓰지도 않는다. 무엇이 비켜가는지 알리기 위해
    # 설정값만 조회한다.
    global_path = _config("--global", "core.hooksPath")

    # 1) 로컬 포인터를 먼저 건다. 이 순서가 전역 오염을 막는 유일한 장치다.
    if _git("config", "--local", "core.hooksPath", HOOKS_DIR).returncode != 0:
        print("core.hooksPath를 설정하지 못했습니다", file=sys.stderr)
        return 2

    # 2) 해소값이 기대한 곳인지 확인하기 전에는 lefthook을 부르지 않는다.
    resolved = _config(None, "core.hooksPath")
    if resolved != HOOKS_DIR:
        print(
            f"core.hooksPath가 {resolved!r}로 해소됩니다 — {HOOKS_DIR!r}가 아니면 "
            f"lefthook이 엉뚱한 곳에 훅을 씁니다. 중단합니다.",
            file=sys.stderr,
        )
        return 1

    # 3) 훅 생성. lefthook은 hooksPath가 잡혀 있으면 기본적으로 거부하므로 --force가
    #    필요하다. 위에서 목적지를 확인했기 때문에 안전하다.
    res = subprocess.run(
        ["uv", "run", "--project", str(_ROOT), "lefthook", "install", "--force"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(f"lefthook install에 실패했습니다:\n{res.stderr.strip()}", file=sys.stderr)
        return 2

    # 4) 훅이 repo 안에 떨어졌는지 확인한다. 여기 있다는 것이 곧 다른 곳에 쓰지
    #    않았다는 증거다(lefthook은 해소된 hooksPath 한 곳에만 쓴다).
    hooks = installed_hooks()
    if not hooks:
        print(f"{HOOKS_DIR}/에 훅이 생성되지 않았습니다", file=sys.stderr)
        return 1

    print(f"로컬 훅을 걸었습니다 — core.hooksPath = {HOOKS_DIR} (이 repo에만 적용)")
    print("  훅: " + ", ".join(hooks))
    print(f"  정의: {CONFIG} (판정은 scripts/에 위임 — CI와 같은 코드)")
    if global_path:
        print(f"  전역 core.hooksPath({global_path})는 읽지도 쓰지도 않았습니다.")
        print("    git은 이 값을 병합하지 않고 교체하므로 이 repo 안에서만 비켜갑니다.")
    print("  건너뛰려면: git commit --no-verify / git push --no-verify")
    return 0


def check() -> int:
    """배선 상태만 확인하고 아무것도 바꾸지 않는다."""
    resolved = _config(None, "core.hooksPath")
    if resolved != HOOKS_DIR:
        print(
            f"로컬 훅이 걸려 있지 않습니다 — core.hooksPath = {resolved or '(미설정)'}",
            file=sys.stderr,
        )
        print("  `python3 scripts/install_hooks.py`로 겁니다.", file=sys.stderr)
        return 1
    hooks = installed_hooks()
    if not hooks:
        print(f"{HOOKS_DIR}/에 훅이 없습니다 — 다시 배선하세요", file=sys.stderr)
        return 1
    print(f"로컬 훅 정상 — core.hooksPath = {resolved}, 훅: {', '.join(hooks)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="install_hooks", description="이 repo의 로컬 git 훅 배선(전역 설정 무변경)"
    )
    ap.add_argument("--check", action="store_true", help="상태만 확인하고 바꾸지 않음")
    args = ap.parse_args(argv)
    return check() if args.check else install()


if __name__ == "__main__":
    sys.exit(main())
