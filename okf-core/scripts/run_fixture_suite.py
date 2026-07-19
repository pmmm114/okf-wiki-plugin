"""픽스처 스위트 (T-P3-2).

픽스처 번들마다 `okf validate --format json`과 동일한 출력(Finding 목록)을 만들어
`tests/expected/<이름>.json` 스냅샷과 비교한다. 불일치·기대값 부재는 실패(exit 1).
`--update`는 실측으로 스냅샷을 다시 쓴다 — 생성분은 사람이 검수 후 커밋한다.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "okf-core" / "src"))

from okf_core.validate import validate_bundle  # noqa: E402

FIXTURES = ROOT / "okf-core" / "tests" / "fixtures"
EXPECTED = ROOT / "okf-core" / "tests" / "expected"
# (스냅샷 이름, 번들, strict)
CASES = [
    ("appendix-a", "appendix-a", False),
    ("violations", "violations", False),
    ("strict-warns", "strict-warns", False),
    ("strict-warns.strict", "strict-warns", True),
]


def _actual(bundle: str, strict: bool) -> list[dict]:
    return [f.to_dict() for f in validate_bundle(FIXTURES / bundle, strict=strict)]


def main(argv: list[str]) -> int:
    update = "--update" in argv
    failures: list[str] = []
    for name, bundle, strict in CASES:
        got = _actual(bundle, strict)
        path = EXPECTED / f"{name}.json"
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(got, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"갱신: {path.relative_to(ROOT)}")
            continue
        if not path.is_file():
            failures.append(f"{name}: 기대값 없음 — --update로 생성 후 검수·커밋")
            continue
        want = json.loads(path.read_text(encoding="utf-8"))
        if got != want:
            failures.append(
                f"{name}: 스냅샷 불일치\n"
                f"   기대: {json.dumps(want, ensure_ascii=False)}\n"
                f"   실측: {json.dumps(got, ensure_ascii=False)}"
            )
    if update:
        return 0
    if failures:
        print("픽스처 스위트 실패:")
        for item in failures:
            print(f" - {item}")
        return 1
    print(f"픽스처 스위트 통과: {len(CASES)}케이스 일치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
