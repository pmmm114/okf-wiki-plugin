# okf-core — CLAUDE.md

OKF v0.1 번들 엔진. 파서 · §9 컨포먼스 검사기 · index/graph/context 생성기 · `okf`
CLI. 독립 Python 패키지(`hatchling`, `src/okf_core` 레이아웃)이며 **Claude · 플러그인 ·
특정 소비처 · 목적지를 전부 모른다** — 의존은 플러그인→엔진 단방향이다.

> 교차 불변식·금지의 정본은 루트 `../CLAUDE.md`, 컨포먼스 계약·CLI 호출면은
> `../CONTRIBUTING.md`, §9 해석은 자기 번들 `../.okf/`(dogfood)다. 이 파일은 **엔진
> 안에서 일할 때** 필요한 모듈 지도·명령·엔진-국소 불변식(+강제 테스트)만 담는다.

## 명령 (repo 루트에서 실행)

- 엔진 테스트: `uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q`
- 자기 번들 검증(도그푸딩): `uv run --project okf-core okf validate .okf --strict`
- 픽스처 스위트(회귀 계약): `uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py`
  - 스냅샷 갱신: 위 명령에 `--update` — diff 검수 후 커밋한다(스냅샷이 곧 회귀 계약).
- CLI 직접: `uv run --project okf-core okf <sub> …`, 미설치 실행은 `bin/okf` 셔틀.

## 모듈 지도 (`src/okf_core/`)

- `parser.py` — **단일 파스 지점.** `parse()`→`ParsedDoc`, `walk_bundle()`가 `*.md`를
  정렬 순회하며 파일당 1회 파스한다. BOM/CRLF 정규화, 인라인 링크 추출(이미지·펜스
  내부 제외).
- `validate.py` — §9 컨포먼스. `validate_bundle()`가 파이프(walk→§9→policy→strict
  승격)를 돈다. rule id `OKF9.1/9.2/9.3`, `Finding{file,rule,level,msg}`. `load_rules()`가
  규칙 로더다.
- `policy.py` — 권장 필드 warn 등 소프트 정책. 넘겨받은 ParsedDoc을 재사용한다(재파스 없음).
- `index.py` — §6 `index.md` 재생성. §9 통과 개념만 나열하고, 하위디렉토리는 베어
  `<name>/`가 아니라 `<name>/index.md`로 링크한다.
- `graph.py` — 링크·역링크 그래프. `--edges-from KEY`로 임의 frontmatter 축 엣지,
  `--chain`으로 근거 사슬 BFS.
- `context.py` — 주입용 압축 인덱스(`<okf-context>`). 절단은 **글자 수만**(기본 8000).
- `logmd.py` — §7 `log.md` 변경(`## YYYY-MM-DD` 그룹, 최신 먼저).
- `cli.py` — 서브커맨드 6종(validate·index·graph·context·log·init)을 각 모듈 main으로 위임.
- `init.py` — 컨포먼트 최소 번들 스캐폴드. 산출물은 `--strict` 자기검증을 통과해야 한다.
- `rules/v0_1.json` — **판정 상수 단일 원천**(예약 파일명·필수/권장 필드·strict 승격
  집합·§7 날짜 패턴). 코드에 하드코딩 금지(아래 불변식).

`okf` 종료코드: `0` 컨포먼트 / `1` 비컨포먼트 / `2` 실행 오류(not-a-dir·인자 오류).

## 어겨서는 안 되는 것 (엔진 국소 — 각 강제 테스트)

- **단일 파스** — `parser.parse`는 파일당 1회. validate/policy/index/graph/context는
  `walk_bundle` 결과를 재사용하고 루프에서 재파스하지 않는다.
  `tests/test_policy_rules.py::test_pipeline_parses_each_file_once`(appendix-a는 정확히
  6회)가 차단.
- **판정 상수 단일 원천** — 예약 파일명·필수 필드를 `validate.py`/`policy.py`에 리터럴로
  박지 않는다. `tests/test_policy_rules.py::test_no_rule_constants_in_validate_or_policy_code`가
  차단. 새 규칙 버전은 `rules/vX_Y.json`으로 추가하면 로더가 `okf_version`으로 자동 선택한다.
- **"index 소비 집합 == validate §9 통과 집합"** — 색인 생성 로직을 바꾸면 §9 판정과 함께
  움직여야 한다(둘 다 `validate.concept_conforms` 경유).
  `tests/test_invariants.py::test_invariant_index_consumes_exactly_section9_pass_set`가
  차단(감도 앵커: violations→∅, appendix-a→3이라 공집합 동치로 회피 불가).
- **무-Claude · 무-목적지** — `src/okf_core/`의 `.py`/`.json`에 `CLAUDE_`·`claude`(대소문자
  무관) 문자열, 특정 소비처·축 어휘 하드코딩 금지.
  `tests/test_invariants.py::test_invariant_okf_core_knows_no_claude`가 차단. `--group-by`/
  `--filter`/`--edges-from`는 **임의** frontmatter 키를 받고 엔진은 그 의미를 해석하지 않는다
  (택소노미 중립).
- **self-output 컨포먼스** — `okf init`·`okf index --write` 산출물은 `--strict` 재검증을
  통과해야 한다(`tests/test_init.py`·`tests/test_index_graph_context.py`). 베어
  디렉토리 링크(`<name>/`)는 정적 웹뷰에서 깨지므로 회귀 가드가 잡는다.
- **context 절단은 글자 수만** — 개념 수 상한(maxConcepts류)은 폐기된 안티패턴이니
  재도입 금지.

## 벤더 (`vendor/`)

`vendor/spec/SPEC.md`·`vendor/oracle/okf_validate.py`는 업스트림 **바이트 그대로** —
오라클을 포함해 1바이트도 수정 금지. `scripts/vendor_sync_check.py`가 `vendor.lock`의
파일별 sha256과 대조해 차단한다. 수정이 필요하면 벤더 파일이 아니라 `vendor/patches/`에
패치 + `vendor.lock` 갱신으로 반영한다(ruff도 `vendor/`를 제외하니 "포맷 정리"도 금지).
오라클은 CI 차동용(리포트 전용, 빌드 실패 아님)이고, §9.3 예약구조를 warn으로만 내는
차이는 `scripts/oracle_diff.py` 어댑터가 흡수한다 — 오라클을 고쳐 맞추지 않는다.

## 버전

`okf-core/pyproject.toml`의 `version`이 **숫자 단일 원천**, 루트 `../pyproject.toml`은 동기
셔틀이다. main은 버전-중립 `0.0.0.dev0` 플레이스홀더로 굴러간다 — 실번호 기입·사전 minor
상향 금지(`../scripts/test_version_sync.py`가 차단, 릴리스 컷 커밋만 잠깐 실번호). 컷 절차는
`../docs/releasing.md`.
