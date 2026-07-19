# study 도입 가이드 (소비 repo)

`study`는 Claude Code **메모리**(일시적)를 감지·적재해 이 repo의 **OKF 지식 개념**으로
선택 승격하고, **소비처가 주입한 핸들러**로 흘려보내는 기능이다(Epic
[#72](https://github.com/pmmm114/okf-wiki-plugin/issues/72)). 플러그인은 **목적지를
모른다** — "어디로 보낼지"는 소비처가 제공하는 핸들러의 몫이다.

## 1. 설치·초기화

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf
/okf-init
```

`/okf-init`은 멱등·비파괴로 다음을 만든다:

- `.okf/` — 지식 번들(없을 때만 스캐폴드)
- `.okf-wiki.json` — 프로젝트 설정(있으면 `study` 블록만 보강)
- `.okf-study/` — 런타임 상태 디렉터리 + 자체 `.gitignore`(`*` + `!.gitignore`).
  `inbox.md`·`ledger`·`trust`는 커밋되지 않고 무시 규칙만 커밋된다.

## 2. 설정 — `.okf-wiki.json`

```json
{
  "study": {
    "capture": "review",
    "handlers": [{ "name": "kb-pr", "command": "scripts/okf-open-pr.sh" }]
  }
}
```

- `capture` 사다리 `off` ⊂ `review` ⊂ `auto`(사용자 손잡이는 이 하나뿐):
  - `off`(기본): 훅 무동작. `/study`로 수동 승격.
  - `review`(권장): 저장 시 후보만 `.okf-study/inbox.md`에 적재. `/study`로 검토·승격.
  - `auto`: review + 살아있는 세션이 능동 드레인(모델 개입·trust 필요).
- `handlers[].command`는 **git에 커밋된 repo 내 경로**여야 한다(아래 4·5).

전체 스키마: [`plugins/okf/skills/okf/reference/CONFIG.md`](../plugins/okf/skills/okf/reference/CONFIG.md).

## 3. 사용 흐름

```
메모리 저장 ──(review)──▶ .okf-study/inbox.md 후보 적재
                              │
                       /study │ (선택 승격: 판정=사람+모델)
                              ▼
        개념 작성(type+주제 하위디렉토리) → okf validate --strict
                              ▼
                    resolved 원장 기록 + inbox 드레인
                              ▼
              핸들러 디스패치(경로·git추적·trust 게이트)
```

- `/study` — 후보를 **선택적으로** 승격(전체 아님). `/study <topic>`·`/study --type X`로 한정.
- `/study --clear` — 현재 후보 전부 discard(재적재 방지 원장 기록).
- 카테고리 = 승격 시점 `type`(필수) + **주제 하위디렉토리**(`tags` 아님).

## 4. 핸들러 계약

핸들러는 **훅 모양의 실행 파일**이다. 승격된 개념마다 한 번 호출된다.

- **입력(stdin)** — study 아이템 JSON:
  ```json
  {
    "source": "manual",
    "project": "/abs/repo",
    "concept": { "path": ".okf/<...>.md", "type": "<type>", "topic": "<주제-디렉토리>" }
  }
  ```
- **입력(env var)** — 편의 접근: `OKF_TRIGGER`(manual|memory)·`OKF_CONCEPT_PATH`·
  `OKF_CONCEPT_TYPE`·`OKF_CONCEPT_TOPIC`.
- **종료코드** — `0` 성공, 비0 실패(디스패처가 격리, 나머지 핸들러엔 영향 없음).
- **위치 요건** — `command`는 **repo 트리 안 + git 추적** 경로. `.okf-study/` 하위·미추적·
  repo 밖(심링크/`..` 포함)은 **거부(fail-closed)**.

## 5. trust 승인 (필수)

핸들러 실행은 **로컬 승인**이 필요하다 — 커밋되는 `.okf-wiki.json`이 코드 실행을
좌우하지 못하게 하는 게이트다.

```
/study --trust
```

- 승인은 `.okf-study/trust`(gitignore·로컬)에 **핸들러 셋 내용 해시**로 저장된다 →
  프레시 클론은 항상 미승인에서 시작.
- 해시 입력 = 핸들러 `name` + 정규화 경로 + **스크립트 바이트 SHA-256** + `capture`.
  스크립트 내용·핸들러 셋·capture가 바뀌면 **재승인** 강제.
- 미승인 상태의 `auto`는 **가시적 저하**: 개념은 로컬 번들에 승격·검증되고
  **핸들러 실행만 보류**된다("N개 승격됨; `/study --trust`로 승인" 안내).

## 6. 참조 핸들러 템플릿

계약을 실증하는 예시가 [`examples/okf-open-pr.sh.example`](examples/okf-open-pr.sh.example)에
있다. **그대로 쓰는 활성 핸들러가 아니라** 소비처가 자기 커밋 경로(예: `scripts/`)로
복사·수정하는 골격이다. 목적지 repo는 하드코딩하지 말고 소비처가 채운다.

## 요약

| 단계 | 명령/파일 |
| --- | --- |
| 설치·초기화 | `/plugin install okf` → `/okf-init` |
| 설정 | `.okf-wiki.json` `study.capture` + `handlers` |
| 핸들러 | 커밋 경로에 실행 파일(§4 계약) |
| 승인 | `/study --trust` |
| 사용 | `/study` (`<topic>`·`--type`·`--clear`) |
