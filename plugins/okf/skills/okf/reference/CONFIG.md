# `.okf-wiki.json` — 프로젝트 설정 (T-P5-5 확정)

소비 repo **루트**에 두는 프로젝트 스코프 설정 파일이다. 플러그인의
`session_start.sh`는 프로젝트 설정을 **이 파일 하나에서만** 읽는다
(userConfig는 유저 스코프이므로 프로젝트별 값에 쓰지 않는다). 파일이 없으면
훅은 아무것도 하지 않는다.

```json
{
  "bundlePath": ".okf",
  "context": { "maxChars": 8000, "wrapperTag": "okf-context" },
  "inject": true
}
```

| 키 | 타입 | 기본값 | 의미 |
| --- | --- | --- | --- |
| `bundlePath` | string | `".okf"` | 번들 루트(프로젝트 상대 경로) |
| `context.maxChars` | number | `8000` | 주입 컨텍스트 문자 상한 — 훅 additionalContext 10,000자 한도의 마진 |
| `context.wrapperTag` | string | `"okf-context"` | 래퍼 태그 이름. v1 엔진은 고정값만 지원 — 예약 필드 |
| `inject` | boolean | `true` | `false`면 SessionStart 컨텍스트 주입을 생략 |

모든 키는 선택이며 생략 시 기본값이 적용된다. 알 수 없는 키는 무시한다.

## `study` — 메모리→지식 승격 (Epic #72)

메모리 저장을 감지해 지식 개념으로 승격하고, 소비처가 주입한 핸들러로 흘려보내는
`study` 기능의 설정. `/okf-init`이 이 블록과 `.okf-study/` 런타임 디렉터리를 만든다.

```json
{
  "study": {
    "capture": "off",
    "handlers": [{ "name": "wiki-pr", "command": "scripts/okf-open-pr.sh" }]
  }
}
```

| 키 | 타입 | 기본값 | 의미 |
| --- | --- | --- | --- |
| `study.capture` | string | `"off"` | 자동 캡처 사다리 `off` ⊂ `review` ⊂ `auto`. 블록 부재 = `off` |
| `study.scope` | string | `"project"` | 적재 목적지. `"home"`이면 캡처를 **사용자 홈 파이프라인으로 위임**(아래 홈 폴백 절). 목적지만 위임하는 키 — 활성화 키가 아니며 `capture` 부재 시 여전히 `off`. `"home"`일 때 동반 `handlers` 등 로컬 파이프라인 키는 무시 |
| `study.memoryPathPattern` | string | — | 캡처 입구(메모리 경로) 판정 오버라이드 — **절대경로 대상 정규식**. 자동 판정(설정 조회·기본형·transcript 파생)이 전부 빗나갈 때의 탈출구로, **홈 repo 설정에서만** 의미. 무효 정규식은 stderr 1줄 후 무시 |
| `study.handlers` | array | `[]` | 승격 후 실행할 핸들러 배열(배선, 튜닝 아님) |
| `study.handlers[].name` | string | — | 핸들러 표시 이름 |
| `study.handlers[].command` | string | — | 실행 파일 경로. **git에 커밋된 repo 내 경로**여야 함(미추적·`.okf-study/` 하위는 거부) |

`capture` 사다리(한 칸당 자동 단계 하나 추가):

- `off`(기본): 저장 감지 훅 무동작 — `/study`로 수동 승격.
- `review`: 저장 시 **개념 블록** 후보를 스테이징 스토어에 적재만 — `/study`로 드레인.
- `auto`: review + 살아있는 세션이 알아서 드레인·승격(모델 개입·trust 필요).

**설정은 `.okf-wiki.json`에, 상태는 런타임 루트에** — 섞지 않는다. 런타임 루트는
후보 큐·승격/폐기 원장·이벤트 저널을 **하나의 SQLite `study.db`**(#130)와 `trust`
(핸들러 로컬 승인)로 담고, 위치는 **스코프가 정한다**(#114): 자기 study 블록이
있는 프로젝트는 `<repo>/.okf-study/`(자체 `.gitignore`로 `study.db`·WAL 사이드카까지
커밋 제외), 홈/폴백은 유저 스코프 `~/.claude/okf/study/`. 홈은 순수 목적지라 런타임을
담지 않는다.

- **캡처 원자는 개념 블록**(#131): 여러 줄에 걸친 한 개념이 후보 1개로 묶인다(줄-해시
  자식 병존으로 ledger 연속성). 재캡처는 재등장 카운터를 올리고(#132), 재서술된
  근사중복은 `study near`로 자문 표시(SimHash — 자동병합·게이팅 없음, 정확 해시 앵커
  불변, #133). 지식 정본은 git 번들 + `log.md`이고 `study.db`는 소모성 런타임 상태다.
- **`_sqlite3` 부재 파이썬**에선 스테이징이 fail-closed(무동작)한다 — `OKF_PYTHON`을
  SQLite 포함 빌드로 지정하면 활성된다(`/okf-doctor`가 감지·안내). 옛 markdown
  스테이징(pre-0.5)은 `study migrate`가 `study.db`로 멱등 이관한다(#134).

도입 절차(설치→`/okf-init`→핸들러 계약→trust 승인→사용)와 참조 핸들러 템플릿은
repo 루트 `docs/adopting-study.md` 참조.

## 홈 프로젝트 폴백 — 유저 스코프 (Epic #91)

프로젝트 설정이 없는 위치(비-repo 폴더·무설정 repo)에서도 캡처·주입이 동작하게
하는 폴백 계층. 유저 스코프는 설정 계층이 아니라 **포인터 하나**다:

```
~/.claude/okf/home-project      # 한 줄: 홈 repo 절대경로(~/ 경로) 또는 repo URL(#153)
```

- **홈 repo**는 실제 git repo로, 자기 `.okf-wiki.json`·번들(`.okf/`)·커밋 핸들러를
  가진 **순수 지식 목적지**다(#114). 런타임(inbox/ledger/trust)은 홈이 아니라 유저
  스코프 `~/.claude/okf/study`에 쌓인다 — 홈엔 `.okf-study`를 만들지 않는다. 보안
  모델(커밋 핸들러 + trust)은 무변경이되 trust 파일 저장만 유저 스코프로 옮긴다.
- **생성 = 옵트인**: `/okf-init --home <path|url>`가 검증 후 기록. 플러그인이 임의 생성하지
  않는다. env `OKF_HOME_PROJECT`가 파일보다 우선(테스트·CI용).
- **값 규약**: **로컬 경로**(절대경로 또는 `~/` 시작, expanduser) 또는 **repo URL**
  (아래 URL 모드) 둘 중 하나. 전후 공백·개행·따옴표 무시.
- **유효 판정(로컬 경로)**: ① 대상 실재 ② git repo ③ `.okf-wiki.json` 존재 — 셋 중 하나라도
  깨지면 무효. 설정은 있으나 study 블록이 없는 **반쪽 상태는 무효가 아니라
  "주입 전용 홈"**(정상 — 캡처만 비활성).

### URL 포인터 모드 — 관리형 clone (#153)

포인터에 **repo URL**을 주면 로컬 clone 위치를 정하고 유지할 필요가 없고(온보딩 단순화)
설정이 머신 간 이식된다(로컬 절대경로는 머신마다 다르다). 플러그인이 유저 스코프에
**관리형 clone**을 두고, 하류(주입·캡처·승격·디스패치·trust)는 로컬 경로 홈과 **동일
파이프라인**을 탄다.

```
~/.claude/okf/remotes/<slug>/   # 관리형 clone. slug = sanitize(host/path)-sha256(canonical)[:8]
```

- **감지·해소**: 값이 `scheme://`(https/ssh/git/file) 또는 `git@host:path`(scp-like)면 URL
  모드. `home_state`는 URL→canonical→slug→로컬 clone 경로 매핑만 하는 **순수(무네트워크)
  분류기**다 — clone/fetch는 절대 여기서 하지 않는다(매 `.md` Write 훅 핫패스 보호).
- **transport 허용**: `https`·`ssh`·`git`·`file`만. `user:token@` 크레덴셜은 포인터에
  저장하지 않고(git credential helper·ssh-agent 위임), `ext::` 등 명령 실행 transport는
  거부한다. canonical은 host 소문자·유저정보 제거·scp→ssh·`.git`/트레일링 슬래시 제거로
  같은 repo의 서로 다른 표기를 한 slug로 수렴시킨다.
- **생성 = 옵트인**: `set <url>`은 URL만 기록하고 **clone하지 않는다**. `/okf-init --home <url>`
  마법사가 동의를 받아 관리형 clone을 만든다. 미생성 상태는 무효 사유 **"URL 포인터 —
  관리형 clone 미생성"**(로컬 오탈자 "대상 없음"과 구분)으로 SessionStart가 안내한다.
- **신선도**: SessionStart가 **fetch-only**(origin ref만, worktree 불변·bounded·TTL dedup).
  워킹트리 ff-only 갱신은 `/study` 진입에서 clean-gate(미커밋 잔재 없음) 통과 시에만.
  오프라인·인증 실패는 **캐시로 저하**(주입 계속, PR만 보류)하고 1줄 경고. env
  `OKF_REMOTE_OFFLINE=1`로 fetch 강제 중단, `OKF_REMOTE_FETCH_TTL`(초)로 dedup 창 조정.
- **캡처 옵트인**: URL 홈은 `enable-capture`가 clone의 커밋 설정을 편집하지 않는다(origin
  diverge 방지) — **원격 repo에 `study.capture`를 커밋**하면 다음 fetch로 반영된다.
- **진단**: `/okf-doctor`가 clone 상태·마지막 fetch·behind·dirty·이원화(로컬↔관리형)를
  **무네트워크**(로컬 git 메타)로 표시한다.
- **주입 전용 홈에 캡처 켜기**: `/okf-init --home`이 대상의 캡처 준비 상태
  (`capture_ready`: `active`/`off`/`absent`)를 판정해, 캡처가 꺼져 있으면 동의를 받아
  홈 `study.capture: review`(설정)만 켠다(스크립트 `study_scope.py enable-capture <홈>`
  — 판정·편집 모두 코드 경로). 런타임(inbox/ledger/trust)은 **홈이 아니라 유저
  스코프 `~/.claude/okf/study`**에 보장한다 — 홈엔 `.okf-study`를 만들지 않는다
  (#114 U2, 홈은 순수 목적지). 홈 설정에 쓰기이므로 활성만 사용자 선택이고,
  이미 `auto`면 격하하지 않는다.

### 스코프 해소 규칙

슬로건: **"자기 파이프라인이 있으면 거기로, 없으면 홈으로."**

캡처(쓰기 — 메모리 저장 훅):

```
1. 프로젝트 study 블록 + "scope": "home"  → 홈 (캡처 레벨은 블록 값)
2. 프로젝트 study 블록 (scope 생략)       → 프로젝트 (capture=off 포함 — 명시가 이긴다)
3. 블록 없음 + 포인터 유효 + 홈 capture ∈ {review, auto} → 홈 (레벨은 홈 설정)
4. 아니면 무동작
```

주입(읽기 — SessionStart 컨텍스트):

```
1. 프로젝트 .okf-wiki.json 존재 → 프로젝트 번들
2. 아니고 포인터 유효 + 홈 inject ≠ false → 홈 번들
3. 아니면 무동작
```

- 판별자가 다른 이유: 캡처는 **study 블록**, 주입은 **설정 파일** 기준 — 주입 전용
  얇은 설정 repo에서 "주입=프로젝트, 캡처=홈" 혼합이 성립한다(그 repo엔 inbox가
  없으므로 충돌 없음).
- 한 이벤트의 스코프는 항상 **정확히 하나** — 이중 캡처·이중 디스패치 없음.
- `/study --scope home|project`로 어디서든 명시 지정 가능.

### 침묵 정책

| 상태 | 동작 |
| --- | --- |
| 포인터 없음(옵트인 안 함) | 완전 무음 — 기존 동작과 동일 |
| 포인터 있으나 무효 | SessionStart 계열 훅만 1줄 경고(+`/okf-doctor` 안내). PostToolUse 캡처 훅은 무음 스킵 |
| URL 포인터·clone 미생성(#153) | 무효로 취급 — SessionStart가 "관리형 clone 미생성"을 안내(로컬 오탈자와 구분). `/okf-init --home <url>`로 옵트인 생성 |
| URL 신선도 실패(오프라인·인증) | fail-closed 아님 — 주입은 clone 캐시로 계속, 1줄 경고 + PR만 보류 |
| 반쪽 상태(주입 전용 홈) | 경고 없음 — doctor가 캡처 비활성 사유를 명시 |

진단·회복은 `/okf-doctor`(해소 트레이스·건강·캡처 입구 진단)와
`study scan [--enqueue]`(미큐잉 후보 결정론 탐지·멱등 재적재)가 담당한다.

> 구현 배선: 포인터·홈 판정(로컬/URL)·주입 해소는 generic 공유 모듈 `scripts/okf_home.py`에,
> 캡처 스코프·런타임 루트·캡처 입구 판정은 study 층 `scripts/study_scope.py`에 있다
> (#145 U3 분할 — import는 study_scope→okf_home 단방향). URL 모드의 git I/O(clone·fetch·
> refresh)는 generic `scripts/okf_remote.py`가 소유한다 — `okf_home`은 무네트워크 순수
> 분류기로 남고, 네트워크는 명시 지점(clone 옵트인·SessionStart fetch·`/study` refresh)에만
> (#153 C6). 캡처 훅(`study_hook`·`study_session`)은 `study_scope`를, 주입 훅(`okf_hooks`
> session-start)은 `okf_home`+`okf_remote`(fetch-only)를 재사용한다. doctor는
> generic(`okf_home`·`okf_remote`)만 하드 의존하고 study 진단(캡처 트레이스·입구·스토어·
> inbox·회복)은 `study_doctor` 심으로 선택 위임한다(#145 U4 — 있으면 실행, 없으면 생략).
> promote/discard는 유효 홈이 있으면 홈 원장에도 write-through되어 스코프를 넘는 재큐를
> 막는다(전역 원장). CLI: `study_scope.py status|set|enable-capture`,
> `okf_remote.py clone|sync|refresh|status`, `study.py scan [--enqueue]`, `okf_doctor.py` —
> 해소 규칙은 Epic #91에서 랜딩, 모듈 분할은 #145 U3·U4, URL 모드는 #153.
