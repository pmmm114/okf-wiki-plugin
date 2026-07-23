# 기여 가이드

이 repo는 벤치마크가 아니라 **컨포먼스 계약**으로 정확성을 지킵니다. 기여할 때
알아 둘 정확성 게이트와, 엔진 CLI를 직접 쓰는 법을 정리합니다.

브랜치·커밋·머지 규칙은 [docs/branching.md](docs/branching.md), 배포·버전 관리는
[docs/releasing.md](docs/releasing.md), 에이전트 작업 규칙(불변식·게이트)은
[CLAUDE.md](CLAUDE.md)를 참고하세요.

## 컨포먼스와 회귀 계약

`okf validate`는 OKF §9의 세 규칙만 error로 보고하고, 나머지는 warn으로 둡니다.
어떤 파일명이 예약이고 어떤 필드가 필수·권장인지, `--strict`에서 무엇을 error로
올릴지 같은 **판정 상수는 코드에 하드코딩하지 않습니다.** 단일 원천은
[`rules/v0_1.json`](okf-core/src/okf_core/rules/v0_1.json) 하나뿐이고, 그렙 테스트가
이를 강제합니다.

CI의 `core` 잡이 아래를 게이트로 겁니다. 모두 로컬에서 그대로 재현할 수 있습니다.

| 게이트 | 하는 일 |
| --- | --- |
| 자기 번들 검증 | 이 repo의 `.okf/`를 `--strict`로 검증한다(도그푸딩) |
| 픽스처 스위트 | 픽스처별 `validate --format json` 출력을 `tests/expected/*.json` 스냅샷과 비교한다 — 이 스냅샷이 곧 회귀 계약이다 |
| 오라클 차동 | 벤더 업스트림 검증기와 파일별 §9 위반 집합을 비교한다(리포트 전용, 빌드를 실패시키지는 않는다) |
| vendor 동기화 | `okf-core/vendor/`가 업스트림과 **바이트 그대로**인지 확인한다(1바이트만 달라도 차단) |
| 라이선스 검사 | 벤더 반입물의 라이선스 고지가 맞는지 확인한다 |
| 플러그인 검증 | `claude plugin validate`를 돌린다(비-strict — plugin.json은 커밋 SHA로 추적) |

### 로컬에서 재현하기

```bash
uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q   # 엔진 테스트
uv run --no-project --with pytest python -m pytest plugins/okf/tests -q # 플러그인 테스트
uvx ruff check . && uvx ruff format --check .                          # 린트·포맷 (CI는 0.15.8 핀)
uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py       # 픽스처 스냅샷
```

## 엔진 CLI (`okf`)

플러그인 사용자는 이 CLI를 직접 부를 일이 거의 없습니다 — 스킬이 대신 실행해 주기
때문입니다. 아래는 CI·pre-commit·기여자가 직접 부르는 호출면입니다.

```
okf validate <path> [--strict] [--format json]   # §9 컨포먼스 검사
okf index    <path> [--write]                     # §6 형식 index.md 재생성
okf graph    <path> --json [--linked-to P]        # 링크 그래프·역링크 조회
okf context  <path> [--max-chars N]               # 주입용 압축 인덱스
okf log      append <path> -m MSG                 # log.md 항목 추가(§7)
okf init     <dir>                                # §9 컨포먼트 최소 번들 스캐폴드
```

`validate` 종료코드는 `0`(컨포먼트) / `1`(비컨포먼트) / `2`(실행 오류)입니다.
`--format json`을 주면 발견 1건마다 `{"file","rule","level","msg"}` 객체를 출력합니다.

이 repo를 클론해서 엔진 CLI를 직접 써 보려면:

```bash
uv run --project okf-core okf validate .okf --strict   # uv (권장)
pip install ./okf-core && okf validate .okf --strict   # 또는 pip (repo 루트 설치도 동작)
```
