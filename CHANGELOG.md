# Changelog

릴리스 노트의 원료는 스쿼시 로그(PR 1건 = 한 줄)다 — `git log vA..vB --pretty='- %s'`
(`docs/releasing.md` 참조). 태그 하나 = repo 전체 묶음(엔진 + 플러그인 +
`actions/validate` + pre-commit)의 직배달(D2) 릴리스.

## v0.4.0 — 2026-07-22

### 추가

- **홈 = 순수 지식 목적지 — 스터디 런타임 유저 스코프 이동** (Epic #114): 런타임
  루트 리졸버로 `promote_target`(승격 대상)과 `runtime_root`(런타임 위치)를 분리
  하고, 홈/폴백 캡처의 런타임(inbox·ledger·trust·journal)을 유저 스코프
  `~/.claude/okf/study`로 이동 — 홈은 `.okf/`만 담는 순수 목적지가 된다. 홈
  자기참조도 유저 스코프로 특수처리해 홈 안 세션의 지식 재평가 뿌리를 차단
  (#121). 마법사(`/okf-init --home`)는 홈에 `.okf-study`를 만들지 않고 `study.
  capture` 설정만 켠다 (#122). 비-git 스테이징의 이벤트 저널(`journal.jsonl`)·
  `study log`·doctor 이력 + 승격 provenance를 git 홈 `log.md`로 이관 (#123).
  "지식 홈 repo 패턴" 정본 + doctor 홈 부합 진단 (#124). `study migrate`(레거시
  홈 런타임 → 유저 스코프 멱등 이동) + 런타임-in-홈 회귀 게이트 (#125)

### 수정

- 훅 exec form(`args` 존재 → 셸 없음)에서 `command`의 셸용 따옴표가 파일명에
  박혀 `posix_spawn` ENOENT가 재발하던 회귀 — `command`(단일 실행파일)/`args`
  (스크립트·서브커맨드) 분리로 해소, 그렙 게이트 강화 (#120)

## v0.3.0 — 2026-07-21

### 추가

- **study 마법사 — 주입 전용 홈에 캡처 활성 제안** (#110): `/okf-init --home`이 대상의
  캡처 준비 상태(`capture_ready`: active/off/absent)를 판정해, 캡처가 꺼진 "주입 전용
  홈"이면 동의를 받아 `study.capture: review`와 `.okf-study` 골격을 멱등 활성한다
  (`okf_home enable-capture` — 판정·편집 모두 코드 경로, 이미 auto면 격하 금지).
  doctor도 주입 전용 홈·capture=off에 켜는 법을 안내 (#111)

### 수정

- 훅 커맨드의 bare `python3` 직접 spawn이 최소 PATH 환경(GUI 앱 등)에서 ENOENT로
  죽던 회귀 — `bin/okf-py` 부트스트랩 셔틀 경유로 전환(패치 v0.2.1과 동일 수정이
  main 라인에도 포함) (#108)

## v0.2.1 — 2026-07-21

v0.2.0 태그에서 갈라 핫픽스만 백포트한 패치 릴리스(릴리스 브랜치 `release/v0.2`).

### 수정

- 훅 커맨드의 bare `python3` 직접 spawn이 최소 PATH 환경(GUI 앱 등)에서
  `posix_spawn 'python3'` ENOENT로 죽던 회귀 — 인터프리터 부트스트랩 셔틀
  `bin/okf-py` 경유로 전환(`hooks.json`·커맨드·doctor 안내문 전량), 재유입은
  그렙 게이트가 차단 (#108)

## v0.2.0 — 2026-07-21

### 추가

- **study — 메모리→지식 승격 인터페이스** (Epic #72): `/okf-init`·`.okf-study`
  스캐폴드·설정 스키마 (#81), 메모리 저장 감지 capture 훅 (#84), inbox·resolved
  원장·디스패처 코어 (#82), 내용 해시 trust 게이트 (#83), 승격 스킬 + `/study`
  (선택 승격·clear·discard·필터·auto 나즈) (#85)
- **study 홈 프로젝트 폴백 — 위치 무관 지식 적립** (Epic #91): 캡처 폴백 +
  캡처 입구 설정 우선 판정(autoMemoryDirectory·CLAUDE_CONFIG_DIR·transcript
  파생·패턴 오버라이드 합집합) + inbox 락 (#99), 주입 폴백 + `/study --scope` +
  `/okf-init --home` 마법사 (#100), `/okf-doctor` 진단 + `study scan` 회복
  스크립트 (#101), 전역 원장 write-through + 교차 승격 원장 규약 (#102)
- 훅 Python 전환 1단계 — `okf_hooks.py` 단일 진입점 + sh↔py 파리티 하네스 (#70)
- `okf init` — §9 컨포먼트 최소 번들 스캐폴드 (#66), 스킬 §2 배치 판단 스텝 (#68)

### 수정

- 캡처 입구가 `CLAUDE_CONFIG_DIR`·`autoMemoryDirectory` 사용자를 놓치던 잠복
  결함 — 설정 우선 판정으로 해소 (#99)
- 비-git 위치 `/okf-init` 스캐폴드에 fail-closed 스크립트 가드 (#105)

### 문서

- 홈 폴백 규약·스키마 확정 및 실측 정합 (#98, #103)
- study 소비처 도입 가이드 + 참조 핸들러 템플릿 (#88)
- 작업 전략 문서(브랜치·배포/버전) (#86), CLAUDE.md 규칙 보강 (#79, #89)

## v0.1.0 — 2026-07-19

최초 릴리스 — OKF 번들 엔진(validate·index·graph·context·log), Claude Code
플러그인(스킬·훅·컨텍스트 주입), `actions/validate` composite action,
pre-commit 훅 정의.
