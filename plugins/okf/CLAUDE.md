# plugins/okf — CLAUDE.md

Claude Code 플러그인. OKF 스킬 + 세션 컨텍스트 주입 훅 + `/okf-init`·`/study`·`/okf-doctor`
커맨드 + `study` 승격 파이프라인. 엔진(`okf-core`)을 `bin/okf` 셔틀로 호출하지만 **지식이
어디로 가는지는 모른다** — 목적지는 소비처가 핸들러로 주입한다.

> 교차 불변식·금지의 정본은 루트 `../../CLAUDE.md`, 설정 스키마·스코프 해소는
> `skills/okf/reference/CONFIG.md`, 인식층은 `skills/okf/reference/LAYERS.md`, study 내부
> 상세는 `scripts/study/CLAUDE.md`다. 이 파일은 **플러그인 안에서 일할 때** 필요한 배선·
> 셔틀·플러그인-국소 불변식(+강제 테스트)만 담는다.

## 명령 (repo 루트에서 실행)

- 플러그인 테스트: `uv run --no-project --with pytest python -m pytest plugins/okf/tests -q`
- 플러그인 검증: `claude plugin validate ./plugins/okf` (**비-strict** — plugin.json에 version 없음)

## 배선 지도

- `.claude-plugin/plugin.json` — 매니페스트. **version 필드 없음**(SHA 추적, 불변식).
- `hooks/hooks.json` — SessionStart·PostToolUse·FileChanged 훅. **exec form**(`args` 존재 → 셸 없음).
- `commands/*.md` — `/okf-init`(멱등 셋업)·`/study`(승격 플로우, 판정=사람+모델)·`/okf-doctor`
  (스크립트 출력 그대로, 재량 없음). 기계적 작업은 전부 스크립트에 위임한다.
- `skills/okf/SKILL.md` — 번들 작성·유지·소비 스킬. 검증/색인/그래프/로그는 전부 `okf` CLI에 위임.
- `skills/okf/reference/` — `CONFIG.md`(설정·스코프 정본)·`LAYERS.md`(인식층 정본)·`SPEC.md`(벤더 스펙 심링크).
- `bin/okf` — 엔진 셔틀(`uv run --project core okf …`, `core`는 `okf-core`로의 git 심링크).
- `bin/okf-py` — Python 부트스트랩 셔틀. PYTHONPATH에 `scripts/core:scripts/study` 주입 + 인터프리터 탐색.
- `scripts/core/` — 제네릭 `okf_*` 계층: `okf_vault`(스코프 해소)·`okf_doctor`·`okf_remote`(관리형
  clone)·`okf_hooks`(훅 엔트리)·`okf_layers`(접지 린트).
- `scripts/study/` — `study` 기능 계층 → `scripts/study/CLAUDE.md`.

## 어겨서는 안 되는 것 (플러그인 국소 — 각 강제 테스트)

- **plugin.json에 version 금지** — SHA로 추적하므로 `claude plugin validate`는 비-strict로 돈다.
  version을 넣으면 그 전제가 깨진다.
- **훅·커맨드의 Python은 `bin/okf-py` 경유** — bare `python3` 금지. 훅 spawn은 로그인 셸 PATH를
  보장하지 않아 ENOENT가 난다(#108). `tests/test_okf_py_shim.py`(hooks.json·`commands/*.md` grep)가 차단.
- **hooks.json exec-form 따옴표 함정** — `command`는 따옴표·공백 없는 **단일 실행파일 토큰**
  (`${CLAUDE_PLUGIN_ROOT}/bin/okf-py`)이어야 하고, 스크립트 경로·서브커맨드는 전부 `args` 배열로
  넘긴다. `command`에 셸 따옴표를 넣으면 그 문자가 파일명에 박혀 `posix_spawn` ENOENT가 재발한다
  (#108 후속). 같은 테스트가 차단.
- **Python 전용 스크립트** — 새 훅·헬퍼는 Python(stdlib `json`/`pathlib`)으로 쓰고 `bin/okf-py`로
  실행한다. shell 예외는 `bin/`의 두 얇은 셔틀(`okf`·`okf-py`)뿐. 기존 `scripts/*.sh`
  (`session_start`·`file_changed`·`post_tool_use`)는 규칙 이전 레거시 — 손대는 김에 Python으로 옮긴다.
  주의: `okf_hooks.py`가 3종의 바이트-패리티 대체본이나 현재 hooks.json은 `session-start`만 배선하고,
  나머지 둘은 아직 `.sh`가 라이브다 — 셸이 죽었다고 가정하지 말 것.
- **core ⊥ study 경계** — `scripts/core/*`는 `study_*`를 import하지 않는다. 유일 허용 seam은
  `okf_doctor.py → study_doctor`(옵션 위임)뿐이고, `okf_vault`는 subprocess-free를 유지한다.
  `tests/test_core_study_boundary_gate.py`(정확 일치 allowlist)·
  `test_okf_vault.py::test_okf_vault_is_subprocess_free`가 차단.
- **stdlib 전용 · py3.10 하한** — 훅·런타임 스크립트는 소비처 시스템 `python3`(하한 3.10)로 돈다.
  CI "훅 py3.10 하한 게이트"가 `okf_hooks`·`okf_vault`·`okf_remote`를 3.10 `py_compile`로 검증하고,
  study 스테이징 모듈의 stdlib 전용은 `tests/test_staging_stdlib_gate.py`가 별도 차단.
- **목적지 무참조** — 특정 소비처·wiki·목적지 repo명을 `scripts/study/study_dispatch.py`·
  `commands/study.md`·`skills/okf/reference/CONFIG.md` 어디에도 하드코딩 금지. 핸들러는 소비처가
  `.okf-wiki.json` + stdin/env 계약으로 주입하고, 예시는 중립 placeholder(`wiki-pr` 등)로만 쓴다.
  이 규칙(매 세션 로드되는 CLAUDE.md)이 1차 게이트다.
- **심링크 보존** — `core → okf-core`(bin/okf의 엔진)·`skills/okf/reference/SPEC.md → 벤더 스펙`은
  둘 다 git 추적 심링크다. 끊거나 실체 파일로 치환하지 말 것.
