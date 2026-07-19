# CLAUDE.md

OKF 번들 엔진(`okf-core/`) + Claude Code 플러그인(`plugins/okf/`) + 배포면
(`actions/validate/`, `.pre-commit-hooks.yaml`)을 담은 repo.

## 명령

- 테스트: `uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q`
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
- 엔진(`okf-core/src/`)은 Claude를 모른다 — `CLAUDE_` 환경변수·claude 참조
  금지(무참조 grep 테스트가 차단). 플러그인 쪽에서만 엔진을 호출한다.

## 작업 플로우

- 모든 변경은 브랜치 → PR → CI 녹색 확인 → 스쿼시 머지. PR 본문은
  `.github/pull_request_template.md` 구조를 따른다.
- CI 검사에 파괴 감지 성격이 있으면(스냅샷·해시·게이트) 고의 실패 커밋으로
  실제 red를 실증하고 원복해 기록한다 — 이 repo의 관례다.
- 브랜치 이름·커밋 규칙·머지·릴리스 태그·벤더 반영의 상세는
  `docs/branching.md` 참조(브랜치 작업 전략 정본).
