"""study trust 게이트 (S4, #76).

핸들러 임의 실행의 보안 게이트. 커밋되는 ``.okf-wiki.json``이 메모리 저장만으로
코드를 실행시키는 위협을, **로컬(비커밋) 승인 + 스크립트 내용 해시**로 차단한다.

- 승인은 ``.okf-study/trust``(S1의 자체 gitignore로 커밋 제외)에 저장된다 →
  프레시 클론은 항상 untrusted에서 시작한다.
- 승인 대상 해시 = 정렬된 핸들러별 {name + repo 내 정규화 경로 + ``sha256(스크립트
  바이트)``} + ``capture``. 핸들러 command·셋·capture·**스크립트 내용**이 무엇이든
  바뀌면 해시가 달라져 재승인이 강제된다(같은 경로 내용 교체 포함).
- 경로 정규화(심링크·``..`` 탈출 거부)와 파일 부재는 fail-closed(untrusted)로 흡수.

``make_trust_check``는 S3 디스패처의 ``trust_check`` 훅에 넘길 판정을 만든다.
미승인 시 디스패처는 핸들러를 보류한다. **가시적 저하**(개념은 로컬 번들에
승격·검증하되 핸들러 실행만 보류하고 안내)는 이 판정을 소비하는 S5 ``/study``
플로우가 수행한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from study_dispatch import CommandError, resolve_command

STUDY_DIR = ".okf-study"
TRUST_NAME = "trust"
CONFIG_NAME = ".okf-wiki.json"


def _trust_path(project: str | Path) -> Path:
    return Path(project) / STUDY_DIR / TRUST_NAME


def compute_hash(project: str | Path, handlers: list[dict], capture: str) -> str:
    """핸들러 셋 + capture의 내용 해시. 경로 밖은 CommandError, 파일 부재는 OSError."""
    root = Path(project).resolve()
    entries = []
    for handler in sorted(handlers, key=lambda h: str(h.get("name", ""))):
        path = resolve_command(project, handler.get("command", ""))  # 경로 검사
        entries.append(
            {
                "name": str(handler.get("name", "")),
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    material = json.dumps(
        {"capture": capture, "handlers": entries}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def is_trusted(project: str | Path, handlers: list[dict], capture: str) -> bool:
    """현재 핸들러 셋이 로컬 승인 기록과 일치하는지 여부(fail-closed)."""
    path = _trust_path(project)
    if not path.is_file():
        return False
    try:
        current = compute_hash(project, handlers, capture)
    except (CommandError, OSError):
        return False
    return path.read_text(encoding="utf-8").strip() == current


def approve(project: str | Path, handlers: list[dict], capture: str) -> str:
    """현재 핸들러 셋을 로컬 승인 기록하고 해시를 반환한다."""
    digest = compute_hash(project, handlers, capture)
    path = _trust_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest + "\n", encoding="utf-8")
    return digest


def make_trust_check(
    project: str | Path, handlers: list[dict], capture: str
) -> Callable[[str, Path], bool]:
    """디스패처용 ``trust_check(name, path)``를 만든다. 셋 전체 승인 시에만 True."""
    trusted = is_trusted(project, handlers, capture)
    return lambda _name, _path: trusted


def _load_study(project: str | Path) -> tuple[str, list[dict]]:
    config = Path(project) / CONFIG_NAME
    data = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    study = data.get("study") or {}
    return study.get("capture", "off"), study.get("handlers") or []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="study_trust", description="study 핸들러 로컬 trust 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "approve"):
        parser = sub.add_parser(name)
        parser.add_argument("project", nargs="?", default=".", help="소비 repo 루트")
    args = ap.parse_args(argv)

    capture, handlers = _load_study(args.project)
    if not handlers:
        print("핸들러 없음 — trust 불필요")
        return 0

    if args.cmd == "status":
        print("trusted" if is_trusted(args.project, handlers, capture) else "untrusted")
        for handler in handlers:  # 승인 전 확인용으로 해석된 command를 보인다
            name = handler.get("name", "?")
            try:
                print(f"  {name}: {resolve_command(args.project, handler.get('command', ''))}")
            except CommandError as exc:
                print(f"  {name}: 거부 — {exc}")
        return 0

    digest = approve(args.project, handlers, capture)
    print(f"승인 기록(capture={capture}): {digest[:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
