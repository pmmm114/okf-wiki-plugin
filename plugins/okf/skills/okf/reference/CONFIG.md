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
