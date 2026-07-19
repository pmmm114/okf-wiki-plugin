---
description: 메모리 후보(inbox)를 선택적으로 지식 개념으로 승격하고 핸들러로 디스패치
argument-hint: "[<topic> | --type <type> | --clear | --trust]"
---

study 승격 플로우를 실행한다. 인자: `$ARGUMENTS`(없으면 전체 후보 검토).

실행은 전부 플러그인 스크립트·`okf` CLI에 위임하고, **판정(선별·개념화·배치)만 직접**
한다. 경로: 스크립트 `${CLAUDE_PLUGIN_ROOT}/scripts`, 엔진 `${CLAUDE_PLUGIN_ROOT}/bin/okf`.

1. **인자 분기**
   - `--trust`: `study_trust.py status .`로 해석된 handler command를 사용자에게 보이고,
     승인받으면 `study_trust.py approve .` 실행 후 종료.
   - `--clear`: `study.py clear .`로 현재 후보를 전부 discard하고 종료.
   - 그 외(`<topic>`·`--type X`·없음): 아래 2단계부터 승격 진행.

2. **후보 로드**: `study.py list .` → 후보 JSON. 비었으면 안내 후 종료.

3. **선별(판정)**: 장기 지식(스키마·명령·결정·규약)만 고른다. 상호작용 취향·일회성은
   제외. `<topic>`/`--type` 인자가 있으면 스니펫·출처로 그 주제/타입 후보만 한정.
   전부가 아니라 **사용자가 고른 부분집합**을 승격한다(모호하면 물어본다).

4. **개념화(판정, 후보별)**: okf 스킬 §2·§3대로 루트 index를 먼저 읽어 배치를 정하고
   개념 파일을 작성한다 — `type` 필수, `description` 1문장, 백링크 ≥1, 답-우선 본문,
   **주제 하위디렉토리** 배치(카테고리 = `type` + 주제 디렉토리).

5. **로그·색인·검증**: `okf log append <dir> -m "<요약>" --kind Promotion` →
   `okf index <bundle> --write` → `okf validate <bundle> --strict`(error·warn 0까지).

6. **드레인**: 검증 통과 개념마다
   `study.py resolve . --id <id> --status promoted --ref <경로>`.
   버릴 후보는 `--status discarded`.

7. **디스패치**: 승격 개념마다 `study.py dispatch . --source manual --concept-path <경로>
   --concept-type <type> --concept-topic <topic>`. 결과 `note`에 "핸들러 미승인"이 오면
   (가시적 저하) 사용자에게 `/study --trust`를 안내한다 — 개념은 이미 로컬 번들에
   승격됐고 핸들러만 보류된 상태다.

8. **요약**: 승격/폐기/디스패치 결과를 알리고, 오래된 후보가 많으면 `/study --clear`를
   제안한다.
