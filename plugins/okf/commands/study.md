---
description: 메모리 후보(inbox)를 선택적으로 지식 개념으로 승격하고 핸들러로 디스패치
argument-hint: "[<topic> | --type <type> | --layer <layer> | --scope vault|project | --clear | --trust]"
---

study 승격 플로우를 실행한다. 인자: `$ARGUMENTS`(없으면 전체 후보 검토).

실행은 전부 플러그인 스크립트·`okf` CLI에 위임하고, **판정(선별·개념화·배치)만 직접** 한다. 경로: 스크립트 `${CLAUDE_PLUGIN_ROOT}/scripts/<도메인>`(각 단계에 명시), 엔진 `${CLAUDE_PLUGIN_ROOT}/bin/okf`.

0. **대상 스코프 해소(`--scope`, #91)**: 이하 모든 단계의 `<project>`(스크립트의 project 인자·번들 경로의 기준)를 정한다.
   - 인자 없음(기본): `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/capture/study_scope.py" status .`의 `capture.target`을 쓴다 — 현재 위치의 해소 결과(프로젝트 또는 vault). `target`이 null이고 `invalid`가 있으면 그 사유를 사용자에게 보이고(가시적 진단) 종료. null이며 무효 사유도 없으면 현재 repo(`.`)를 그대로 쓴다(수동 승격 경로).
   - `--scope vault`: 같은 status 출력의 `vault`을 `<project>`로 강제 — repo 안에서도 vault(KB) 파이프라인으로 명시 승격·드레인한다. `vault`이 null이면 `invalid` 사유를 보이고 종료.
   - `--scope project`: 현재 repo(`.`)로 강제(현행 기본과 동일).

0a. **신선도 갱신(URL vault만, #153)**: 승격은 관리형 clone의 워킹트리에 쓴다 — 그 전에 base를 최신화한다. `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/vault/okf_remote.py" refresh`를 실행한다.

   **`code`로 분기한다**(한국어 `reason`·`warning` 매칭 금지 — 사람용 표시일 뿐이고 문구가 바뀌면 조용히 깨진다). 코드 집합은 `okf_remote.REFRESH_REASONS`가 단일원천이고 각 코드에 실행 가능한 복구 지시가 붙어 있다.
   - `ok` → 최신 base다. 계속. `discarded`가 있으면 **원격에 이미 담긴** 잔재를 폐기해 정체를 푼 것이므로(#216 V1) 그 경로 목록을 한 줄로 보인다.
   - `unsealed_residue` → 원격 어디에도 없는 잔재가 ff를 막고 있다. **폐기하지 않았다**(지식 유실 금지). `warning`을 **그대로** 보이고 그 지시대로 처리한 뒤 재시도하도록 안내한다 — `warning`은 배선 여부로 갈린다(배선됨 → 디스패치로 반영 / 미배선 → `/okf-init --vault`로 배선). 여기서 임의로 "디스패치하라"를 덧붙이지 않는다(반영 경로가 없는 vault에는 실행 불가능한 지시다). 강제 stash·머지 금지 — clone을 wedge시킨다(U3-2).
   - `ff_retry_failed` → 봉인 잔재는 **폐기했는데도** ff가 여전히 막힌 것이다(#298). 위 분기와 사실관계가 반대이므로 안내를 섞지 않는다 — `discarded` 경로 목록을 한 줄로 보이고(원격에 담긴 것이라 회수 가능), 남은 잔재에 대해 `warning`을 그대로 보인 뒤 **캐시로 계속**한다.
   - `detached` · `no_upstream` → 관리형 clone의 git 상태가 비정상이라 ff 대상을 정할 수 없다. **잔재 회수에 들어가지 않았으므로 아무것도 폐기되지 않았다.** `REFRESH_REASONS`의 복구 지시를 보이고 **캐시로 계속**한다.
   - `diverged` · `fetch_failed` · `offline` · `locked` → `warning`을 보이고 **캐시로 계속**한다 (승격은 진행되나 stale base 위일 수 있으니, 핸들러 PR 단계에서 rebase로 정리).
   - `clone_missing` → 처방이 다르다. 캐시로 계속하지 말고 `/okf-init --vault`로 관리형 clone을 만들도록 안내하고 종료한다 — 승격할 워킹트리 자체가 없다.
   - `not_url` → URL vault가 아니다(로컬 경로 vault·프로젝트 스코프). **무동작으로 그냥 넘어간다.**
   - `bad_transport` → 포인터 transport가 미지원이다. `REFRESH_REASONS`의 복구 지시를 보이고 종료한다.

1. **인자 분기**
   - `--trust`: `study_trust.py status <project>`로 해석된 handler command를 사용자에게 보이고, 승인받으면 `study_trust.py approve <project>` 실행 후 종료.
   - `--clear`: `study.py clear <project>`로 현재 후보를 전부 discard하고 종료 — 전량 원장 기록이므로, 구조 노이즈가 섞여 있으면 3단계의 prune을 먼저 실행한다.
   - 그 외(`<topic>`·`--type X`·없음): 아래 2단계부터 승격 진행.

2. **후보 로드(헤더 먼저)**: `study.py list <project> --by-file --headers` → 그룹 헤더 JSON (`[{source, count}]` — 스니펫 본문 없음, 순서는 전량 뷰와 동일하게 최신 캡처 그룹 우선). 비었으면 안내 후 종료(`--scope vault`이면 vault inbox의 후보 — 다른 위치에서 캡처된 것 포함). **전량 뷰(`--by-file` 무필터)를 먼저 부르지 않는다** — 인박스가 수천 후보로 자라면 컨텍스트에 물리적으로 들어가지 않아 절차 자체가 실행 불가능해진다(#383 실측 50배). 그룹은 **캡처 스냅샷의 누적**이다 — 파일의 현재 상태가 아니므로 같은 줄의 편집 전 후보가 공존할 수 있다(판정은 스니펫 기준).

3. **선별(판정)**: **파일 그룹 단위로** 검토한다 — 메모리 관례상 1파일 = 1사실이라 리뷰 결정 단위 = 개념 단위 = 파일이다(#257). 2단계 헤더로 훑고 **검토할 그룹만** `study.py list <project> --by-file --source <경로>`로 펼친다(헤더의 `source` 값 그대로 — 저장값 정확 일치이고 무매칭이면 현존 source 목록과 함께 실패한다. 출력은 그 그룹 하나짜리 `[{source, count, candidates: [...]}]`이고 후보 필드는 평탄 `list`와 동일). 큰 그룹(색인 MEMORY.md류·수십 블록 파일)을 접어 두는 것은 색인 특례가 아니라 **일반 규칙**이다(비색인 대형 파일 실존). 장기 지식(스키마·명령· 결정·규약)만 고른다. 상호작용 취향·일회성은 제외. `<topic>`/`--type`/`--layer` 인자가 있으면 그 주제/타입/인식층으로 좁힌다 — 셋 다 **후보 필드가 아니라 판정 결과로 거르는 필터**다. 후보에는 축 값이 없다 (`id`·`date`·`snippet`·`source`·`recurrence`뿐) — 스니펫을 읽고 그 주제/타입/층일 것으로 판정되는 후보만 남긴다. 전부가 아니라 **사용자가 고른 부분집합**을 승격한다(모호하면 물어본다).
   - **근사중복 자문(#133)**: `study.py near <project>`로 재서술된 근사중복 후보를 확인할 수 있다(어휘 겹침 **내림차순 상위 K** — **자문 전용**, 자동병합 없음). 목록에 올랐다는 것 자체는 판정이 아니다 — `overlap`을 함께 보고 판단한다(#306: 임계 필터는 한국어에서 사실상 발화하지 않아 빈 결과가 "근사중복 없음"으로 읽혔다). **`overlap`은 어휘가 얼마나 겹치는지이지 의미가 같은지가 아니다**(#387 실측: 의미가 정반대인 쌍이 같은 의미인 쌍보다 5~7배 높게 나온다) — 값이 크다고 같은 지식인 것이 아니므로 스니펫 원문을 읽고 판정한다. 값은 코퍼스 통계(IDF)에 따라 달라지므로 절대값으로 인용하지 않는다. 같은 지식의 변주로 판단되면 하나만 승격하고 나머지는 discard한다. 정확 판정은 사람·모델의 몫.
   - **노이즈 정리(#256·#263)**: 노이즈 판정은 **눈대중이 아니라 `prune --dry-run`**이다 — 펼치지 않은 그룹의 노이즈는 보이지 않으므로 "보이면"을 트리거로 두면 놓친 노이즈가 `--clear`에서 원장에 비가역 기록된다(#306). `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/study.py" prune <project> --dry-run`을 **항상 먼저** 실행하고 `matches`가 **1건 이상이면** discard가 **아니라** `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/study.py" prune <project>`로 정리한다 — discard는 노이즈 id를 원장(공유 원장 write-through 포함)에 **비가역 기록**한다. 단 prune은 그룹이 아니라 **인박스 전역** 대상이다: 먼저 `--dry-run`으로 매치 목록을 확인해 오폭(`--- ` 접두 실사실·diff 헤더 인용)을 검토한 뒤 실행한다. 오폭 매치(실사실)가 있으면 그 후보를 먼저 4~6단계로 승격해 인박스에서 빼낸 뒤 prune한다(prune에는 선택 제외 수단이 없다).

4. **개념화·제안(판정, 후보별)**: okf 스킬 §2·§3·§6대로 배치·`type`·본문을 판정하되, **개념 파일을 직접 쓰지 않는다** — 산출은 후보별 제안 JSON이고 실체화·검증은 5단계 apply가 수행한다(#351: `/okf-promote`와 같은 집행기로 수렴 — 판정과 집행의 분리를 inbox 승격에도).
   - **번들 관측(선행)**: `"${CLAUDE_PLUGIN_ROOT}/bin/okf" census <bundle>`로 디렉토리 형상(직속·하위 개념 수, 깊이, 내부/유입/유출 링크)·축 값 분포와 값×디렉토리 교차표·개념별 요약 원문을 확보한 뒤 배치와 `type`을 정한다(스킬 §2-1·2-2·2-3). `--axis <키>`로 다른 축을 함께 볼 수 있다. **관측은 자문이다** — 승격 게이트의 입력이 아니고, 판정 근거는 관측이 좁혀 준 개념의 **원문**이다.
   - **인식층 판정**: 후보의 인식 고도를 판정해 `layer`(`information`/`knowledge`/`wisdom` — 각각 정보·지식·지혜)를 부여한다 — 카테고리 = `type` + 주제 디렉토리 + `layer`. 불확실하면 **미기재**한다 — 층 게이트는 층이 있을 때만 적용된다(#351). 어휘·판정 기준(하향식 루브릭: 처방→연결→대조)은 스킬 `reference/LAYERS.md`(§1·§8).
   - **존재 대조(멱등)**: 층을 정했으면 **세션에 주입된 개념 목록**(`okf context`의 `<경로> [<type>] — <핵심>` 줄)에서 같은 층에 이미 있는지 대조한다 — 지표 자문을 두지 않는다(#391: 주입이 개념 전량을 주므로 상위 K로 좁힐 이유가 없다). 주입은 전량이 `context.maxChars`를 넘으면 목록 대신 **축 윤곽**으로 저하하므로(#403 — 잘린 목록은 나오지 않는다), 윤곽이 왔으면 `okf query`로 대조한다. 같은 정보면 신설이 아니라 **`mode: update` 제안**으로 기존 개념을 갱신한다(스킬 §3 supersedes — 갱신 이력은 apply가 log.md 엔트리 kind `Update`로 남기고, 기존 frontmatter는 apply가 로드·병합해 미지 축을 보존한다. `derived_from`·`resource` 미제공은 유지, 빈 리스트는 명시적 소거. 중첩 frontmatter 등 실체화 불가 반려는 스킬 §3 수동 편집 경로다). exact 재부상 차단은 원장이 이미 한다.
   - **접지(교차층 맵핑)**: 상위 층(지식·지혜)은 근거 하위 개념을 `derived_from`으로 잇는다 — `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/explore/okf_layers.py" <bundle> --candidates-for <layer> --json`로 후보를 질의한다(정초 엄격 하향: 지식→정보, 지혜→지식·정보). 근거 사실이 후보에 함께 있으면 그 정보 제안을 배치 앞에 둔다(같은 배치의 선행 승격 재료는 스냅샷 면제 — §9 원칙 3 캐스케이드). 번들에 **실존하는** `derived_from` 재료는 `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/okf_promote.py" snapshot <bundle> --paths <재료 경로…>` 출력 `materials`를 제안에 담는다(§9 금지 3 — 재료 무수정 검증). 번들에 없는 근거는 만들어내지 않는다 — `allow_dangling`에 명시해 접지 린트가 "미작성 지식 신호"로 잡게 둔다(출처·근거 날조 금지). 하위층 개념이 쌓여 상위층 신설(층간 승격)이 필요해 보이면 LAYERS.md §9의 승격 절차를 따른다(자문 — 재라벨이 아니라 적립, 전용 플로우는 `/okf-promote`).
   - **제안 JSON**: `mode`(`create` 신설 | `update` 갱신 — **명시**, path 실존으로 암묵 판별하지 않는다) · `layer`(§8 판정, 미기재 허용) · `path`(create는 **주제 하위디렉토리** — 스킬 §2 배치, update는 기존 경로) · `type` · `description`(1문장) · `body`(답-우선, 백링크 ≥1 — 재료 링크는 루트 상대) · `derived_from` · `materials` · 정보층 create면 `resource` · `allow_dangling` · `log_note`(**provenance 이관**, #114 U5 · #132 — 후보의 캡처 일자를 `captured <후보 date>`로 새겨 비-git 스테이징의 적립 시점을 git-추적 `log.md`에 남긴다. `list`의 `recurrence`(출현 전이 수 — 파일 저장 횟수가 아니라 블록이 파일에 새로 나타난 횟수, #369)가 크면 반복 학습 신호로 함께 반영할 수 있다. 더 세밀한 순서·시각 이력은 `study.py log <project>`) · 상위 층 create면 `rubric`(자기검증 `new_insight`·`falsification` — 게이트가 빈칸을 반려하고 통과분은 본문 `## 자기검증` 섹션으로 영속된다, #307).

5. **게이트+집행(스크립트, 결정적)**: 제안 모음을 `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/okf_promote.py" apply <bundle> --proposals <제안.json>`으로 집행한다 — §9 기계 게이트 후 통과분만 쓰기 → `validate --strict` → 근거 사슬 감사 → `log append`(create는 kind `Promotion`, update는 `Update` — `log_note`의 캡처 일자가 요약에 함께 새겨진다) → `index --write` → 접지 린트를 **한 실행**이 수행한다. frontmatter 실체화가 기계라 형식 실수 계열이 소멸한다(#351 기대 효과 — 두 승격 경로의 게이트·로그·색인 순서가 한 구현으로 수렴).
   - **종료코드로 먼저 분기한다**(출력 텍스트가 아니라): `0` 전량 승격 · `1` 반려 있음 · `3` **집행 크래시** · `2` 인자·로드 오류.
   - `rejected[].reasons[]`는 `{code, detail}`이다(#360) — **`code`로 분기한다**(한국어 `detail` 매칭 금지, 코드 어휘는 `okf_promote.REJECT_CODES`가 단일원천 — 코드별 복구 지시 포함). 사유대로 제안을 고쳐 4단계 재판정으로 돌아간다 — `validate_failed`는 create면 롤백, update면 원문 복원이라 번들은 오염되지 않는다. `rubric_missing`이면 자기검증을 채우고(빈칸은 승인 근거가 되지 않는다), `update_unrenderable`(중첩 frontmatter 등 실체화 불가)만 스킬 §3 수동 편집 경로다.
   - **`error`가 있으면**(exit 3) 엔진 호출이 중간에 죽은 것이다. `error.stage`와 `error.detail`을 그대로 보이고, **같은 배치의 `promoted`는 이미 번들에 쓰였다**는 사실을 함께 알린다 — `index`가 돌지 않았으면 색인이 개념과 어긋난 상태다. `okf validate <bundle> --strict`·`okf index <bundle> --write`로 정합시킨 뒤 재시도한다. 6단계 드레인(로컬 원장 기록)은 그때까지의 `promoted`분만 진행하되, **정합 전에는 7단계 디스패치로 진행하지 않는다**(반쪽 상태를 원격에 반영하지 않는다 — #216 V4, `/okf-promote` ⑤와 같은 가드).
   - `lint_warns[]`는 **자문**이다(스펙 §9 판정 불변) — `code`별로 분기한다(한국어 `message` 매칭 금지): `derivation_order`(파생 대상을 더 낮은 층으로) · `ungrounded`(`derived_from`을 잇거나 근거 개념을 마저 쓴다 — `allow_dangling` 의도적 dangle은 "미작성 지식 신호"로 안내) · `no_source`(정보 층 `resource`를 채운다). 코드 집합은 `okf_layers.WARN_CODES`가 단일원천이다. 이미 승격된 개념의 보강은 4단계로 돌아가 `mode: update` 제안으로 잇는다.

6. **드레인**: apply 결과 `promoted[]`의 **기계 필드로** 드레인한다 — 개념마다 `study.py resolve <project> --id <id> --status promoted --ref <promoted[].path> --layer <promoted[].layer> --mode <제안의 mode>`(`layer`가 null이면 `--layer` 생략 — 미기재 승격). `--mode`는 4단계 제안 JSON의 `mode`를 그대로 넘긴다(#393 — 흡수(`update`) 건수가 중복 판정 실수요의 분모다. apply가 직접 남길 수 없다: `okf_promote`는 캡처 런타임 무-import). `rejected[]`로 남은 후보는 드레인하지 않는다(수정·재제안 대상). 버릴 후보는 `--status discarded` — 단 구조 노이즈는 discarded가 아니라 3단계의 prune으로(원장 오염 방지). `--layer`는 판정 인식층을 저널·후보에 provenance로 새긴다(후보 드레인 후에도 `study.py log`에 층이 남는다).
   - **파일 단위 일괄(#258)**: 한 파일 그룹을 통째로 버릴 땐 `study.py resolve <project> --source <경로> --status discarded` — 펼치지 않은 그룹이면 먼저 3단계의 `list --by-file --source <경로>`로 펼쳐 구조 노이즈가 섞였는지 확인한다(섞였으면 3단계의 prune 먼저 — 일괄 discard는 보지 못한 노이즈 id까지 원장에 기록한다). 경로는 2단계 그룹의 `source` 값 그대로 쓴다(저장값 정확 일치 매칭이라 rename·삭제된 옛 경로의 잔존 후보 정리에도 쓴다. 매칭 0건이면 현존 source 목록과 함께 실패한다). 여러 후보를 **한 개념으로 병합 승격**했다면 다중 `--id`(반복) + 단일 `--ref`로 일괄 promoted 처리한다 — 단일 `--ref`는 "N후보 → 1개념 병합"의 의미다. 개념별 개별 승격은 종전대로 후보당 resolve가 기본이다.
   - **뒤늦게 발견한 중복(#393)**: 승격을 마친 뒤 "이 두 개념이 사실 같은 말이었다"를 알아채면 `study.py dedup-miss <project> --concepts <경로> <경로> [--captured <후보 id>]`로 남긴다. 자문이 놓친 사건이라 의미 기반 지표 도입 필요성의 직접 근거가 된다 — 지금 지표는 어휘 겹침만 재므로 표현이 완전히 갈린 재서술을 못 잡는다(#387). 기록은 관측일 뿐 원장·후보를 건드리지 않으며, 누적 현황은 `study.py dedup-report <project>`로 본다.
   - **교차 승격 규약(#91 §4)**: 프로젝트 inbox의 후보를 vault 번들로 승격했다면 `resolve`는 **후보가 잡힌 스코프**(그 프로젝트)에 대해 실행하고 `--ref`에 vault 개념 경로를 준다 — 기록은 원 스코프 원장이 정본, 유효 vault가 있으면 vault 원장에도 자동 write-through된다(시간축 재큐 방지).

7. **디스패치**: 승격 개념마다 `study.py dispatch <project> --source manual --concept-path <경로> --concept-type <type> --concept-topic <topic> --concept-layer <layer>`. 결과의 **기계 필드**로 분기한다(한국어 `note`를 매칭하지 말 것 — 문구가 바뀌면 조용히 깨진다).
   - `reflected: true` → 원격 반영 경로를 탔다. 다음으로.
   - `reflected: false` → `blockers[]`의 각 항목에 `code`와 **실행 가능한 복구 지시**(`recovery`)가 있다. 그 지시를 그대로 사용자에게 보인다. 코드별 의미: `unwired`(배선 없음 — `/okf-init --vault`) · `untracked`(핸들러 미커밋 — vault repo에 커밋, 관리형 clone이면 브랜치→PR) · `not_executable`(핸들러 실행권한 없음 — `chmod +x` 후 그 mode를 커밋) · `untrusted`(이 머신 미승인 — `/study --trust`) · `escape`(command가 repo 밖 — 설정 수정) · `handler_failed`(핸들러가 비-0으로 끝남 — 해당 항목의 `failed[].output`에 담긴 핸들러 통지를 함께 보이고, 원인을 고친 뒤 재디스패치하도록 안내한다). **어느 경우든 개념은 이미 로컬 번들에 승격·검증됐고 원격 반영만 보류된 상태다**(가시적 저하 — 승격을 되돌리지 않는다).
   - `reclaimed`(관리형 clone에서만 옴)가 비어 있지 않으면 **원격에 반영이 확인된** 잔재를 정리한 것이다(#216 V2). 경로 수만 한 줄로 보이고 넘어간다 — 미푸시 승격은 봉인되지 않으므로 여기 오지 않는다.

8. **요약**: 승격/폐기/디스패치 결과를 알리고, 오래된 후보가 많으면 `/study --clear`를 제안한다 — `--clear`도 전량 discard(원장 기록)이므로, 제안 전에 `prune --dry-run`을 실행해 `matches`가 **0건일 때만** 제안한다(1건 이상이면 prune을 먼저 안내). 노이즈 유무를 눈으로 판단하지 않는다(#306).
