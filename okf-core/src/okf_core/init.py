"""§9 컨포먼트 최소 번들 스캐폴드 (T-B1).

예약 파일 2개를 생성한다 — 루트 index.md는 ``okf_version`` frontmatter(§11)와
빈 목차, log.md는 Initialization 엔트리(§7). 산출물은 생성 직후
``okf validate --strict``를 통과해야 한다(자기 출력 컨포먼트). 안전 규칙:
대상이 이미 내용을 가진 디렉터리면 아무것도 만들지 않고 거부한다 — 기존
번들의 index 재생성은 ``okf index --write``의 몫이다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from okf_core import logmd
from okf_core.validate import load_rules


def init_bundle(target: str | Path) -> list[str]:
    """대상 디렉터리에 최소 번들을 만들고 생성 파일 이름 목록을 반환한다."""
    target = Path(target)
    rules, _ = load_rules()
    index_name = rules["index_file"]
    log_name = rules["log_file"]
    if target.exists():
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        if any(target.iterdir()):
            raise FileExistsError(str(target))
    target.mkdir(parents=True, exist_ok=True)
    okf_version = rules["okf_version"]
    (target / index_name).write_text(
        f'---\nokf_version: "{okf_version}"\n---\n\n# Contents\n', encoding="utf-8"
    )
    logmd.append_entry(target, "Established bundle skeleton.", kind="Initialization")
    return [index_name, log_name]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf init", description="§9 컨포먼트 최소 번들 스캐폴드")
    ap.add_argument("target", help="번들을 만들 디렉터리(없으면 생성, 비어 있어야 함)")
    args = ap.parse_args(argv)
    try:
        created = init_bundle(args.target)
    except FileExistsError as exc:
        print(f"오류: 비어 있지 않은 디렉터리 — 덮어쓰지 않음: {exc}")
        print("기존 번들의 index 재생성은 `okf index <path> --write`를 사용")
        return 2
    except NotADirectoryError as exc:
        print(f"오류: 디렉터리가 아님: {exc}")
        return 2
    for name in created:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
