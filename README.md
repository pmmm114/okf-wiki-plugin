# okf-wiki-plugin

**오픈소스 · 로컬 우선 · 사용자 소유 · Markdown 네이티브 지식**

[OKF(Open Knowledge Format) v0.1](okf-core/vendor/spec/SPEC.md) 지식 번들을
만들고, 검증하고, Claude Code 세션에 주입하는 도구 모음입니다. 에이전트가 세션마다
처음부터 다시 시작하지 않도록, 지식을 `cat` 가능한 markdown으로 남겨 **git에서
버저닝**하고 다음 세션·다른 에이전트가 그대로 이어받게 합니다.

> **비공식 고지** — 이 프로젝트는 Google과 무관한 비공식 도구이며, OKF
> 스펙 원문은 Apache-2.0으로 무수정 벤더링했습니다
> ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). 코드는 MIT
> ([LICENSE](LICENSE)).

| 구성 | 경로 | 역할 |
| --- | --- | --- |
| 엔진 | `okf-core/` | 파서·§9 검사기·index/graph/context 생성기 + `okf` CLI |
| 플러그인 | `plugins/okf/` | Claude Code 플러그인 — 스킬 + 세션 컨텍스트 주입 훅 + `study` 승격 |
| CI 소비면 | `actions/validate/` | 소비 repo용 composite action |
| pre-commit | `.pre-commit-hooks.yaml` | 소비 repo용 pre-commit 훅 정의 |

## 왜 okf-wiki-plugin인가

| 필요 | okf-wiki-plugin이 주는 것 |
| --- | --- |
| 세션이 끝나면 맥락이 사라지고 다음 에이전트가 처음부터 시작한다 | 지식을 `.okf/` git 번들의 **개념**으로 남겨, 다음 세션·다른 에이전트가 그대로 이어받는다 |
| 답의 근거가 코드·대화·문서에 흩어져 있다 | 개념은 설명이 아니라 **답**(스키마 컬럼·조인 키·명령어·수치)을 담고, 백링크·인용으로 근거를 연결한다 |
| 지식이 특정 도구·SaaS 포맷에 갇힌다 | `cat` 가능한 **markdown + YAML frontmatter**를 git으로 배포 — SDK·스키마 레지스트리·중앙 권위가 필요 없다(OKF v0.1) |
| 메모리는 일시적이고 취향과 지식이 섞인다 | `study`가 **장기 지식만** 골라 개념으로 선택 승격한다 — 스테이징은 드레인되면 소모된다 |
| 커밋된 설정 파일이 임의 코드를 실행할 수 있다 | 핸들러 실행은 **로컬 trust 승인**이 게이트다 — fresh clone은 항상 미승인에서 시작한다 |
| 형식이 흔들리면 소비가 조용히 깨진다 | `okf validate`의 **§9 컨포먼스 검사** + CI 픽스처 스냅샷·오라클 차동이 회귀를 계약으로 막는다 |

## Getting Started

**목표** — 첫 개념 하나를 남기고, 그것이 다음 세션에 주입되는 것까지 확인합니다.
5단계, 약 5분.

**필요한 것** — Claude Code, 그리고 지식을 담을 git repo 하나. 엔진은 플러그인에
동봉되어 있어 별도 설치 스텝이 없습니다.

### 1. 플러그인 설치

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf@okf-wiki-plugin
```

### 2. 번들 초기화

지식을 담을 repo에서 실행합니다.

```
/okf-init
```

멱등·비파괴입니다 — 여러 번 실행해도 안전하고 이미 있는 파일은 보존합니다.

| 산출물 | 역할 | git |
| --- | --- | --- |
| `.okf/` | 지식 번들(개념·index·log) — 없을 때만 스캐폴드 | 커밋 |
| `.okf-wiki.json` | 프로젝트 설정(주입 · `study`) — 있으면 보강 | 커밋 |
| `.okf-study/.gitignore` | 런타임 무시 규칙(`*` + `!.gitignore`) | 커밋 |
| `.okf-study/study.db`(+WAL) | 후보 큐·원장·이벤트 저널(SQLite) | gitignored |
| `.okf-study/trust` | 핸들러 로컬 승인 해시 | gitignored |

> git repo가 아닌 폴더에서는 가드가 exit 3으로 차단합니다. 그 자리에서는
> `/okf-init --home <홈 repo 경로>`로 홈 포인터만 씁니다
> ([스코프 절](#스코프-우선순위와-설정-user--project) 참조).

### 3. 세션 주입 켜기

`/okf-init`이 만든 repo 루트 `.okf-wiki.json`에 주입 설정이 있는지 확인합니다.

```json
{
  "bundlePath": ".okf",
  "context": { "maxChars": 8000 },
  "inject": true
}
```

이 파일이 있어야 SessionStart 훅이 번들 요약을 세션에 넣습니다. 없으면 훅은 아무것도
하지 않습니다(fail-open — 지식 번들이 없는 repo의 일반 작업에는 개입하지 않습니다).
전체 스키마: [CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md).

### 4. 첫 개념 작성

개념은 설명이 아니라 **답**을 담습니다 — 스키마 컬럼, 조인 키, 명령어, 수치처럼
다음 세션이 곧바로 쓸 수 있는 것. 한 파일에 한 개념이고, **주제 하위디렉토리**로
묶습니다.

`.okf/deploy/release.md`:

````markdown
---
type: concept
title: Release
description: 릴리스 컷 명령과 승인 게이트.
---

# 릴리스 컷

```
make release VERSION=x.y.z
```

- 승인자: 2명 이상
- 롤백 창: 배포 후 30분
````

지켜야 할 규칙은 셋입니다 — frontmatter `type` **필수**, `description` **1문장**,
주제 하위디렉토리 배치. Claude에게 "이 내용을 개념으로 적재해줘"라고 맡기면
okf 스킬이 이 규칙대로 배치·작성하고 index까지 재생성합니다.

### 5. 검증하고 주입을 확인

Claude에게 "번들 검증해줘"라고 하면 스킬이 검사기를 실행합니다. 통과하면 이렇게
나옵니다.

```
컨포먼트: error 0건, warn 0건
```

이어서 "주입될 컨텍스트 보여줘"라고 하면 압축 인덱스를 그대로 출력합니다.

```
<okf-context>
deploy/release.md [concept] — 릴리스 컷 명령과 승인 게이트.
</okf-context>
```

**이 블록이 다음 세션 시작 시 그대로 주입되는 텍스트입니다.** 새 세션을 열어
확인해 보세요. 여기까지 왔으면 루프가 닫혔습니다 — `.okf/`를 커밋하면 팀과 다음
세션이 이 지식을 그대로 이어받습니다.

지금 이 위치에서 캡처·주입이 **어디로 가는지** 확인하려면:

```
/okf-doctor
```

### 다음 단계

- 메모리를 자동으로 후보에 쌓고 골라 승격하기 → [`study`](#단기-기억과-장기-기억-study)
- 팀 repo에 CI·pre-commit 검사 걸기 → [소비 repo 배포면](#ci와-pre-commit으로-번들-검증-소비-repo)
- repo 밖 어디서나 홈 repo로 적립하기 → [스코프 설정](#스코프-우선순위와-설정-user--project)

## 동작 방식

엔진은 **파일당 한 번만 파싱**한다. `parser.parse`가 만든 `ParsedDoc`을
validate·index·graph·context가 재사용한다(재파싱 금지 — 호출 카운터 테스트로
고정). 세션 주입은 플러그인 계층이 담당한다 — **엔진(`okf-core/`)은 Claude를
모른다**(무참조 grep 불변식).

```
파일(.okf/*.md)
   │  parser.parse   (파일당 1회 → ParsedDoc 재사용)
   ▼
ParsedDoc ──┬─▶ validate   §9 컨포먼스 검사 (3규칙 error / 나머지 warn)
            ├─▶ index      §6 형식 index.md 재생성
            ├─▶ graph      링크·역링크 그래프
            └─▶ context    주입용 압축 인덱스
                              │
   Claude Code SessionStart 훅 │  (플러그인 계층 — 엔진 밖)
                              ▼
                  <okf-context> 세션 자동 주입
```

1. **파스 1회, 판정 재사용** — validate/policy/index/graph/context가 같은
   `ParsedDoc`을 공유한다.
2. **§9 3규칙만 error** — 스펙이 거부를 요구하지 않는 항목은 warn이다(`--strict`는
   권장 필드 위반을 error로 승격). 판정 상수는 코드가 아니라
   [`rules/v0_1.json`](okf-core/src/okf_core/rules/v0_1.json)이 단일 원천.
3. **"index 소비 집합 == validate 통과 집합" 불변식** — 색인 로직을 바꾸면 검증
   판정과 함께 움직여야 한다(불변식 테스트가 차단).
4. **주입은 압축 컨텍스트** — 번들 전체가 아니라 `context`가 만든 압축 인덱스를
   `<okf-context>` 블록으로 세션에 넣는다(문자 상한은 `.okf-wiki.json`에서 조정).
5. **fail-open** — 소비 repo에 `.okf-wiki.json`이 없으면 훅은 아무것도 하지
   않는다. 지식 번들이 없는 repo의 일반 작업에는 개입하지 않는다.

## 단기 기억과 장기 기억 (`study`)

`study`는 Claude Code **메모리**(일시적)를 감지·적재해 이 repo의 **OKF 지식
개념**(영구)으로 선택 승격하고, **소비처가 주입한 핸들러**로 흘려보내는
기능이다. 플러그인은 목적지를 모른다 — "어디로 보낼지"는 소비처가 제공하는
핸들러의 몫이다([docs/adopting-study.md](docs/adopting-study.md)).

| 계층 | 저장소 | 수명 |
| --- | --- | --- |
| 단기 — 메모리 | Claude Code 메모리(일시) | 세션·휘발 |
| 캡처 스테이징 | `.okf-study/study.db`(SQLite, gitignored) + `trust` | 드레인되면 소모(소모성 런타임) |
| 장기 — 지식 개념 | `.okf/` git 번들 + `log.md` | 영구(git 버저닝) |

지식·이력의 **정본은 번들 + log.md + git**이다. 스테이징(후보 큐·승격/폐기
원장·이벤트 저널)은 단일 SQLite `study.db`에 담기는 소모성 상태로, 드레인되면
사라진다 — 승격 시 캡처 일자·재등장 수를 `log.md`에 새겨 **버저닝을 git에
남긴다**.

### 승격 게이트

okf에는 자동 점수나 자동 병합이 **없다** — 승격 판정은 사람과 모델의 몫이다.
게이트는 다음으로 구성된다:

- **캡처 사다리** `off ⊂ review ⊂ auto` — 사용자 손잡이는 이 하나뿐.
  - `off`(기본): 훅 무동작. `/study`로 수동 승격.
  - `review`(권장): 저장 시 개념 블록 후보만 스테이징에 적재. `/study`로 검토·승격.
  - `auto`: review + 살아있는 세션이 능동 드레인(모델 개입·trust 필요).
- **선별** — 장기 지식(스키마·명령·결정·규약)만. 상호작용 취향·일회성은 제외.
- **재등장 카운터** — 같은 개념이 반복 캡처되면 카운터가 오른다(반복 학습 신호).
- **근사중복 자문(SimHash)** — 재서술된 후보를 `study near`가 해밍거리로 표시한다.
  **자문 전용**이며 자동병합·게이팅은 없다(정확 해시 앵커는 불변).
- **드레인** — `okf validate --strict` 통과분만 원장에 `promoted`로 기록하고
  inbox에서 제거한다. 버릴 후보는 `discarded`(동일 스니펫 재적재 방지).

```
메모리 저장 ──(review)──▶ 개념 블록 후보 적재(스테이징 study.db)
                              │
                       /study │ (선택 승격: 판정 = 사람 + 모델)
                              ▼
        개념 작성(type + 주제 하위디렉토리) → okf validate --strict
                              ▼
                    원장에 promoted 기록 + inbox 드레인
                              ▼
              핸들러 디스패치(커밋 경로 · trust 게이트)
```

카테고리는 승격 시점의 `type`(필수) + **주제 하위디렉토리**이다 — `tags`는 선택
메타일 뿐 배치·필터 축이 아니다.

## 신뢰 경계와 스코프

### 핸들러 trust 경계

승격된 개념을 소비처 핸들러로 넘기는 실행은 **로컬 승인**이 필요하다 —
커밋되는 `.okf-wiki.json`이 코드 실행을 좌우하지 못하게 하는 게이트다.

```
/study --trust
```

- 승인은 `.okf-study/trust`(gitignore·로컬)에 **핸들러 셋 내용 해시**로 저장된다
  → 프레시 클론은 항상 미승인에서 시작한다.
- 해시 입력 = 핸들러 `name` + 정규화 경로 + **스크립트 바이트 SHA-256** +
  `capture`. 스크립트 내용·핸들러 셋·capture가 바뀌면 **재승인**을 강제한다.
- 핸들러 `command`는 **git에 커밋된 repo 내 경로**여야 한다 — 미추적·`.okf-study/`
  하위·repo 밖(심링크/`..` 포함)은 거부(fail-closed).
- 미승인 상태의 `auto`는 **가시적 저하**다: 개념은 로컬 번들에 승격·검증되고
  **핸들러 실행만 보류**된다("N개 승격됨; `/study --trust`로 승인" 안내).

### 스코프 우선순위와 설정 (User / Project)

`study`의 적재 목적지는 **두 스코프**로 설정한다. 해소기가 위치마다 **정확히
하나**를 고른다 — 슬로건대로 **"자기 파이프라인이 있으면 거기로(Project), 없으면
홈으로(User)."**

| 스코프 | 설정 위치 | 런타임·trust | 이기는 조건 |
| --- | --- | --- | --- |
| **Project** | `<repo>/.okf-wiki.json`의 `study` 블록 | `<repo>/.okf-study/` | 그 repo에 `study` 블록이 **있을 때**(명시가 이긴다 — `capture:"off"`도 이 자리에선 홈 폴백을 끈다) |
| **User** | 포인터 `~/.claude/okf/home-project` → 홈 repo | `~/.claude/okf/study/` | repo에 `study` 블록이 **없을 때**, 비-git 폴더 |

우선순위는 **Project 블록이 있으면 Project, 없으면 User(홈)**. 한 이벤트의 스코프는
늘 정확히 하나이며, `/study --scope home|project`로 그때그때 벽을 넘는다.

**User scope — 비-git 폴더 어디서나 홈 repo로 적재:**

```
/okf-init --home <홈 repo 경로>   # ~/.claude/okf/home-project 포인터 기록 + 캡처 활성 제안
/study --trust                    # 홈 핸들러를 유저 스코프에 승인
```

홈은 **순수 지식 목적지**다 — 큐레이션된 지식만 담고 런타임 스테이징은 담지 않는다
(스테이징·trust는 유저 스코프 `~/.claude/okf/study`에 격리). 홈 repo의
`.okf-wiki.json`에 핸들러를 배선하고 **커밋**해 둔다:

```json
{ "study": { "capture": "review",
             "handlers": [{ "name": "kb-pr", "command": "scripts/okf-open-pr.sh" }] } }
```

> 비-git 폴더에서 `/okf-init`(인자 없이)을 돌리지 않는다 — `capture:"off"` 블록이
> 생겨 그 자리의 홈 폴백까지 꺼버리므로, 가드가 exit 3으로 차단하고 `--home`을
> 안내한다. 비-git은 `--home` 포인터만 쓴다.

**Project scope — 이 repo만의 파이프라인:**

```
/okf-init          # <repo>/.okf-wiki.json study 블록 + .okf-study/ 런타임(멱등)
/study --trust     # 이 repo의 .okf-study/trust에 승인
```

그 repo의 `study.handlers`가 홈보다 우선하고 런타임·trust도 repo 안에 격리된다.
블록을 만들지 않으면 자동으로 User(홈) 폴백을 따른다. 진단·회복은
`/okf-doctor`(스코프 해소 트레이스·건강)와 `study scan`이 담당한다.

정본 해소 규칙(캡처 4단·주입 3단·침묵 정책)은
[CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md)의 "홈 프로젝트 폴백" 절,
도입 상세는 [docs/adopting-study.md](docs/adopting-study.md) §7.

## 일상 명령

### 슬래시 커맨드 (플러그인)

```
/okf-init [--home <path>]                         # 번들·런타임 세팅(멱등) / 홈 포인터 마법사
/study    [<topic> | --type T | --scope home|project | --clear | --trust]
                                                  # 후보를 선택적으로 지식 개념으로 승격
/okf-doctor                                       # 현재 위치의 스코프 해소 결과·건강 진단
```

### `okf` CLI (엔진)

플러그인 사용자는 CLI를 직접 부를 일이 없다 — 스킬이 위임 실행한다. 아래는
CI·pre-commit·기여자용 직접 호출면이다.

```
okf validate <path> [--strict] [--format json]   # §9 컨포먼스 검사
okf index    <path> [--write]                     # §6 형식 index.md 재생성
okf graph    <path> --json [--linked-to P]        # 링크 그래프·역링크 조회
okf context  <path> [--max-chars N]               # 주입용 압축 인덱스
okf log      append <path> -m MSG                 # log.md 항목 추가(§7)
okf init     <dir>                                # §9 컨포먼트 최소 번들 스캐폴드
```

`validate` 종료코드: `0` 컨포먼트 / `1` 비컨포먼트 / `2` 실행 오류. `--format json`은
발견 1건당 `{"file","rule","level","msg"}` 객체를 출력한다.

## 데이터와 프라이버시

- **로컬 우선** — 지식은 당신의 git repo(`.okf/`)에 산다. 중앙 서버·SaaS·
  텔레메트리가 없다.
- **정본은 git** — 지식·이력의 정본은 번들 + `log.md` + git이다. 스테이징
  `study.db`·WAL·`trust`는 gitignored 소모성 런타임이라 커밋되지 않는다.
- **trust 게이트** — 핸들러(외부로 내보내는 코드) 실행은 로컬 승인이 필요하고,
  프레시 클론은 미승인에서 시작한다. 커밋된 설정만으로는 코드가 실행되지 않는다.
- **계층 분리** — 엔진은 Claude를 모르고, 플러그인은 특정 목적지를 모른다.
  "어디로 내보낼지"는 소비처가 자기 repo의 커밋 핸들러로 주입한다.
- **홈은 순수 목적지** — 홈 폴백을 써도 홈 repo에는 런타임 스테이징을 만들지
  않는다(유저 스코프에 격리).

## CI와 pre-commit으로 번들 검증 (소비 repo)

번들을 쓰는 repo는 같은 §9 검사를 배포면에서 걸 수 있다.

```yaml
# GitHub Actions
steps:
  - uses: actions/checkout@<SHA>
  - uses: pmmm114/okf-wiki-plugin/actions/validate@<v태그>
    with: { path: .okf, strict: true }
```

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pmmm114/okf-wiki-plugin
    rev: <v태그>
    hooks:
      - id: okf-validate
```

## 기여자 — 컨포먼스와 회귀 계약

이 repo는 벤치마크가 아니라 **컨포먼스 계약**으로 정확성을 담보한다. `okf
validate`는 OKF §9의 3규칙만 error로 보고하고 나머지는 warn으로 둔다. 판정
상수(예약 파일명·필수/권장 필드·strict 승격 집합)는 코드에 하드코딩되지 않고
[`rules/v0_1.json`](okf-core/src/okf_core/rules/v0_1.json)이 단일 원천이다.

CI의 `core` 잡이 아래를 게이트로 건다(모두 로컬에서 재현 가능):

| 게이트 | 하는 일 |
| --- | --- |
| 자기 번들 검증 | 이 repo의 `.okf/`를 `--strict`로 검증(도그푸딩) |
| 픽스처 스위트 | 픽스처별 `validate --format json` 출력을 `tests/expected/*.json` 스냅샷과 비교 — 스냅샷이 곧 회귀 계약 |
| 오라클 차동 | 벤더 업스트림 검증기와 파일별 §9 위반 집합을 비교(리포트 전용, 빌드 실패 아님) |
| vendor 동기화 | `okf-core/vendor/`가 업스트림과 **바이트 그대로**인지 확인(1바이트 수정도 차단) |
| 라이선스 검사 | 벤더 반입물 라이선스 고지 정합 |
| 플러그인 검증 | `claude plugin validate`(비-strict — plugin.json은 커밋 SHA 추적) |

로컬 재현:

```bash
uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q   # 엔진 테스트
uv run --no-project --with pytest python -m pytest plugins/okf/tests -q # 플러그인 테스트
uvx ruff check . && uvx ruff format --check .                          # 린트·포맷 (CI 0.15.8 핀)
uv run --with pyyaml python okf-core/scripts/run_fixture_suite.py       # 픽스처 스냅샷
```

이 repo를 클론해 엔진 CLI를 직접 쓰려면:

```bash
uv run --project okf-core okf validate .okf --strict   # uv (권장)
pip install ./okf-core && okf validate .okf --strict   # 또는 pip (repo 루트 설치도 동작)
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/adopting-study.md](docs/adopting-study.md) | `study` 도입(설치→핸들러 계약→trust→홈 폴백) |
| [plugins/okf/skills/okf/reference/CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md) | `.okf-wiki.json` 전체 스키마·스코프 해소 규칙 |
| [okf-core/vendor/spec/SPEC.md](okf-core/vendor/spec/SPEC.md) | OKF v0.1 스펙 원문(무수정 벤더링) |
| [docs/branching.md](docs/branching.md) | 브랜치·커밋·머지·벤더 반영 전략 |
| [docs/releasing.md](docs/releasing.md) | 배포·버전관리(마일스톤·SemVer·컷 절차) |
| [CLAUDE.md](CLAUDE.md) | 에이전트 작업 규칙(불변식·게이트) |
| [.okf/](.okf/) | 엔진 자기 번들(아키텍처·벤더 정책·컨포먼스 결정) |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 벤더 반입물 출처·라이선스 고지 |

## 라이선스

MIT ([LICENSE](LICENSE)). 벤더 반입물의 출처·라이선스는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 고지되어 있습니다.
