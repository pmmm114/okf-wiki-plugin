# CLAUDE.md

OKF 번들 엔진(`okf-core/`) + Claude Code 플러그인(`plugins/okf/`) + 배포면
(`actions/validate/`, `.pre-commit-hooks.yaml`)을 담은 repo.

## 구조

- `okf-core/src/okf_core/` — 엔진 9모듈. `parser.parse`의 ParsedDoc을
  `validate`(§9)·`policy`·`index`(§6)·`graph`·`context`가 재사용, `cli`가
  서브커맨드를 각 모듈 `main`으로 위임. `init`=번들 스캐폴드, `logmd`=log.md.
- `okf-core/vendor/`(업스트림 `spec`·`oracle` 참조검증기) + `okf-core/scripts/`
  (픽스처·오라클차동·vendor동기·라이선스 검사).
- `plugins/okf/` — `scripts/core/`(엔진 인접: doctor·hooks·layers·remote·vault),
  `scripts/study/`(소비처 확장), `commands/`·`skills/okf/`·`hooks/`·`bin/`.
- `scripts/`(루트) — 릴리스·버전 툴링 + 게이트 pytest(version_sync·doc_links·security_scan).

## 명령

- 테스트: `uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q`
- 플러그인 테스트: `uv run --no-project --with pytest python -m pytest plugins/okf/tests -q`
- 툴링 테스트: `uv run --no-project --with pytest python -m pytest scripts -q`
- 린트·포맷: `uvx ruff check .` / `uvx ruff format .` (CI는 0.15.8 핀)
- 번들 검증: `uv run --project okf-core okf validate <번들경로> --strict`
- 픽스처 스냅샷 갱신: `uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py --update`
  — 갱신분은 diff 검수 후 커밋(스냅샷이 곧 회귀 계약).

## 어겨서는 안 되는 것

`게이트`는 위반을 CI/테스트로 차단하는 검사다.

- `ci.yml` job 이름 **`core` 불변** — 브랜치 룰셋 required check 컨텍스트. 이름이 갈리면 잡이 red가 되는 게 아니라 required check가 매칭되지 않아 **아무것도 막지 않게 된다**. 검사는 새 잡 말고 카테고리 composite action(`.github/actions/<이름>/action.yml`)에 스텝으로 추가하고 `core`에서 `uses:`로 부른다 — **reusable workflow(`uses: ./.github/workflows/x.yml`)는 잡 레벨 호출이라 금지**(잡이 늘고 컨텍스트가 `core / <피호출자>`로 갈림). composite은 스텝 레벨이라 잡이 하나로 남는다(게이트: `test_repo_contract` — 잡이 정확히 하나이고 이름이 `core`, 액션 참조 정합, 액션 파일에 `jobs:` 금지).
- 이 repo는 **public** — 올라가면 안 되는 것은 **추적되기 전에** 막는다. `.gitignore`는 추적되지 않은 파일에만 듣고 `git add -f` 한 번이면 뚫리므로, 무시 대상이 추적 중인지·키/인증서/환경파일·개발 머신 절대경로(`/Users/…`)·워크플로 최소권한 `permissions:` 선언을 함께 본다(게이트: `security_scan` — CI `보안` 스텝과 lefthook pre-push가 **같은 코드**). 시크릿 **값** 탐지는 gitleaks(CI 전용, 커밋 SHA 핀)에 맡기고 이 게이트는 무네트워크·무의존으로 남긴다 — 값 탐지 룰셋은 전용 도구의 몫이고, 로컬에서 통과한 것이 CI에서도 통과해야 한다. push 시점 방어선(GitHub secret scanning·push protection)은 `repo_settings.py`가 룰셋과 같이 **확인만** 한다.
- `okf-core/vendor/`는 업스트림 **바이트 그대로** — 수정은 `vendor/patches/`에 패치로(게이트: vendor_sync_check).
- 판정 상수(예약 파일명·필수/권장 필드·strict 승격 집합) 하드코딩 금지 — 단일원천 `rules/v0_1.json`(게이트: 그렙).
- 파스는 `parser.parse`로 **파일당 1회**, 소비자는 ParsedDoc 재사용(게이트: 호출 카운터).
- 불변식 **index 소비집합 == validate §9 통과집합** — index 로직을 바꾸면 validate 판정도 함께(게이트: 불변식).
- `plugin.json`에 **version 필드 금지**(커밋 SHA 추적, 소비처가 고정하는 태그가 곧 버전) → `claude plugin validate`는 비-strict(게이트: `test_repo_contract`).
- 루트 `pyproject.toml`은 pre-commit·`pip install <루트>` 소비용 **셔틀**, 엔진 메타 단일원천은 `okf-core/pyproject.toml`. 두 버전 **동기**, main은 **버전-중립 `0.0.0.dev0`** — minor 선점(`0.(Y+1).0.dev0`) 금지, 번호는 컷 때 도출, 릴리스 컷 커밋만 dev 없는 `X.Y.Z`(게이트: `test_version_sync`; `docs/releasing.md`, #164).
- 엔진(`okf-core/src/`)은 **Claude를 모른다** — `CLAUDE_`·claude 참조 금지, 엔진 호출은 플러그인 쪽에서만(게이트: 무참조 grep).
- 이 repo는 **특정 소비처·목적지 repo를 모른다**(목적지 무참조) — 코드·문서·설정·이슈·커밋 어디에도 목적지 repo명 하드코딩 금지. `study` 같은 소비처 확장은 **계약만**(stdin 아이템·env var) 정의하고 핸들러·목적지는 소비처가 자기 repo에 주입(엔진이 Claude를 모르는 것과 같은 계층 원리). denylist에 repo명을 넣는 것 자체가 참조이므로 **이 CLAUDE.md가 1차 게이트** — 예시·핸들러명은 중립 placeholder로.
- 플러그인 스크립트(훅·헬퍼)는 **Python으로** — shell 신규 작성 금지(`jq`+`bash` 대신 `json`·`pathlib`, 실행 `uv run python`, 엔진 호출 `bin/okf`). 기존 `scripts/*.sh`는 레거시 — 손대는 김에 이관. shell 예외는 `bin/`의 exec 셔틀(`okf`·`okf-py`)뿐.
- (#108) 훅·커맨드의 Python은 bare `python3` 금지, `bin/okf-py` 경유 — 훅 spawn은 로그인 쉘 PATH 미보장이라 ENOENT. `hooks.json`은 exec form(`args` 존재 → 셸 없음)이라 `command` 따옴표를 안 벗기므로 `command`는 따옴표·공백 없는 단일 실행파일로 두고 경로·서브커맨드는 `args` 배열로 — 따옴표를 넣으면 `posix_spawn` ENOENT 재발. 둘 다 그렙 게이트가 차단.

## 작업 플로우

- 모든 변경: 브랜치 → PR → CI 녹색 → 스쿼시 머지. PR 본문은 `.github/pull_request_template.md` 구조.
- 이슈 생성은 선택 — 만들면 PR↔이슈 **1:1**(한 PR이 이슈 하나만 닫음), 안 만들면 그냥 여닫음. Epic이 유닛(sub-issue)으로 분해되면 **유닛당 브랜치·PR**(한 PR에 여러 유닛 금지 — 스쿼시가 경계를 지움). 원자적 착지가 필요하거나 단일 브랜치 제약과 충돌하면 **`epic/<n>` 통합 브랜치**(유닛→통합→main, `epic`은 브랜치 전용 접두), 그래도 안 되면 유닛별 분리 허가를 먼저 요청. 상세 `docs/branching.md` §Epic과 유닛 분해.
- CI에 파괴 감지 성격(스냅샷·해시·게이트)이 있으면 고의 실패 커밋으로 red 실증 후 원복·기록 — 이 repo 관례.
- 상세: 브랜치·커밋·머지·벤더 반영은 `docs/branching.md`, 배포·버전관리(스코프 마일스톤·커밋-도출 SemVer·버전-중립 main·컷 절차)는 `docs/releasing.md`.
