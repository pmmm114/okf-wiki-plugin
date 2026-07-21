"""study 런타임 스캐폴드 (S1, #73 · 가드 #104).

소비 repo에 study 런타임을 **멱등·비파괴**로 준비한다.

- ``.okf-study/.gitignore`` — 내용물 전부 무시(자신만 추적): ``*`` + ``!.gitignore``.
  런타임 상태(inbox.md·ledger·trust)는 커밋되지 않고, 무시 규칙 파일만 커밋된다.
- ``.okf-wiki.json`` — 없으면 study 블록 포함 템플릿을 생성하고, 있으면 study가
  없을 때만 추가한다(기존 키·값 보존). study가 이미 있으면 파일을 건드리지 않는다.

``/okf-init`` 커맨드가 **가장 먼저** 호출한다(가드 게이트 — 거부 시 로컬 산출물 0).
여러 번 실행해도 중복 생성·덮어쓰기가 없다.

**CLI 가드(#104, fail-closed — #91 #20 원칙: 판정은 스크립트)**: cwd가 git repo가
아니면 스캐폴드를 거부한다(exit 3) — 핸들러 git-추적 요건상 디스패치가 영구
불가한 반쪽 파이프라인이 되고, study 블록이 생기는 순간 해소 규칙 2("명시가
이긴다")로 그 자리의 홈 캡처까지 꺼지기 때문이다. 우회는 명시 ``--force``뿐.
라이브러리 함수(``scaffold``)는 순수하게 유지한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

STUDY_DIR = ".okf-study"
GITIGNORE_BODY = "*\n!.gitignore\n"
CONFIG_NAME = ".okf-wiki.json"
DEFAULT_STUDY = {"capture": "off", "handlers": []}
DEFAULT_CONFIG = {"bundlePath": ".okf", "study": DEFAULT_STUDY}

GUARD_NOT_GIT = (
    "거부: git repo가 아님 — 이 위치의 로컬 study 파이프라인은 핸들러 git-추적 요건상 "
    "디스패치가 불가하고, 생성되는 study 블록이 이 자리의 홈 캡처를 꺼버린다(해소 규칙 2). "
    "위치 무관 적립은 /okf-init --home <홈경로>(포인터만 기록)를 쓰라. "
    "정말 로컬 스캐폴드가 필요하면 --force로 재실행."
)


def guard(project: str | Path) -> str | None:
    """스캐폴드 거부 사유를 반환한다(없으면 None). ``.git``은 파일(worktree)도 인정."""
    if not (Path(project) / ".git").exists():
        return GUARD_NOT_GIT
    return None


def home_notice(project: str | Path) -> str | None:
    """유효 홈 포인터와 공존할 때의 우선순위 고지(진행은 허용 — 정보 출력만)."""
    try:
        import okf_home
    except ImportError:  # pragma: no cover - 단독 배포 등 비정상 배치 관용
        return None
    home, _reason = okf_home.home_state()
    if home is None:
        return None
    try:
        if Path(home).resolve() == Path(project).resolve():
            return None
    except OSError:
        return None
    return (
        f"주의: 유효한 홈 포인터({home})가 있다 — 이 repo에 study 블록이 생기면 "
        "여기서는 로컬 파이프라인이 홈 캡처보다 우선한다(해소 규칙 2)."
    )


def ensure_study_gitignore(project: Path) -> str:
    """``.okf-study/.gitignore``를 보장하고 수행 상태 문자열을 반환한다."""
    study_dir = project / STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)
    gitignore = study_dir / ".gitignore"
    if gitignore.exists():
        return f"{STUDY_DIR}/.gitignore: 유지"
    gitignore.write_text(GITIGNORE_BODY, encoding="utf-8")
    return f"{STUDY_DIR}/.gitignore: 생성"


def ensure_study_config(project: Path) -> str:
    """``.okf-wiki.json``에 study 블록을 보장하고 수행 상태 문자열을 반환한다."""
    config = project / CONFIG_NAME
    if not config.exists():
        _write_json(config, DEFAULT_CONFIG)
        return f"{CONFIG_NAME}: 생성(study 포함)"

    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONFIG_NAME} 파싱 실패 — {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_NAME} 최상위가 객체가 아님")
    if "study" in data:
        return f"{CONFIG_NAME}: study 유지"

    data["study"] = dict(DEFAULT_STUDY)
    _write_json(config, data)
    return f"{CONFIG_NAME}: study 추가(기존 키 보존)"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def scaffold(project: str | Path) -> list[str]:
    """프로젝트 루트에 study 런타임을 스캐폴드하고 수행 상태 목록을 반환한다."""
    project = Path(project)
    return [ensure_study_gitignore(project), ensure_study_config(project)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="study_scaffold", description="study 런타임 스캐폴드(멱등)")
    ap.add_argument("project", nargs="?", default=".", help="소비 repo 루트(기본: 현재 디렉터리)")
    ap.add_argument(
        "--force", action="store_true", help="가드(비-git 거부, #104)를 명시적으로 우회"
    )
    args = ap.parse_args(argv)
    if not args.force:
        reason = guard(args.project)
        if reason:
            print(reason)
            return 3
    notice = home_notice(args.project)
    try:
        lines = scaffold(args.project)
    except ValueError as exc:
        print(f"오류: {exc}")
        return 2
    for line in lines:
        print(line)
    if notice:
        print(notice)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
