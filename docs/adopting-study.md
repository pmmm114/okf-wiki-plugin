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

## 7. 홈 프로젝트 폴백 — repo 밖에서도 적립 (Epic #91)

슬로건: **"자기 파이프라인이 있으면 거기로, 없으면 홈으로."**

기본 study는 소비 repo 안에서만 동작한다. 홈 폴백을 켜면 **코드 repo가 아닌 어떤
위치에서도**(스크래치 폴더·무설정 repo 포함) 캡처·주입이 사용자가 지정한 **홈
repo**(예: 소비처 KB 클론)로 흐른다.

### 셋업 (1회)

```
/okf-init --home <홈 repo 경로>     # 검증 → 포인터(~/.claude/okf/home-project) 기록
/study --trust                      # 홈 repo에서 핸들러 로컬 승인
```

홈 repo는 §1~§5를 갖춘 **보통의 소비 repo**다 — 보안 모델(커밋 핸들러 + trust)도
그대로다. 홈 설정의 `study.capture`는 `review` 권장(`auto`는 세션 시작 넛지가 모든
무설정 세션에 따라온다).

### 위치별 동작

| 내가 있는 곳 | 캡처 → | 주입 ← |
| --- | --- | --- |
| study 블록 있는 repo | 그 repo inbox | 그 repo 번들 |
| `scope:"home"` 선언 repo | **홈** inbox | 그 repo 번들 |
| 주입 전용 설정 repo(study 블록 없음) | **홈** inbox | 그 repo 번들 |
| 무설정 repo · 비-repo 폴더 | **홈** inbox | **홈** 번들 |

- 자동 캡처의 스코프는 위치가 정하고(이벤트당 정확히 하나), 의도가 있을 때만
  `/study --scope home|project`로 벽을 넘는다.
- 운영 권고: **프로젝트 파이프라인은 최소로** — 대부분의 repo는 study 블록 없이
  두면 적재가 홈 단일 경로로 수렴한다. 자기 번들이 정말 필요한 repo만 로컬
  파이프라인을 두고 핸들러를 홈 쪽으로 배선한다.

### 이력·회복

- 지식·이력의 **정본은 번들 + log.md + git**이다. inbox는 드레인되면 삭제되는
  소모품 큐 — 유실돼도 사라지는 건 미검토 후보뿐이다(캡처의 원천은 메모리 파일).
- 포인터가 깨진 기간의 미큐잉은 `study scan`(원장·inbox 대비 차집합 탐지) →
  `study scan --enqueue`(멱등 재적재)로 회복한다. 막히면 `/okf-doctor`가 현재
  위치의 스코프 해소 결과와 이유를 그대로 보여준다.
- 스코프를 넘는 중복 재큐(예: repo A에서 promote한 스니펫을 다른 위치에서 재저장)는
  **전역 원장**이 막는다 — promote/discard가 홈 원장에도 write-through되고, 캡처
  dedup은 활성 원장과 홈 원장을 함께 본다.

상세 규약(포인터 값·유효 판정·해소 규칙·침묵 정책·`study.scope`/`memoryPathPattern`
스키마)은 [`CONFIG.md`](../plugins/okf/skills/okf/reference/CONFIG.md)의 "홈 프로젝트
폴백" 절이 정본이다.

> 구현: Epic #91 전 유닛 랜딩 완료 — 캡처 폴백(#93)·주입 폴백과 마법사(#94)·
> 전역 원장(#95)·doctor와 scan(#97). 본 절의 모든 명령은 실측 검증됐다.

## 요약

| 단계 | 명령/파일 |
| --- | --- |
| 설치·초기화 | `/plugin install okf` → `/okf-init` |
| 설정 | `.okf-wiki.json` `study.capture` + `handlers` |
| 핸들러 | 커밋 경로에 실행 파일(§4 계약) |
| 승인 | `/study --trust` |
| 사용 | `/study` (`<topic>`·`--type`·`--clear`) |
| 홈 폴백(선택) | `/okf-init --home <path>` → 어디서든 적립(§7) |
