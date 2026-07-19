"""study 런타임 스캐폴드 (S1, #73).

소비 repo에 study 런타임을 **멱등·비파괴**로 준비한다.

- ``.okf-study/.gitignore`` — 내용물 전부 무시(자신만 추적): ``*`` + ``!.gitignore``.
  런타임 상태(inbox.md·ledger·trust)는 커밋되지 않고, 무시 규칙 파일만 커밋된다.
- ``.okf-wiki.json`` — 없으면 study 블록 포함 템플릿을 생성하고, 있으면 study가
  없을 때만 추가한다(기존 키·값 보존). study가 이미 있으면 파일을 건드리지 않는다.

``/okf-init`` 커맨드가 ``okf init <번들>`` 뒤에 호출한다. 여러 번 실행해도 중복
생성·덮어쓰기가 없다.
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
    args = ap.parse_args(argv)
    try:
        lines = scaffold(args.project)
    except ValueError as exc:
        print(f"오류: {exc}")
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
