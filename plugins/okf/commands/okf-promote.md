---
description: 번들에 쌓인 하위층 개념 위에 상위 층(지식·지혜)을 적립하는 층간 승격 플로우
argument-hint: "[<topic> | --layer <layer> | --bundle <path>]"
---

층간 승격(LAYERS §9의 도구화, Epic #197)을 실행한다. 인자: `$ARGUMENTS` (없으면 신호 리포트에서 주제를 고른다). 실행은 전부 플러그인 스크립트·`okf` CLI에 위임하고, **판정(재료 선택·§8 층 판정·본문 저작)과 제안만 직접** 한다. 판정 기준·승격 방법론은 재기술하지 않는다 — 정본은 스킬 `reference/LAYERS.md`(§8·§9), 탐색 인터페이스는 `reference/EXPLORE.md` 계약이다.

0. **번들 해소**: `--bundle`이 있으면 그 경로, 없으면 현재 repo의 OKF 번들 (`.okf/` 또는 `.okf-wiki.json`이 가리키는 번들). 이하 `<bundle>`.

1. **① 신호 수집(자문)**: `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/explore/okf_explore.py" run <bundle> signals`. 탐색은 EXPLORE.md 계약 경유다 — 리졸버가 `explore.provider`(설정 시·승인 시)를 쓰고 아니면 내장(okf_layers)으로 폴백하며, 외부 응답은 계약 검증기를 통과한 것만 나온다. stderr에 "미승인" 안내가 보이면 사용자에게 설정된 제공자 명령을 보여주고, 승인 의사를 받으면 `… okf_explore.py approve <project>`를 실행한다 (거절하면 내장으로 계속). 응답의 **계약 필드만 소비**하고 확장 필드에 로직을 걸지 않는다. 하위층 밀집·참조 집중·미접지·미분류에서 승격 후보 주제를 고른다(`<topic>` 인자가 있으면 그 주제로 한정). 신호는 자문이다 — 커도 §8 부적격이면 승격하지 않는다.

2. **②a 지형 탐색(자문)**: `… okf_explore.py run <bundle> map --topic <t>` (`--layer <층>` 인자가 있으면 전달). 맵의 description 인벤토리로 재료 후보를 좁힌 뒤, **고른 재료의 원문을 직접 Read**한다 — §8 판정 대상은 본문이며 맵 요약으로 판정하지 않는다.

3. **②b 판정·제안(모델)**: 재료 위에 설 새 인식(연결 또는 판단)을 §8 하향 루브릭으로 판정해 목표 층을 정한다. 재료의 재기술·요약뿐이면 **제안하지 않는다**(§9 원칙 2 — 그 사유만 5단계 요약에 남긴다).
   - **존재 대조**: **세션에 주입된 개념 목록**(`okf context`의 `<경로> [<type>] — <핵심>` 줄)에서 같은 층에 이미 있는지 본다 — 지표 자문을 두지 않는다(#391: 주입이 개념 전량을 준다. 전량이 `context.maxChars`를 넘으면 목록 대신 **축 윤곽**이 주입되므로(#403 — 잘린 목록은 나오지 않는다), 윤곽이 왔으면 `okf query`로 대조한다). 겹치면 신설 대신 **`mode: update` 제안**으로 기존 개념을 갱신한다(스킬 §3 supersedes — 갱신 이력은 apply가 log.md 엔트리 kind `Update`로 남기고, 기존 frontmatter는 apply가 로드·병합해 미지 축을 보존한다. 미제공 필드는 유지, 빈 리스트는 명시적 소거)(자문·비차단, #189 결정 B).
   - **재료 스냅샷**: `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/okf_promote.py" snapshot <bundle> --paths <재료 경로…>` → 출력 `materials`를 제안에 담는다.
   - **제안 JSON**(개념 파일을 직접 쓰지 않는다 — 실체화는 4단계 스크립트가): `mode`(`create` 신설 | `update` 갱신 — **명시**, path 실존으로 암묵 판별하지 않는다, #351) · `layer`(§8 판정 — 정식 어휘. 구 `target_layer`는 하위호환 별칭이다) · `path`(create는 주제 하위디렉토리 — 스킬 §2 배치, update는 기존 경로) · `type` · `description`(1문장) · `body`(재료 **위의** 연결·판단만, 재료 링크는 루트 상대) · `derived_from`(재료, 정초 엄격 하향 — 동일 층 관계는 본문 링크로) · `materials` · 정보층이면 `resource` · (선택) `log_note`(log.md 요약에 덧붙는 provenance 문구) · `rubric`(자기검증: `new_insight` — 무엇이 새 인식인가, `falsification` — 무엇이면 반증되는가). **rubric은 상위 층(지식·지혜) create 제안에 필수다** — 4단계 게이트가 두 필드가 비어 있으면 반려하고, 통과분은 개념 파일 본문 `## 자기검증` 섹션으로 **영속된다**(#307). 게이트가 보는 것은 필드의 존재이지 내용의 진위가 아니다 — 진위는 3단계 선별에서 사람이 본다. 번들에 없는 근거는 만들어내지 않는다 — `allow_dangling`에 명시해 미작성 신호로 남긴다.

4. **③ 선별(사람, 필수)**: 제안 목록을 표로 제시한다 — 층 · 경로 · description · 근거 n건 · 새 인식 요지 · 반증 요지. 사용자가 **부분집합**을 승인/반려하거나 재판정을 요청한다(→ 3단계 루프). 전량 자동 승인 없음, 모호하면 물어본다.

5. **④ 게이트+집행(스크립트, 결정적)**: 승인분만 `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/okf_promote.py" apply <bundle> --proposals <선택분.json>`. 스크립트가 §9 금지를 기계 게이트한 뒤 통과분만 쓰기 → `validate --strict` → 사슬 감사 → `log --kind Promotion`(update는 `Update`) → `index --write`를 수행한다. 결과 JSON의 `promoted`/`rejected`/ `lint_warns`(`{path, code, message}` — 분기는 `code`로)를 그대로 보인다. `rejected[].reasons[]`도 `{code, detail}`이다(#360) — **`code`로 분기한다**(한국어 `detail` 매칭 금지, 코드 어휘는 `okf_promote.REJECT_CODES`가 단일원천 — 코드별 복구 지시 포함). `rubric_missing`이면 3단계로 돌아가 자기검증을 채운다 — 빈칸은 승인 근거가 되지 않는다.
   - **종료코드로 먼저 분기한다**(출력 텍스트가 아니라): `0` 전량 승격 · `1` 반려 있음 · `3` **집행 크래시** · `2` 인자·로드 오류.

6. **⑤ 결과 처리**: 반려는 사유별로 — 게이트·검증 실패는 제안을 수정해 4단계 재선별로, 근사중복 갱신 판정분은 3단계에서 `mode: update` 제안으로 전환한다(`update_unrenderable` — 중첩 frontmatter 등 실체화 불가 반려만 스킬 §3 수동 편집으로). `lint_warns`의 의도적 dangle은 "미작성 지식 신호"로 안내한다.
   - **`error`가 있으면**(exit 3) 엔진 호출이 중간에 죽은 것이다. `error.stage`(`context`·`graph`·`log`·`index`)와 `error.detail`을 그대로 보이고, **같은 배치의 `promoted`가 이미 번들에 쓰였다**는 사실을 함께 알린다 — `index`가 돌지 않았으면 색인이 개념과 어긋난 상태다. 이때 **7단계 디스패치로 진행하지 않는다**(반쪽 상태를 원격에 반영하지 않는다). 사용자에게 `okf validate <bundle> --strict`와 `okf index <bundle> --write`로 번들을 정합시킨 뒤 재시도하도록 안내한다.

7. **⑥ 디스패치(원격 반영)**: 이 커맨드도 `/study`와 **같은 곳**(번들)에 쓰므로, 그 번들이 관리형 clone이면 승격분이 로컬 잔재로 남아 신선도 갱신을 막는다(#216 V4). 승격 개념마다 `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/promote/study.py" dispatch <bundle의 repo 루트> --source manual --concept-path <경로> --concept-type <type> --concept-topic <topic> --concept-layer <layer>`를 실행한다.
   - `reflected: false`면 `blockers[]`의 `code`·`recovery`로 분기한다(한국어 `note` 매칭 금지 — 문구가 바뀌면 조용히 깨진다): `unwired`(배선 없음) · `untracked`(핸들러 미커밋) · `not_executable`(핸들러 실행권한 없음) · `untrusted`(이 머신 미승인) · `escape`(command가 repo 밖) · `handler_failed`(핸들러가 비-0으로 끝남 — `failed[].output`의 통지를 함께 보인다). 각 항목의 `recovery`를 **그대로** 보이고, 승격분이 로컬에만 남는다는 사실을 함께 알린다(무동의 파괴 금지 — 임의로 지우지 않는다).
   - `reclaimed`가 오면 원격 반영이 확인된 잔재를 정리한 것이다. 경로 수만 보인다.
   - 로컬 경로 vault·비-clone 번들이면 이 단계는 무동작이다(디스패치가 스스로 판정).

8. **요약**: 승격/반려/미제안 사유와 디스패치 결과를 요약하고 종료한다.
