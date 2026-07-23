# plugins/okf/scripts/study — CLAUDE.md

`study` 기능 계층. Claude Code 메모리(일시)를 감지→스테이징→선택 승격해 `.okf/` 지식 개념
(영속)으로 올리고, **소비처가 주입한 핸들러**로 흘려보낸다. 판정(후보→개념)은 사람+모델의
몫이고, 이 스크립트들은 기계적 작업만 한다.

> 플러그인 배선·셔틀·Python 전용·core⊥study 경계의 정본은 상위 `../../CLAUDE.md`, 설정·스코프
> 해소·vault 폴백은 `../../skills/okf/reference/CONFIG.md`, 도입·핸들러 계약 상세는
> `../../../../docs/adopting-study.md`다. 이 파일은 **study 안에서 일할 때** 필요한 모듈 지도·
> 목적지 계약·study-국소 불변식만 담는다.

## 모듈 지도

- `study.py` — 오케스트레이션 CLI(`list`/`resolve`/`clear`/`dispatch`/`scan`/`log`/`near`/`migrate`).
  스코프를 `study_scope`로 풀고 승격 대상(repo)과 런타임 루트(inbox/ledger/trust)를 분리한다.
- `study_scope.py` — 캡처 스코프 해소(4규칙)·런타임 루트 분리·메모리 경로 감지. `okf_vault`를 단방향 import.
- `study_store.py` — SQLite 스테이징 스토어(`study.db`: candidate/line/resolution/event). `_sqlite3`
  없으면 `available()`가 False → 호출자 fail-closed. 읽기는 DB를 만들지 않는다.
- `study_inbox.py` — 스토어 위 공개 API(안정 시그니처). content-hash id, 후보 append/list/drop/clear,
  near_duplicates, 이벤트 저널, resolved 원장(**유저 스코프 전역 원장에 write-through**로 스코프 넘는 재큐 차단).
- `study_blocks.py` — **캡처 원자(개념 블록)의 단일 정의.** 훅과 `scan`이 같은 `concept_blocks`를
  써 동일 후보 집합을 낸다.
- `study_trust.py` — trust 게이트. 승인 해시 = 정렬된 `{name + repo상대경로 + sha256(스크립트 바이트)}`
  + `capture`. `<runtime>/trust`(gitignore)에 저장 → 프레시 클론은 늘 미승인. 스크립트 내용·핸들러 셋·
  capture가 바뀌면 재승인을 강제한다.
- `study_dispatch.py` — 디스패처 코어(라이브러리, CLI 없음, real `trust_check` 없이는 거부). 핸들러를
  경로 게이트(repo 트리 정규화·`..`/심링크 거부) + git추적 + trust 게이트 뒤에서 실행하고, 핸들러별로 실패를 격리한다.
- `study_simhash.py` — SimHash 근사중복 지문(stdlib). **자문 전용** — 정확 content-hash dedup·원장 앵커를 대체하지 않는다.
- `study_session.py` — SessionStart 넛지(`auto` + 대기 후보). 무효 포인터 1줄 경고의 방출점(SessionStart 계열이 경고 소유).
- `study_hook.py` — PostToolUse(Write) 캡처 훅. 메모리 저장 감지 후 정책대로 개념 블록을 inbox에 append.
  **승격·디스패치는 안 함**(모델 부재). 무효 포인터엔 침묵.
- `study_legacy.py` — 레거시 markdown 리더(마이그레이션 전용, #134). `study migrate`가 옛 트리오를 `study.db`로 이관.
- `study_doctor.py` — `/okf-doctor`의 study 절반(`okf_doctor`가 try-import하는 옵션 위임 seam). 스토어
  건강·캡처 트레이스·회복 안내.

## 목적지 계약 (핸들러 인터페이스)

`study_dispatch.py`는 **목적지를 모른다.** 소비처가 `.okf-wiki.json`의 `study.handlers`로 핸들러를
주입하고, 디스패처는 계약만 안다:

- **입력(stdin)** — study 아이템 JSON(`source`·`project`·`concept{path,type,topic}`).
- **입력(env)** — `OKF_TRIGGER`·`OKF_CONCEPT_TYPE`·`OKF_CONCEPT_TOPIC`·`OKF_CONCEPT_PATH`·`OKF_PROJECT`.
- **실행 cwd** — 승격 대상 repo 루트(`OKF_PROJECT`).
- **위치 요건** — `command`는 repo 트리 안 + **git 추적** 경로. `.okf-study/` 하위·미추적·repo 밖은 거부(fail-closed).
- **종료코드** — `0` 성공, 비0 실패(디스패처가 격리, 나머지 핸들러엔 무영향).

핸들러 실체·목적지 repo명은 절대 여기 두지 않는다. 계약 상세·URL vault 격리(임시 `git worktree`)는
`../../../../docs/adopting-study.md` §4가 정본이다.

## 어겨서는 안 되는 것 (study 국소 — 각 강제 테스트)

- **stdlib 전용(스테이징)** — `study_store`·`study_simhash`·`study_blocks`·`study_legacy`는 stdlib+로컬만
  import한다. numpy/scipy/datasketch/simhash 등 서드파티 금지(그래서 SimHash도 stdlib로 직접 구현).
  `../../tests/test_staging_stdlib_gate.py`가 차단.
- **sqlite fail-closed** — `_sqlite3` 없는 파이썬에선 스테이징이 조용한 noop이 된다. 스토어 op는 그걸
  감지해 fail-closed하고 `/okf-doctor`가 `OKF_PYTHON`을 안내한다
  (`../../tests/test_study_scan_doctor.py::test_doctor_flags_missing_sqlite`). 이 경로를 우회해 부분 기록하지 말 것.
- **정확 해시가 앵커, SimHash는 자문** — dedup·원장의 단일 기준은 content-hash다. SimHash는 표시만 하고
  자동 병합·게이팅하지 않는다(`../../tests/test_study_simhash.py::test_near_duplicates_is_advisory_only`).
- **훅 ↔ scan 동일 후보** — 캡처 원자는 `study_blocks.concept_blocks` 한 곳에서만 정의한다. 훅과 scan이
  다른 경계를 쓰면 id가 어긋난다(`../../tests/test_study_blocks.py::test_hook_and_scan_agree_on_block_ids`).
- **경고 방출 비대칭** — 무효 포인터 경고는 SessionStart 계열만 낸다. PostToolUse 캡처(`study_hook`)는
  무효 포인터에 **침묵**한다(저장 경로에 잡음 금지).
- **런타임은 vault에 안 쌓는다** — vault는 순수 목적지다. 캡처 스테이징(inbox/ledger/trust/`study.db`)은
  유저 스코프(`~/.claude/okf/study`)에 두고 vault repo엔 만들지 않는다
  (`../../tests/test_study_migrate.py::test_gate_vault_fallback_runtime_never_in_vault`).
- **목적지 무참조** — 핸들러 실체·특정 repo명을 이 계층에 두지 않는다(상위 `../../CLAUDE.md`의 무참조
  규칙). 예시는 중립 placeholder로만.
