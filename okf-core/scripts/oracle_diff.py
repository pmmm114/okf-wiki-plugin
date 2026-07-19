"""차동 오라클 (T-P3-3).

`okf validate`와 벤더 오라클(okf_validate.py --json)을 같은 번들에 실행해
**파일별 §9 위반 집합**을 비교하고 `oracle-diff-report.md`를 만든다.
불일치는 빌드 실패가 아니다 — 항상 exit 0(리포트 전용, CI 아티팩트 업로드).

어댑터 매핑(T-P1-3 관찰 + .okf Conformance Decisions):
- 오라클 errors의 `§9.1`/`§9.2` 인용 → 해당 §9 위반
- 오라클은 예약 파일 구조 위반을 §6/§7/§11 **warning**으로만 보고하고 §9.3
  error를 내지 않는다 → 그 warning들을 §9.3으로 매핑해 비교
- §4.1 권장 필드 warning 등 나머지는 §9 비위반 — 비교에서 제외

strict 수준: §9 위반 집합은 양쪽 모두 strict와 무관(우리 --strict는 POL.* 승격,
오라클 --strict는 종료코드만 변경)이므로 기본 모드 실행이 동일 수준 비교다.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "okf-core" / "src"))

from okf_core.validate import validate_bundle  # noqa: E402

ORACLE = ROOT / "okf-core" / "vendor" / "oracle" / "okf_validate.py"
REPORT = ROOT / "oracle-diff-report.md"
BUNDLES = [
    ("appendix-a", ROOT / "okf-core" / "tests" / "fixtures" / "appendix-a"),
    (".okf", ROOT / ".okf"),
]
_S9 = re.compile(r"§9\.([123])")
_RESERVED_STRUCT = re.compile(r"§(?:6|7|11)\b")


def _ours(bundle: Path) -> dict[str, set[str]]:
    per: dict[str, set[str]] = {}
    for f in validate_bundle(bundle):
        if f.level == "error" and f.rule.startswith("OKF9."):
            per.setdefault(f.file, set()).add("§9." + f.rule.split(".", 1)[1])
    return per


def _oracle(bundle: Path) -> dict[str, set[str]]:
    proc = subprocess.run(
        [sys.executable, str(ORACLE), str(bundle), "--json"],
        capture_output=True,
        text=True,
    )
    # --json도 비컨포먼트면 exit 1을 낸다(2026-07-19 실측 — T-P1-3 기록 정정).
    # JSON은 0/1 모두 stdout에 나오므로 종료코드는 판정에 쓰지 않는다.
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        raise RuntimeError(f"오라클 실행 오류 (exit {proc.returncode}): {proc.stderr.strip()}")
    payload = json.loads(proc.stdout)
    per: dict[str, set[str]] = {}
    for kind in ("errors", "warnings"):
        for item in payload.get(kind, []):
            file, _, msg = item.partition(": ")
            cited = _S9.search(msg)
            if cited:
                per.setdefault(file, set()).add(f"§9.{cited.group(1)}")
            elif kind == "warnings" and _RESERVED_STRUCT.search(msg):
                per.setdefault(file, set()).add("§9.3")
    return per


def main() -> int:
    lines = [
        "# oracle-diff-report",
        "",
        "`okf validate` ↔ 벤더 오라클의 파일별 §9 위반 집합 비교. 불일치는 빌드",
        "실패가 아니며, 각 항목은 `내 버그` 또는 `스펙 모호`로 분류해 처리한다.",
        "",
    ]
    total = 0
    for name, bundle in BUNDLES:
        mine, oracle = _ours(bundle), _oracle(bundle)
        mismatched = [
            (f, mine.get(f, set()), oracle.get(f, set()))
            for f in sorted(set(mine) | set(oracle))
            if mine.get(f, set()) != oracle.get(f, set())
        ]
        lines.append(f"## {name} — 불일치 {len(mismatched)}건")
        for f, m, o in mismatched:
            lines.append(
                f"- `{f}` — okf validate: {sorted(m) if m else '∅'} / 오라클: {sorted(o) if o else '∅'}"
            )
        lines.append("")
        total += len(mismatched)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"oracle diff: 총 불일치 {total}건 → {REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
