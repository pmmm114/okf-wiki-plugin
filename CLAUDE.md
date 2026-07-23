# CLAUDE.md

OKF 번들 엔진(`okf-core/`) + Claude Code 플러그인(`plugins/okf/`) + 배포면
(`actions/validate/`, `.pre-commit-hooks.yaml`)을 담은 repo.

이 파일은 **교차 불변식·금지의 정본**이다(매 세션 로드). 서브시스템 상세는 하위 `CLAUDE.md`가
담당하며 그 디렉토리에서 작업할 때 자동 로드된다: `okf-core/CLAUDE.md`(엔진 모듈 지도·불변식),
`plugins/okf/CLAUDE.md`(플러그인 배선·셔틀), `plugins/okf/scripts/study/CLAUDE.md`(study 계약·
모듈), `okf-core/vendor/CLAUDE.md`(벤더 무수정 트립와이어).

## 명령

- 테스트: `uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q`
- 플러그인 테스트: `uv run --no-project --with pytest python -m pytest plugins/okf/tests -q`
- 린트·포맷: `uvx ruff check .` / `uvx ruff format .` (CI는 0.15.8 핀)
- 번들 검증: `uv run --project okf-core okf validate <번들경로> --strict`
- 픽스처 스냅샷 갱신: `uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py --update`
  — 갱신분은 diff 검수 후 커밋한다(스냅샷이 곧 회귀 계약).

## 어겨서는 안 되는 것

- `.github/workflows/ci.yml`의 job 이름 **`core`는 변경 금지** — 브랜치 룰셋
  required check 컨텍스트다. 검사 추가는 이 잡에 스텝 확장으로.
- `okf-core/vendor/`는 업스트림 **바이트 그대로** — 1바이트도 수정 금지
  (vendor_sync_check가 CI에서 차단). 수정이 필요하면 `vendor/patches/`에 패치로.
- 판정 상수(예약 파일명·필수/권장 필드·strict 승격 집합)는 코드에 하드코딩
  금지 — `okf-core/src/okf_core/rules/v0_1.json`이 단일 원천(그렙 테스트가 차단).
- 파스는 `parser.parse` 한 곳에서 파일당 1회 — validate/policy/index/graph/
  context는 ParsedDoc을 재사용한다(호출 카운터 테스트가 차단).
- "index가 소비하는 파일 집합 == validate §9 통과 집합" 불변식 — index 생성
  로직을 바꾸면 validate 쪽 판정과 함께 움직여야 한다(불변식 테스트가 차단).
- `plugins/okf/.claude-plugin/plugin.json`에 **version 필드 금지**(커밋 SHA
  추적). 그래서 `claude plugin validate`는 비-strict로 실행한다.
- 루트 `pyproject.toml`은 pre-commit·`pip install <repo루트>` 소비용 셔틀 —
  엔진 메타데이터의 단일 원천은 `okf-core/pyproject.toml`이다.
- 버전은 두 pyproject(루트 셔틀 + okf-core 단일 원천)가 **동기**여야 하고, main은
  **버전-중립 `0.0.0.dev0` 플레이스홀더**로 굴러간다 — 다음 minor를 미리 박는
  (`0.(Y+1).0.dev0`) 것 금지, 번호는 컷 때 도출(`docs/releasing.md`, #164). 릴리스 컷
  커밋만 dev 없는 `X.Y.Z`를 잠깐 단다(`scripts/test_version_sync.py`가 차단).
- 엔진(`okf-core/src/`)은 Claude를 모른다 — `CLAUDE_` 환경변수·claude 참조
  금지(무참조 grep 테스트가 차단). 플러그인 쪽에서만 엔진을 호출한다.
- 이 repo는 **특정 소비처·wiki·제3자 repo를 모른다**(목적지 무참조) — 코드·
  문서·설정·이슈·커밋 메시지 어디에도 특정 목적지 repo명 하드코딩 금지.
  `study` 같은 소비처 확장은 계약(stdin 아이템·env var)만 정의하고, 핸들러·
  목적지 실체는 소비처가 자기 repo에 주입한다(엔진이 Claude를 모르는 것과
  같은 계층 원리). 위반은 placeholder로 일반화해 스크럽한다. 특정 repo명을
  denylist 그렙에 넣는 것 자체가 참조가 되므로, **이 규칙(매 세션 로드되는
  CLAUDE.md)이 1차 게이트**다 — 예시·핸들러명은 중립 placeholder로만 쓴다.
- 플러그인 스크립트(훅·헬퍼)는 **Python으로** 작성 — 새 스크립트를 shell로
  만들지 말 것. `jq`+`bash` 조합 대신 표준 라이브러리(`json`·`pathlib`)로
  처리하고, 실행은 `uv run python`, 엔진 호출은 `bin/okf` 셔틀 경유. 기존
  `scripts/*.sh`는 이 규칙 이전 레거시 — 손대는 김에 Python으로 옮긴다.
  shell 예외는 `bin/`의 얇은 exec 셔틀(`okf`·`okf-py`)뿐. 훅·커맨드에서 Python
  스크립트는 bare `python3`로 부르지 말고 `bin/okf-py` 경유로 부른다 — 훅 spawn은
  로그인 쉘 PATH를 보장하지 않아 ENOENT가 난다(#108, 그렙 게이트가 차단).
  hooks.json은 exec form(`args` 존재 → 셸 없음)이라 `command`의 따옴표를 벗기지
  않는다 — `command`는 따옴표·공백 없는 단일 실행파일(`${CLAUDE_PLUGIN_ROOT}/bin/
  okf-py`)로 두고 스크립트 경로·서브커맨드는 전부 `args` 배열로 넘긴다. command에
  셸용 따옴표를 넣으면 그 따옴표가 파일명에 박혀 `posix_spawn` ENOENT가 재발한다
  (#108 후속 회귀, 그렙 게이트가 차단).

## 작업 플로우

- 모든 변경은 브랜치 → PR → CI 녹색 확인 → 스쿼시 머지. PR 본문은
  `.github/pull_request_template.md` 구조를 따른다.
- Epic이 유닛(sub-issue)으로 분해되면 유닛당 브랜치 → 유닛당 PR이 기본 —
  스쿼시가 유닛 경계를 지우므로 한 PR에 여러 유닛을 쌓지 않는다. 지정 단일
  브랜치 제약과 충돌하면 임의로 묶지 말고 유닛별 브랜치 분리 허가를 먼저 요청한다.
- CI 검사에 파괴 감지 성격이 있으면(스냅샷·해시·게이트) 고의 실패 커밋으로
  실제 red를 실증하고 원복해 기록한다 — 이 repo의 관례다.
- 브랜치 이름·커밋 규칙·머지·벤더 반영의 상세는 `docs/branching.md`(브랜치 작업
  전략 정본), 배포·버전관리(스코프 마일스톤·커밋-도출 SemVer·버전-중립 main·컷
  절차)는 `docs/releasing.md` 참조.
