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
