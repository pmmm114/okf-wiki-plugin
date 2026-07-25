# 기여 가이드

이 문서는 okf-wiki-plugin에 기여할 때 알아 둘 정확성 게이트와, 엔진 CLI(`okf`)를
직접 부르는 방법을 정리합니다. 이 repo는 성능 벤치마크가 아니라 **컨포먼스 계약**으로
정확성을 지킵니다. 스펙이 정한 판정을 그대로 지키고 있는지, 그리고 그 판정이 예전과
달라지지 않았는지를 CI가 매번 확인합니다.

모든 변경은 브랜치를 따서 PR로 올리고, CI가 녹색이 된 다음 스쿼시 머지합니다.
브랜치와 커밋 규칙, 배포와 버전 관리, 에이전트가 지켜야 할 작업 규칙으로 가는 길은
문서 끝의 [관련 문서](#관련-문서)에 모아 뒀습니다.

## 컨포먼스와 회귀 계약

`okf validate`는 OKF 스펙의 [컨포먼스 규칙](okf-core/vendor/spec/SPEC.md#9-conformance)
가운데 세 규칙만 error로 보고하고, 나머지는 warn으로 둡니다. 스펙이 "거부하라"고
요구하는 규칙만 error로 올리기 때문입니다.

어떤 파일명이 예약돼 있는지, 어떤 필드가 필수이고 어떤 필드가 권장인지, `--strict`를
켰을 때 무엇을 error로 승격할지 같은 **판정 상수는 코드에 하드코딩하지 않습니다.**
단일 원천은 [`rules/v0_1.json`](okf-core/src/okf_core/rules/v0_1.json) 하나뿐이고,
그렙 테스트가 이를 강제합니다.

CI의 `core` 잡은 아래 검사를 게이트로 겁니다. 모두 로컬에서 그대로 재현할 수 있습니다.

| 게이트 | 하는 일 |
| --- | --- |
| 자기 번들 검증 | 이 repo의 `.okf/`를 `--strict`로 검증합니다(도그푸딩) |
| 픽스처 스위트 | 픽스처마다 `validate --format json` 출력을 `tests/expected/*.json` 스냅샷과 비교합니다. 이 스냅샷이 곧 회귀 계약입니다 |
| 오라클 차동 | 벤더 업스트림 검증기와 파일별 컨포먼스 위반 집합을 비교합니다. 리포트만 내고 빌드를 실패시키지는 않습니다 |
| vendor 동기화 | `okf-core/vendor/`가 업스트림과 **바이트 그대로**인지 확인합니다. 1바이트만 달라도 차단합니다 |
| 라이선스 검사 | 벤더로 반입한 것들의 라이선스 고지가 맞는지 확인합니다 |
| 플러그인 검증 | `claude plugin validate`를 돌립니다. `plugin.json`은 커밋 SHA로 추적하므로 비-strict로 실행합니다 |

### 로컬에서 재현하기

```bash
uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q   # 엔진 테스트
uv run --no-project --with pytest python -m pytest plugins/okf/tests -q # 플러그인 테스트
uvx ruff check . && uvx ruff format --check .                          # 린트·포맷 (CI는 0.15.8 핀)
uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py       # 픽스처 스냅샷
```

## 엔진 CLI (`okf`)

플러그인을 쓰는 쪽에서는 이 CLI를 직접 부를 일이 거의 없습니다. 스킬이 대신 실행해
주기 때문입니다. 아래는 CI와 pre-commit, 그리고 기여자가 직접 부르는 호출면입니다.

```
okf validate <path> [--strict] [--format json]   # 컨포먼스 검사
okf index    <path> [--write]                     # index.md 재생성
okf graph    <path> --json [--linked-to P] [--edges-from KEY] [--chain C]  # 링크·역링크·근거 사슬
okf context  <path> [--max-chars N] [--group-by KEY] [--filter KEY=VALUE]  # 주입용 압축 인덱스(층별 그룹·필터)
okf log      append <path> -m MSG                 # log.md 항목 추가
okf init     <dir>                                # 컨포먼트 최소 번들 스캐폴드
```

`validate`의 종료코드는 세 가지입니다. 컨포먼트면 `0`, 비컨포먼트면 `1`, 실행 오류가
나면 `2`입니다. `--format json`을 붙이면 발견 1건마다 `{"file","rule","level","msg"}`
객체 하나를 만들어 출력합니다.

### 이 repo에서 직접 실행하기

이 repo를 클론해서 엔진 CLI를 직접 써 보고 싶다면 uv나 pip 가운데 하나를 씁니다.

```bash
uv run --project okf-core okf validate .okf --strict   # uv (권장)
pip install ./okf-core && okf validate .okf --strict   # 또는 pip (repo 루트 설치도 동작)
```

## 관련 문서

| 문서 | 무슨 내용인지 |
| --- | --- |
| [브랜치 작업 전략](docs/branching.md) | 브랜치와 커밋, 머지, 벤더 반영 전략. 게이트를 새로 만들 때 일부러 깨뜨려 실제로 막히는지 확인하는 [파괴 감지 검사](docs/branching.md#파괴-감지-검사)도 여기 있습니다 |
| [배포·버전관리 전략](docs/releasing.md) | 배포와 버전 관리 — 스코프 마일스톤, 커밋에서 도출하는 SemVer, 릴리스 컷 절차 |
| [CLAUDE.md](CLAUDE.md) | 에이전트가 지켜야 할 작업 규칙과 어겨서는 안 되는 불변식의 정본, 그것을 강제하는 게이트 |
| [README](README.md) | 프로젝트 소개와 Getting Started, 동작 방식 |
