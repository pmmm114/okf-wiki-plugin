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
- `review`: 저장 시 후보를 `.okf-study/inbox.md`에 적재만 — `/study`로 드레인.
- `auto`: review + 살아있는 세션이 알아서 드레인·승격(모델 개입·trust 필요).

**설정은 `.okf-wiki.json`에, 상태는 `.okf-study/`에** — 섞지 않는다. `.okf-study/`는
자체 `.gitignore`(`*` + `!.gitignore`)로 커밋에서 제외되며 `inbox.md`(후보 큐)·
`ledger`(승격/폐기 원장)·`trust`(핸들러 로컬 승인)를 담는다.

도입 절차(설치→`/okf-init`→핸들러 계약→trust 승인→사용)와 참조 핸들러 템플릿은
repo 루트 `docs/adopting-study.md` 참조.

## 홈 프로젝트 폴백 — 유저 스코프 (Epic #91)

프로젝트 설정이 없는 위치(비-repo 폴더·무설정 repo)에서도 캡처·주입이 동작하게
하는 폴백 계층. 유저 스코프는 설정 계층이 아니라 **포인터 하나**다:

```
~/.claude/okf/home-project      # 한 줄: 홈 repo 절대경로 또는 ~/ 경로
```

- **홈 repo**는 실제 git repo로, 자기 `.okf-wiki.json`·번들·커밋 핸들러·`.okf-study/`를
  가진다 — 기존 파이프라인의 "프로젝트"로 그대로 동작한다(보안 모델 무변경).
- **생성 = 옵트인**: `/okf-init --home <path>`가 검증 후 기록. 플러그인이 임의 생성하지
  않는다. env `OKF_HOME_PROJECT`가 파일보다 우선(테스트·CI용).
- **값 규약**: 절대경로 또는 `~/` 시작(expanduser). 전후 공백·개행 무시.
- **유효 판정**: ① 대상 실재 ② git repo ③ `.okf-wiki.json` 존재 — 셋 중 하나라도
  깨지면 무효. 설정은 있으나 study 블록이 없는 **반쪽 상태는 무효가 아니라
  "주입 전용 홈"**(정상 — 캡처만 비활성).

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
| 반쪽 상태(주입 전용 홈) | 경고 없음 — doctor가 캡처 비활성 사유를 명시 |

진단·회복은 `/okf-doctor`(해소 트레이스·건강·캡처 입구 진단)와
`study scan [--enqueue]`(미큐잉 후보 결정론 탐지·멱등 재적재)가 담당한다.

> 구현 배선: 본 절은 Epic #91의 확정 계약이다. 포인터·해소 로직은 공유 모듈
> `scripts/okf_home.py` 한 곳에 두고 캡처 훅·주입 훅·doctor가 재사용한다(유닛
> #93·#94·#97에서 랜딩 — 랜딩 전까지 본 절은 사전 계약 문서).
