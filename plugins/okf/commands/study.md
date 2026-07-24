---
description: 메모리 후보(inbox)를 선택적으로 지식 개념으로 승격하고 핸들러로 디스패치
argument-hint: "[<topic> | --type <type> | --layer <layer> | --scope vault|project | --clear | --trust]"
---

study 승격 플로우를 실행한다. 인자: `$ARGUMENTS`(없으면 전체 후보 검토).

실행은 전부 플러그인 스크립트·`okf` CLI에 위임하고, **판정(선별·개념화·배치)만 직접**
한다. 경로: 스크립트 `${CLAUDE_PLUGIN_ROOT}/scripts/study`, 엔진 `${CLAUDE_PLUGIN_ROOT}/bin/okf`.

0. **대상 스코프 해소(`--scope`, #91)**: 이하 모든 단계의 `<project>`(스크립트의
   project 인자·번들 경로의 기준)를 정한다.
   - 인자 없음(기본): `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/study/study_scope.py" status .`의
     `capture.target`을 쓴다 — 현재 위치의 해소 결과(프로젝트 또는 vault). `target`이
     null이고 `invalid`가 있으면 그 사유를 사용자에게 보이고(가시적 진단) 종료.
     null이며 무효 사유도 없으면 현재 repo(`.`)를 그대로 쓴다(수동 승격 경로).
   - `--scope vault`: 같은 status 출력의 `vault`을 `<project>`로 강제 — repo 안에서도
     vault(KB) 파이프라인으로 명시 승격·드레인한다. `vault`이 null이면 `invalid` 사유를
     보이고 종료.
   - `--scope project`: 현재 repo(`.`)로 강제(현행 기본과 동일).

0a. **신선도 갱신(URL vault만, #153)**: 승격은 관리형 clone의 워킹트리에 쓴다 — 그 전에
   base를 최신화한다. `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/core/okf_remote.py" refresh`를
   실행한다(**URL vault가 아니면 자동 무동작** — 로컬 경로 vault·프로젝트 스코프는 그냥 넘어간다).
   - `refreshed: true` → clean-gate 통과 + ff-only로 최신 base. 계속.
   - `reason: "dirty"`(`warning` 있음) → clone에 미커밋 승격 잔재가 있다. `warning`을 보이고,
     이전 승격을 **디스패치(커밋)하거나 폐기**해 정리한 뒤 재시도하도록 안내한다(강제 stash·머지
     금지 — clone을 wedge시킨다, U3-2).
   - `reason: "diverged" | "fetch 실패" | "offline env"` → `warning`을 보이고 **캐시로 계속**한다
     (승격은 진행되나 stale base 위일 수 있으니, 핸들러 PR 단계에서 rebase로 정리).

1. **인자 분기**
   - `--trust`: `study_trust.py status <project>`로 해석된 handler command를 사용자에게
     보이고, 승인받으면 `study_trust.py approve <project>` 실행 후 종료.
   - `--clear`: `study.py clear <project>`로 현재 후보를 전부 discard하고 종료.
   - 그 외(`<topic>`·`--type X`·없음): 아래 2단계부터 승격 진행.

2. **후보 로드**: `study.py list <project>` → 후보 JSON. 비었으면 안내 후 종료
   (`--scope vault`이면 vault inbox의 후보 — 다른 위치에서 캡처된 것 포함).

3. **선별(판정)**: 장기 지식(스키마·명령·결정·규약)만 고른다. 상호작용 취향·일회성은
   제외. `<topic>`/`--type`/`--layer` 인자가 있으면 그 주제/타입/인식층 후보만 한정한다
   (`--layer`는 4단계 판정 결과로 거른다 — 스니펫을 보고 그 층일 후보만).
   전부가 아니라 **사용자가 고른 부분집합**을 승격한다(모호하면 물어본다).
   - **근사중복 자문(#133)**: `study.py near <project>`로 재서술된 근사중복 후보를
     확인할 수 있다(SimHash 해밍거리 — **자문 전용**, 자동병합 없음). 같은 지식의
     변주로 판단되면 하나만 승격하고 나머지는 discard한다. 정확 판정은 사람·모델의 몫.

4. **개념화(판정, 후보별)**: okf 스킬 §2·§3·§6대로 루트 index를 먼저 읽어 배치를 정하고
   개념 파일을 작성한다 — `type` 필수, `description` 1문장, 백링크 ≥1, 답-우선 본문,
   **주제 하위디렉토리** 배치.
   - **인식층 판정(필수)**: 후보의 인식 고도를 판정해 `layer`(정보/지식/지혜)를 부여한다
     — 카테고리 = `type` + 주제 디렉토리 + `layer`. 어휘·휴리스틱은 스킬 `reference/LAYERS.md`.
   - **존재 대조(멱등)**: 층을 정했으면 `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py"
     "${CLAUDE_PLUGIN_ROOT}/scripts/study/study.py" near-bundle <bundle> --snippet <후보>
     --layer <layer>`로 같은 층 근사중복을 확인한다(SimHash 자문). 같은 정보면 새로 만들지
     않고 기존 개념을 재확인·갱신(`supersedes`)으로 흡수한다(exact 재부상 차단은 원장이 이미 함).
   - **접지(교차층 맵핑)**: 상위 층(지식·지혜)은 근거 하위 개념을 `derived_from`으로 잇는다 —
     `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/core/okf_layers.py"
     <bundle> --candidates-for <layer> --json`로 후보를 질의한다(정초 엄격 하향: 지식→정보,
     지혜→지식·정보). 근거 사실이 후보에 함께 있으면 정보를 먼저 승격하고, 번들에 없으면
     `derived_from`을 남겨 접지 린트가 "미작성 지식 신호"로 잡게 둔다(출처·근거 날조 금지).

5. **로그·색인·검증**: `okf log append <dir> -m "<요약> (layer <layer>, captured <후보 date>)" --kind Promotion`
   → `okf index <bundle> --write` → `okf validate <bundle> --strict`(error·warn 0까지)
   → `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/core/okf_layers.py" <bundle>`
   **접지 린트**(정초 순서·미접지·깨진 `derived_from` 자문 warn — 스펙 §9 판정 불변). 뜬
   신호는 4단계 접지로 되돌아가 해소하거나 근거 개념을 마저 쓴다.
   - **provenance 이관(#114 U5 · #132)**: 후보의 캡처 일자(2단계 `list`의 `date`)를
     로그 메시지에 새겨 **비-git 스테이징의 적립 시점을 git-추적 `log.md`에 남긴다** —
     스테이징이 사라져도 버저닝은 git(vault)에 남는다. `list`의 `recurrence`(재등장 수)가
     크면 반복 학습된 개념이라는 신호이니 요약에 함께 반영할 수 있다. 더 세밀한 순서·
     시각 이력은 `study.py log <project>`(이벤트 저널: capture/promote/discard).
   - **갱신(supersedes, #132)**: 승격 개념이 기존 개념의 **갱신**이면, 드레인(6단계)
     후 `study.py` 없이 판단만 남긴다 — 새 후보는 이미 승격됐고, 기존 개념 파일은
     스킬 §3대로 편집·교체한다(원장은 자식 줄-해시로 재부상을 자동 차단).

6. **드레인**: 검증 통과 개념마다
   `study.py resolve <project> --id <id> --status promoted --ref <경로> --layer <layer>`.
   버릴 후보는 `--status discarded`. `--layer`로 4단계 판정 인식층을 저널·후보에
   provenance로 새긴다(후보 드레인 후에도 `study.py log`에 층이 남는다).
   - **교차 승격 규약(#91 §4)**: 프로젝트 inbox의 후보를 vault 번들로 승격했다면
     `resolve`는 **후보가 잡힌 스코프**(그 프로젝트)에 대해 실행하고 `--ref`에
     vault 개념 경로를 준다 — 기록은 원 스코프 원장이 정본, 유효 vault가 있으면 vault
     원장에도 자동 write-through된다(시간축 재큐 방지).

7. **디스패치**: 승격 개념마다 `study.py dispatch <project> --source manual
   --concept-path <경로> --concept-type <type> --concept-topic <topic> --concept-layer <layer>`.
   결과 `note`에 "핸들러 미승인"이 오면
   (가시적 저하) 사용자에게 `/study --trust`를 안내한다 — 개념은 이미 로컬 번들에
   승격됐고 핸들러만 보류된 상태다.

8. **요약**: 승격/폐기/디스패치 결과를 알리고, 오래된 후보가 많으면 `/study --clear`를
   제안한다.
