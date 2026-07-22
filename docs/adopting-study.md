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
  스테이징 `study.db`(후보·원장·저널)·WAL 사이드카·`trust`는 커밋되지 않고 무시
  규칙만 커밋된다.

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
메모리 저장 ──(review)──▶ 개념 블록 후보 적재(스테이징 study.db)
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

## 7. 홈 프로젝트 폴백 — repo 밖에서도 적립 (Epic #91·#114)

슬로건: **"자기 파이프라인이 있으면 거기로, 없으면 홈으로."**

기본 study는 소비 repo 안에서만 동작한다. 홈 폴백을 켜면 **코드 repo가 아닌 어떤
위치에서도**(스크래치 폴더·무설정 repo 포함) 캡처·주입이 사용자가 지정한 **홈
repo**(예: 소비처 KB 클론)로 흐른다.

### 지식 홈 repo 패턴 (#114)

홈은 **순수 지식 목적지**다 — 큐레이션된 지식만 담고 런타임 스테이징을 담지 않는다.

| 요소 | 규약 |
| --- | --- |
| 구조 | `.okf/` 큐레이션 번들(index·log·개념, strict-valid) + `.okf-wiki.json`(홈 지목 설정, `study.capture`). **런타임(inbox/ledger/trust)은 홈에 없다** |
| 런타임 위치 | `~/.claude/okf/study`(유저 스코프) — 스테이징은 홈 repo가 아니라 여기에 쌓인다 |
| 역할 | 승격 대상 — `/study`가 후보를 검수해 `.okf/`에 큐레이션 편집만 쓴다(git diff로 확인·커밋) |
| 검증 | `okf validate .okf --strict`(번들 건강) + `/okf-doctor`(홈 부합·스코프 트레이스) |

→ 홈은 스캐폴드·조작 대상이 아니다. **홈 안에서 세션을 열 필요가 없고**(그러면
okf 스킬 유지 플로우가 켜져 기존 지식을 재평가한다), 승격만 `/study`로 한다.

### 셋업 (1회)

```
/okf-init --home <홈 repo 경로>     # 검증 → 포인터 기록 + (주입 전용이면) 캡처 활성 제안
/study --trust                      # 홈 핸들러 로컬 승인(있으면)
```

홈 repo는 `.okf/`가 이미 있는 지식 repo면 된다. `study.capture`가 꺼져 있으면
마법사가 켜기를 제안한다 — 홈 `.okf-wiki.json`의 설정만 켜고 런타임은 유저
스코프에 둔다(홈엔 `.okf-study`를 만들지 않는다). `review` 권장(`auto`는 세션
시작 넛지가 모든 무설정 세션에 따라온다).

### 위치별 동작

| 내가 있는 곳 | 캡처(스테이징) → | 주입 ← |
| --- | --- | --- |
| study 블록 있는 repo | 그 repo `.okf-study` | 그 repo 번들 |
| `scope:"home"` 선언 repo | **유저 스코프** | 그 repo 번들 |
| 주입 전용 설정 repo(study 블록 없음) | **유저 스코프** | 그 repo 번들 |
| 무설정 repo · 비-repo 폴더 | **유저 스코프** | **홈** 번들 |
| 홈 repo 자신 | **유저 스코프** | 홈 번들 |

승격은 언제나 홈 `.okf/`로 간다(위 스테이징 → `/study` 검수 → 홈 번들). 자동 캡처의
스코프는 위치가 정하고(이벤트당 정확히 하나), 의도가 있을 때만 `/study --scope
home|project`로 벽을 넘는다.

### 이력·회복

- 지식·이력의 **정본은 번들 + log.md + git**이다. 스테이징(후보 큐·원장·이벤트 저널)은
  **단일 SQLite `study.db`**(#130)에 담기는 소모성 런타임 상태다 — 드레인되면 소모된다.
  순서·시각 이력은 `study log`(이벤트 저널)로 조회하고, 승격 시 캡처 일자·재등장 수를
  홈 `.okf/log.md`에 새겨 **버저닝을 git에 남긴다**(#114 U5 · #132).
- 캡처 원자는 **개념 블록**(#131) — 여러 줄에 걸친 한 개념이 후보 1개다. 재캡처는
  재등장 카운터를 올리고(#132), 재서술된 근사중복은 `study near`가 자문 표시한다
  (SimHash — 자동병합·게이팅 없음, 정확 해시 앵커 불변, #133).
- 포인터가 깨진 기간의 미큐잉은 `study scan` → `study scan --enqueue`(멱등)로
  회복한다. 막히면 `/okf-doctor`가 스코프 해소·홈 부합·스토어 건강(`_sqlite3` 유무·
  레거시 markdown 잔존)을 보여준다. 옛 markdown 스테이징은 `study migrate`가 `study.db`
  로 멱등 이관한다(#134).
- 스코프를 넘는 중복 재큐는 **전역(유저 스코프 공유) 원장**이 막는다 —
  promote/discard가 공유 원장에도 write-through되고 dedup이 함께 본다.

상세 규약(포인터 값·유효 판정·해소 규칙·침묵 정책·스키마)은
[`CONFIG.md`](../plugins/okf/skills/okf/reference/CONFIG.md)의 "홈 프로젝트 폴백"
절이 정본이다.

> 구현: Epic #91(폴백·마법사·전역 원장·doctor) + #114(런타임 유저 스코프 분리·홈
> 순수 목적지·이벤트 저널). 본 절의 모든 명령은 실측 검증됐다.

## 요약

| 단계 | 명령/파일 |
| --- | --- |
| 설치·초기화 | `/plugin install okf` → `/okf-init` |
| 설정 | `.okf-wiki.json` `study.capture` + `handlers` |
| 핸들러 | 커밋 경로에 실행 파일(§4 계약) |
| 승인 | `/study --trust` |
| 사용 | `/study` (`<topic>`·`--type`·`--clear`) |
| 홈 폴백(선택) | `/okf-init --home <path>` → 어디서든 적립(§7) |
