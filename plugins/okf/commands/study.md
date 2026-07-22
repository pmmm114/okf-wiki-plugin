---
description: 메모리 후보(inbox)를 선택적으로 지식 개념으로 승격하고 핸들러로 디스패치
argument-hint: "[<topic> | --type <type> | --scope home|project | --clear | --trust]"
---

study 승격 플로우를 실행한다. 인자: `$ARGUMENTS`(없으면 전체 후보 검토).

실행은 전부 플러그인 스크립트·`okf` CLI에 위임하고, **판정(선별·개념화·배치)만 직접**
한다. 경로: 스크립트 `${CLAUDE_PLUGIN_ROOT}/scripts`, 엔진 `${CLAUDE_PLUGIN_ROOT}/bin/okf`.

0. **대상 스코프 해소(`--scope`, #91)**: 이하 모든 단계의 `<project>`(스크립트의
   project 인자·번들 경로의 기준)를 정한다.
   - 인자 없음(기본): `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/okf_home.py" status .`의
     `capture.target`을 쓴다 — 현재 위치의 해소 결과(프로젝트 또는 홈). `target`이
     null이고 `invalid`가 있으면 그 사유를 사용자에게 보이고(가시적 진단) 종료.
     null이며 무효 사유도 없으면 현재 repo(`.`)를 그대로 쓴다(수동 승격 경로).
   - `--scope home`: 같은 status 출력의 `home`을 `<project>`로 강제 — repo 안에서도
     홈(KB) 파이프라인으로 명시 승격·드레인한다. `home`이 null이면 `invalid` 사유를
     보이고 종료.
   - `--scope project`: 현재 repo(`.`)로 강제(현행 기본과 동일).

1. **인자 분기**
   - `--trust`: `study_trust.py status <project>`로 해석된 handler command를 사용자에게
     보이고, 승인받으면 `study_trust.py approve <project>` 실행 후 종료.
   - `--clear`: `study.py clear <project>`로 현재 후보를 전부 discard하고 종료.
   - 그 외(`<topic>`·`--type X`·없음): 아래 2단계부터 승격 진행.

2. **후보 로드**: `study.py list <project>` → 후보 JSON. 비었으면 안내 후 종료
   (`--scope home`이면 홈 inbox의 후보 — 다른 위치에서 캡처된 것 포함).

3. **선별(판정)**: 장기 지식(스키마·명령·결정·규약)만 고른다. 상호작용 취향·일회성은
   제외. `<topic>`/`--type` 인자가 있으면 스니펫·출처로 그 주제/타입 후보만 한정.
   전부가 아니라 **사용자가 고른 부분집합**을 승격한다(모호하면 물어본다).

4. **개념화(판정, 후보별)**: okf 스킬 §2·§3대로 루트 index를 먼저 읽어 배치를 정하고
   개념 파일을 작성한다 — `type` 필수, `description` 1문장, 백링크 ≥1, 답-우선 본문,
   **주제 하위디렉토리** 배치(카테고리 = `type` + 주제 디렉토리).

5. **로그·색인·검증**: `okf log append <dir> -m "<요약> (captured <후보 date>)" --kind Promotion`
   → `okf index <bundle> --write` → `okf validate <bundle> --strict`(error·warn 0까지).
   - **provenance 이관(#114 U5 · #132)**: 후보의 캡처 일자(2단계 `list`의 `date`)를
     로그 메시지에 새겨 **비-git 스테이징의 적립 시점을 git-추적 `log.md`에 남긴다** —
     스테이징이 사라져도 버저닝은 git(홈)에 남는다. `list`의 `recurrence`(재등장 수)가
     크면 반복 학습된 개념이라는 신호이니 요약에 함께 반영할 수 있다. 더 세밀한 순서·
     시각 이력은 `study.py log <project>`(이벤트 저널: capture/promote/discard).
   - **갱신(supersedes, #132)**: 승격 개념이 기존 개념의 **갱신**이면, 드레인(6단계)
     후 `study.py` 없이 판단만 남긴다 — 새 후보는 이미 승격됐고, 기존 개념 파일은
     스킬 §3대로 편집·교체한다(원장은 자식 줄-해시로 재부상을 자동 차단).

6. **드레인**: 검증 통과 개념마다
   `study.py resolve <project> --id <id> --status promoted --ref <경로>`.
   버릴 후보는 `--status discarded`.
   - **교차 승격 규약(#91 §4)**: 프로젝트 inbox의 후보를 홈 번들로 승격했다면
     `resolve`는 **후보가 잡힌 스코프**(그 프로젝트)에 대해 실행하고 `--ref`에
     홈 개념 경로를 준다 — 기록은 원 스코프 원장이 정본, 유효 홈이 있으면 홈
     원장에도 자동 write-through된다(시간축 재큐 방지).

7. **디스패치**: 승격 개념마다 `study.py dispatch <project> --source manual
   --concept-path <경로> --concept-type <type> --concept-topic <topic>`.
   결과 `note`에 "핸들러 미승인"이 오면
   (가시적 저하) 사용자에게 `/study --trust`를 안내한다 — 개념은 이미 로컬 번들에
   승격됐고 핸들러만 보류된 상태다.

8. **요약**: 승격/폐기/디스패치 결과를 알리고, 오래된 후보가 많으면 `/study --clear`를
   제안한다.
