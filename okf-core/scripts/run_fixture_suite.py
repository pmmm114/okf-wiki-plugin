"""픽스처 스위트 (T-P3-2).

픽스처 번들마다 산출물을 만들어 `tests/expected/<이름>.json` 스냅샷과 비교한다.
불일치·기대값 부재는 실패(exit 1). `--update`는 실측으로 스냅샷을 다시 쓴다 —
생성분은 사람이 검수 후 커밋한다(스냅샷이 곧 회귀 계약).

산출물은 두 종류다.

- `validate` — `okf validate --format json`과 동일한 Finding 목록. **판정**의 계약이다.
- `census` — `okf census --json`의 payload. **관측**의 계약이라 결이 다르다: 판정이
  아니라 계수·구조·요약 원문을 잠그므로 픽스처의 본문·frontmatter를 고치면 깨진다.
  그게 의도다 — 관측이 조용히 달라지면 그 위의 배치 판정 근거가 달라진다.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "okf-core" / "src"))

from okf_core.census import build_census  # noqa: E402
from okf_core.validate import validate_bundle  # noqa: E402

FIXTURES = ROOT / "okf-core" / "tests" / "fixtures"
EXPECTED = ROOT / "okf-core" / "tests" / "expected"


def _validate_case(bundle: str, strict: bool = False) -> list[dict]:
    return [f.to_dict() for f in validate_bundle(FIXTURES / bundle, strict=strict)]


def _census_case(bundle: str) -> dict:
    return build_census(FIXTURES / bundle)


# (스냅샷 이름, 산출 함수, 인자) — 기존 4건의 이름·내용은 불변이어야 한다.
CASES = [
    ("appendix-a", _validate_case, {"bundle": "appendix-a"}),
    ("violations", _validate_case, {"bundle": "violations"}),
    ("strict-warns", _validate_case, {"bundle": "strict-warns"}),
    ("strict-warns.strict", _validate_case, {"bundle": "strict-warns", "strict": True}),
    ("census.taxonomy", _census_case, {"bundle": "taxonomy"}),
    ("census.appendix-a", _census_case, {"bundle": "appendix-a"}),
]


_BRIEF_MAX = 600


def _brief(payload) -> str:
    """불일치 보고용 축약 — 스냅샷이 커도 로그를 덮지 않게.

    전체 비교는 `--update` 후 `git diff`가 훨씬 낫다(줄 단위 diff + 검수 흔적).
    """
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= _BRIEF_MAX:
        return text
    return f"{text[:_BRIEF_MAX]}… (총 {len(text)}자 — 전체 비교는 `--update` 후 git diff)"


def main(argv: list[str]) -> int:
    update = "--update" in argv
    failures: list[str] = []
    for name, produce, kwargs in CASES:
        got = produce(**kwargs)
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
                f"{name}: 스냅샷 불일치\n   기대: {_brief(want)}\n   실측: {_brief(got)}"
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
