"""라이선스 검사 (T-P3-5).

검사 항목 — 실패 시 exit 1:
1. 루트 LICENSE 존재 + MIT 식별 문구
2. 벤더 라이선스 전문 사본 존재(spec Apache-2.0, oracle MIT)
3. THIRD_PARTY_NOTICES.md 존재 + 벤더 2항목(okf-spec, okf-validate-oracle) 포함

도구 선정 기록: 1순위였던 licensee는 Ruby 의존이라 무의존 CI 원칙에 맞지 않고,
검사 대상이 고정 2항목이라 계획이 허용한 간단 스크립트로 확정.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (파일, 반드시 포함해야 하는 문구 목록)
CHECKS = [
    (ROOT / "LICENSE", ["MIT License"]),
    (ROOT / "okf-core" / "vendor" / "spec" / "LICENSE-APACHE-2.0", ["Apache License"]),
    (ROOT / "okf-core" / "vendor" / "oracle" / "LICENSE-MIT", ["MIT License"]),
    (
        ROOT / "THIRD_PARTY_NOTICES.md",
        ["## okf-spec", "Apache-2.0", "## okf-validate-oracle", "MIT"],
    ),
]


def main() -> int:
    failures: list[str] = []
    for path, needles in CHECKS:
        rel = path.relative_to(ROOT)
        if not path.is_file():
            failures.append(f"{rel}: 파일 없음")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                failures.append(f"{rel}: 필수 문구 없음 — {needle!r}")
    if failures:
        print("라이선스 검사 실패:")
        for item in failures:
            print(f" - {item}")
        return 1
    print(f"라이선스 검사 통과: {len(CHECKS)}개 파일 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
import os
x = 1
