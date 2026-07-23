# okf-wiki-plugin

**오픈소스 · 로컬 우선 · 사용자 소유 · Markdown 네이티브 지식**

[OKF(Open Knowledge Format) v0.1](okf-core/vendor/spec/SPEC.md) 지식 번들을 만들고
검증해서 Claude Code 세션에 넣어 주는 도구 모음입니다. 에이전트가 세션마다 맨땅에서
다시 시작하지 않도록, 알아낸 것을 `cat` 한 번으로 읽히는 markdown에 적어 **git으로
버전 관리**합니다. 그러면 다음 세션이나 다른 에이전트가 그 지식을 그대로 이어받습니다.

> **비공식 고지** — 이 프로젝트는 Google과 무관한 비공식 도구입니다. OKF 스펙 원문은
> Apache-2.0 라이선스 그대로(수정 없이) 벤더링했으며([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)),
> 코드는 MIT입니다([LICENSE](LICENSE)).

## 목차

- [구성](#구성)
- [왜 okf-wiki-plugin인가](#왜-okf-wiki-plugin인가)
- [Getting Started](#getting-started)
- [동작 방식](#동작-방식)
- [단기 기억과 장기 기억 (`study`)](#단기-기억과-장기-기억-study)
- [신뢰 경계와 스코프](#신뢰-경계와-스코프)
- [일상 명령](#일상-명령)
- [데이터와 프라이버시](#데이터와-프라이버시)
- [문서](#문서)
- [라이선스](#라이선스)

## 구성

| 구성 | 경로 | 역할 |
| --- | --- | --- |
| 엔진 | `okf-core/` | 파서, 컨포먼스 검사기, index/graph/context 생성기, 그리고 `okf` CLI |
| 플러그인 | `plugins/okf/` | Claude Code 플러그인 — 스킬 + 세션 컨텍스트 주입 훅 + `study` 승격 |
| CI 소비면 | `actions/validate/` | 소비 repo용 composite action |
| pre-commit | `.pre-commit-hooks.yaml` | 소비 repo용 pre-commit 훅 정의 |

## 왜 okf-wiki-plugin인가

| 겪는 문제 | okf-wiki-plugin이 주는 것 |
| --- | --- |
| 세션이 끝나면 맥락이 사라지고, 다음 에이전트는 처음부터 다시 시작한다 | 알아낸 것을 `.okf/` git 번들 안의 **개념**으로 남긴다. 다음 세션이나 다른 에이전트가 그대로 이어받는다 |
| 답의 근거가 코드와 대화, 문서에 흩어져 있다 | 개념은 설명이 아니라 **답**을 담는다(스키마 컬럼, 조인 키, 명령어, 수치). 근거는 백링크와 인용으로 잇는다 |
| 지식이 특정 도구나 SaaS 포맷에 갇힌다 | `cat`으로 읽히는 **markdown + YAML frontmatter**를 git으로 배포한다. SDK도 스키마 레지스트리도 중앙 권위도 필요 없다(OKF v0.1) |
| 메모리는 일시적이고, 취향과 지식이 뒤섞인다 | `study`가 **오래 남길 지식만** 골라 개념으로 승격한다. 스테이징에 쌓인 후보는 드레인되면 사라진다 |
| 커밋된 설정 파일이 임의 코드를 실행할 수 있다 | 핸들러 실행은 **로컬 trust 승인**을 거쳐야 한다. 새로 클론하면 늘 미승인 상태에서 시작한다 |
| 형식이 흔들리면 소비 쪽이 조용히 깨진다 | `okf validate`의 **컨포먼스 검사**에, CI의 픽스처 스냅샷과 오라클 차동이 더해져 회귀를 계약으로 막는다 |

## Getting Started

**목표** — 첫 개념 하나를 남기고, 그것이 다음 세션에 주입되는 것까지 확인합니다.
5단계, 약 5분이면 됩니다.

**필요한 것** — Claude Code, 그리고 지식을 담아 둘 git repo 하나. 엔진은 플러그인에
함께 들어 있어서 따로 설치할 것이 없습니다.

### 1. 플러그인 설치

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf@okf-wiki-plugin
```

### 2. 번들 초기화

지식을 담을 repo에서 실행합니다. 여러 번 실행해도 안전하고(멱등), 이미 있는 파일은
건드리지 않습니다.

```
/okf-init
```

| 산출물 | 역할 | git |
| --- | --- | --- |
| `.okf/` | 지식 번들(개념·index·log) — 없을 때만 스캐폴드 | 커밋 |
| `.okf-wiki.json` | 프로젝트 설정(주입 · `study`) — 있으면 보강 | 커밋 |
| `.okf-study/.gitignore` | 런타임 무시 규칙(`*` + `!.gitignore`) | 커밋 |
| `.okf-study/study.db`(+WAL) | 후보 큐·원장·이벤트 저널(SQLite) | gitignored |
| `.okf-study/trust` | 핸들러 로컬 승인 해시 | gitignored |

> git repo가 아닌 곳이라면 가드가 막습니다(exit 3). 그럴 때는 대신 vault repo를
> 목적지로 지정합니다 — 방법은 [study 도입 가이드](docs/adopting-study.md)에 있습니다.

### 3. 세션 주입 켜기

repo 루트의 `.okf-wiki.json`에 주입 설정이 있는지 확인합니다.

```json
{
  "bundlePath": ".okf",
  "context": { "maxChars": 8000 },
  "inject": true
}
```

이 파일이 있어야 SessionStart 훅이 번들 요약을 세션에 넣습니다. 없으면 훅은 아무것도
하지 않습니다(fail-open — 지식 번들이 없는 repo의 평범한 작업에는 끼어들지 않습니다).
전체 스키마는 [CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md)에 있습니다.

### 4. 첫 개념 작성

개념은 설명이 아니라 **답**을 담습니다. 스키마 컬럼이나 조인 키, 명령어, 수치처럼
다음 세션이 곧바로 쓸 수 있는 것 말입니다. 한 파일에 한 개념씩, **주제별
하위디렉토리**로 묶습니다.

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

규칙은 세 가지뿐입니다 — frontmatter `type` **필수**, `description`은 **한 문장**,
그리고 주제별 하위디렉토리에 배치. Claude에게 "이 내용을 개념으로 적재해줘"라고
맡기면 okf 스킬이 이 규칙대로 쓰고 배치한 뒤 index까지 다시 만들어 줍니다.

### 5. 검증하고 주입 확인

Claude에게 "번들 검증해줘"라고 하면 스킬이 검사기를 돌립니다. 통과하면 이렇게
나옵니다.

```
컨포먼트: error 0건, warn 0건
```

이어서 "주입될 컨텍스트 보여줘"라고 하면 압축 인덱스를 그대로 보여 줍니다.

```
<okf-context>
deploy/release.md [concept] — 릴리스 컷 명령과 승인 게이트.
</okf-context>
```

**이 블록이 다음 세션이 시작될 때 그대로 주입되는 텍스트입니다.** 새 세션을 열어
확인해 보세요. 여기까지 왔으면 루프가 닫힌 것입니다. 이제 `.okf/`를 커밋하면 팀과
다음 세션이 이 지식을 그대로 이어받습니다.

지금 이 위치에서 캡처와 주입이 **어디로 가는지** 궁금하면 `/okf-doctor`로 확인합니다.

### 다음 단계

- 비-git 폴더에서 쓰거나 vault repo로 적립하기 → [study 도입 가이드](docs/adopting-study.md)
- 메모리를 후보로 쌓고 골라 승격하기 → [단기 기억과 장기 기억](#단기-기억과-장기-기억-study)
- 소비 repo에 CI·pre-commit 검사 걸기 → [소비 repo 가이드](docs/consuming.md)

## 동작 방식

엔진은 **파일 하나를 딱 한 번만 파싱한다.** `parser.parse`가 만든 `ParsedDoc`을
validate·index·graph·context가 돌려쓴다(다시 파싱하지 않도록 호출 카운터 테스트가
막는다). 세션에 주입하는 일은 플러그인 쪽이 맡는다. **엔진(`okf-core/`)은 Claude를
모른다.**

```
파일(.okf/*.md)
   │  parser.parse   (파일당 1회 → ParsedDoc 재사용)
   ▼
ParsedDoc ──┬─▶ validate   컨포먼스 검사 (3규칙 error / 나머지 warn)
            ├─▶ index      index.md 재생성
            ├─▶ graph      링크·역링크 그래프
            └─▶ context    주입용 압축 인덱스
                              │
   Claude Code SessionStart 훅 │  (플러그인 계층 — 엔진 밖)
                              ▼
                  <okf-context> 세션 자동 주입
```

- **파싱은 한 번, 판정은 재사용** — validate/index/graph/context가 같은 `ParsedDoc`을 공유한다.
- **컨포먼스 규칙 중 셋만 error** — 판정 기준은 OKF 스펙의 [컨포먼스 규칙](okf-core/vendor/spec/SPEC.md#9-conformance)이다. 그중 세 규칙만 error이고, 스펙이 거부까지 요구하지 않는 나머지는 warn이다(`--strict`는 권장 필드 위반을 error로 올린다). 판정 상수는 코드가 아니라 [`rules/v0_1.json`](okf-core/src/okf_core/rules/v0_1.json)에 모여 있다.
- **"index가 쓰는 파일 == validate를 통과한 파일"** — 이 불변식 덕분에 색인 로직을 바꾸면 검증 판정도 함께 움직인다.
- **주입은 압축 컨텍스트로** — 번들 전체가 아니라 `context`가 만든 압축 인덱스를 `<okf-context>` 블록에 담아 세션에 넣는다(글자 수 상한은 `.okf-wiki.json`에서 조정).
- **fail-open** — `.okf-wiki.json`이 없는 repo에서는 훅이 아무 일도 하지 않는다.

## 단기 기억과 장기 기억 (`study`)

`study`는 Claude Code의 **메모리**(일시적)를 감지해, 이 repo의 **OKF 지식
개념**(영구)으로 골라 승격하는 기능이다. 승격된 개념은 소비처가 주입한 핸들러로
흘려보낸다. 플러그인은 목적지를 모른다 — "어디로 보낼지"는 소비처가 정한다.

| 계층 | 저장소 | 수명 |
| --- | --- | --- |
| 단기 — 메모리 | Claude Code 메모리(일시) | 세션이 끝나면 사라짐 |
| 캡처 스테이징 | `.okf-study/study.db`(SQLite, gitignored) + `trust` | 드레인되면 소모 |
| 장기 — 지식 개념 | `.okf/` git 번들 + `log.md` | 영구(git으로 버전 관리) |

지식과 이력의 정본은 언제나 **번들 + `log.md` + git**이다. 스테이징은 소모성
상태라 드레인되면 사라지고, 승격할 때 캡처 일자와 재등장 횟수를 `log.md`에 남겨
git에 기록한다.

**자동 점수도 자동 병합도 없다** — 승격은 사람과 모델이 판단한다. 사용자가 만지는
손잡이는 캡처 사다리 `off ⊂ review ⊂ auto` 하나뿐이다. 자세한 승격 게이트(선별
기준, 재등장 카운터, 근사중복 자문, 드레인)와 도입 절차는
[study 도입 가이드](docs/adopting-study.md)에 있다.

## 신뢰 경계와 스코프

**핸들러 trust 경계** — 승격된 개념을 소비처 핸들러로 넘겨 실행하려면 **로컬 승인**을
받아야 한다. 커밋되는 `.okf-wiki.json`만으로는 코드가 실행되지 않게 막는 게이트다.
승인(`/study --trust`)은 핸들러 셋의 내용 해시로 `.okf-study/trust`(gitignore)에
저장되므로, 새로 클론하면 늘 미승인에서 시작한다. 스크립트나 설정이 바뀌면 다시
승인해야 한다.

**적재 스코프** — `study`가 지식을 어디에 쌓을지는 두 스코프로 갈린다. 슬로건은
간단하다 — **"자기 파이프라인이 있으면 거기로(Project), 없으면 vault로(User)."**
그 repo에 `study` 블록이 있으면 Project 스코프로 repo 안에(런타임·trust 포함) 쌓고,
없으면 User 스코프로 vault repo에 적립한다. 비-git 폴더에서도 vault로 흘려보낼 수 있다.

정확한 해소 규칙(캡처 4단, 주입 3단, 침묵 정책)은
[CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md)가, vault 폴백 도입 절차는
study 도입 가이드의 [Vault 프로젝트 폴백 절](docs/adopting-study.md#7-vault-프로젝트-폴백--repo-밖에서도-적립-epic-91114)이 정본이다.

## 일상 명령

플러그인은 슬래시 커맨드로 씁니다.

```
/okf-init [--vault <path>]                         # 번들·런타임 세팅(멱등) / vault 포인터 마법사
/study    [<topic> | --type T | --scope vault|project | --clear | --trust]
                                                  # 후보를 골라 지식 개념으로 승격
/okf-doctor                                       # 지금 위치의 스코프 해소 결과·건강 진단
```

엔진 `okf` CLI는 스킬이 대신 실행하므로 직접 부를 일이 거의 없습니다. CI·pre-commit·
기여자용 직접 호출면은 [CONTRIBUTING.md](CONTRIBUTING.md)에 정리해 두었습니다.

## 데이터와 프라이버시

- **로컬 우선** — 지식은 당신의 git repo(`.okf/`) 안에 있다. 중앙 서버도, SaaS도, 텔레메트리도 없다.
- **정본은 git** — 지식과 이력의 정본은 번들과 `log.md`, 그리고 git이다. 스테이징(`study.db`, WAL, `trust`)은 gitignore된 소모성 런타임이라 커밋되지 않는다.
- **trust 게이트** — 외부로 내보내는 핸들러 코드는 로컬 승인을 받아야 실행된다. 새로 클론하면 미승인 상태라, 커밋된 설정만으로는 코드가 돌지 않는다.
- **계층 분리** — 엔진은 Claude를 모르고, 플러그인은 특정 목적지를 모른다. "어디로 내보낼지"는 소비처가 자기 repo에 커밋한 핸들러로 정한다.
- **vault는 순수 목적지** — vault 폴백을 써도 vault repo에는 런타임 스테이징을 만들지 않는다. 스테이징은 유저 스코프에 따로 격리된다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [study 도입 가이드](docs/adopting-study.md) | 설치부터 핸들러 계약, trust, vault 폴백까지 |
| [CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md) | `.okf-wiki.json` 전체 스키마와 스코프 해소 규칙 |
| [소비 repo 가이드](docs/consuming.md) | CI·pre-commit으로 번들 검증하기 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 컨포먼스·회귀 계약, 엔진 CLI, 로컬 재현 |
| [OKF v0.1 스펙](okf-core/vendor/spec/SPEC.md) | 스펙 원문(수정 없이 벤더링) |
| [docs/branching.md](docs/branching.md) | 브랜치·커밋·머지·벤더 반영 전략 |
| [docs/releasing.md](docs/releasing.md) | 배포·버전 관리(스코프 마일스톤, 커밋 도출 SemVer, 컷 절차) |
| [CLAUDE.md](CLAUDE.md) | 에이전트 작업 규칙(불변식·게이트) |
| [.okf/](.okf/index.md) | 엔진 자기 번들(아키텍처, 벤더 정책, 컨포먼스 결정) |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 벤더 반입물 출처·라이선스 고지 |

## 라이선스

MIT([LICENSE](LICENSE)). 벤더 반입물의 출처와 라이선스는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 고지되어 있습니다.
