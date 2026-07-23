---
description: OKF 지식 번들과 study 런타임을 이 repo에 세팅(멱등) — `--vault`는 vault 포인터 마법사
argument-hint: "[--vault <path|url>]"
---

이 repo에 OKF 번들과 study 런타임을 세팅한다. **멱등**하므로 여러 번 실행해도
안전하고, 기존 파일은 덮어쓰지 않는다. 인자: `$ARGUMENTS`.

**`--vault <path|url>`가 주어지면 아래 대신 vault 포인터 마법사(#91·#153)를 수행한다.**
vault 값은 **로컬 clone 절대경로** 또는 **repo URL**(ssh/https/git/file) 둘 다 된다.
구 플래그 **`--home <path|url>`도 동일하게 처리한다**(deprecated alias — #152; 새 문서는
`--vault`를 쓴다).

H1. **검증·기록**: `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/study/study_scope.py" set <path-or-url>` 실행.
    출력의 `mode`(`"path"`|`"url"`)·`written`으로 분기한다(프롬프트 추측 금지 — 코드 판정).
    - `mode: "path"`, `written: true` → 로컬 경로 vault. `capture_ready` 값으로 H1a 분기 후 H2로.
    - `mode: "url"`, `written: true` → **URL vault(#153)**. 포인터엔 URL 원문(크레덴셜 제거본)이
      기록됐고, 관리형 clone은 **아직 만들지 않았다**(옵트인).
      - `clone_exists: true` → 관리형 clone(`clone_path`)이 이미 있다. `capture_ready`로 H1a 분기 후 H2로.
      - `clone_exists: false` → clone이 없다. 사용자에게 `clone_path`와 "생성엔 네트워크·디스크가
        든다"를 알리고 **동의를 받아** `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/core/okf_remote.py" clone`을
        실행한다(플러그인이 임의 clone하지 않는다 — #91 옵트인 계약).
        - `cloned: true, valid: true` → `set <url>`을 재실행해 `capture_ready`를 얻고 H1a로.
        - `cloned: true, valid: false`(원격에 `.okf-wiki.json` 부재) → 그 사실을 알리고, 원격 repo에
          큐레이션 번들(`.okf/`)·설정을 갖춘 뒤 재시도하도록 안내하고 종료.
        - `cloned: false`(오프라인·인증 실패) → 사유를 보이고, **포인터는 URL로 남으니**(이식 가능
          설정) 네트워크 회복 후 재실행하거나 다음 세션 fetch로 이어짐을 안내하고 종료.
        - 동의하지 않으면 포인터만 URL로 둔 채 종료(다음에 clone하면 활성 — 설정은 이식된다).
    - `written: false, reason: "URL 포인터 — 미지원 transport"` → https/ssh/git/file만 지원함을
      알리고 종료(`ext::` 등 명령 실행 transport는 보안상 거부).
    - `reason: ".okf-wiki.json 없음"`(로컬 경로만) → 대상이 아직 소비 repo 골격이 아니다. 사용자
      동의를 받아 **그 경로를 cwd로** 일반 초기화(아래 1~2단계)를 수행해 골격을
      만들고 `set`을 재시도한다. 스캐폴드 시 `study.capture`는 `review`를 권장 안내.
    - `reason: "대상 없음" | "git repo 아님"`(로컬 경로만) → 사유를 그대로 보이고 종료(경로 오탈자
      또는 git repo가 아닌 대상 — 로컬 vault는 실제 git repo여야 한다).
H1a. **캡처 준비 판정(스크립트 출력 `capture_ready` 기준 — 프롬프트 추측 금지)**:
    - `"active"` → vault가 이미 위치 무관 적재를 켠 상태다. 안내 없이 H2로.
    - `"off" | "absent"` → 이 vault는 지금 **주입(읽기) 전용**이라, 다른 위치에서 저장한
      메모리가 이 vault로 적재되지 않는다.
      - **로컬 경로 vault**: 사용자에게 그 사실과 "위치 무관 적재를 켜려면 vault에 캡처를 활성해야
        한다"를 알리고 **동의를 받아**
        `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/study/study_scope.py" enable-capture <path>`
        를 실행한다(스크립트가 vault `.okf-wiki.json`의 `study.capture: review`만 켜고
        런타임은 **유저 스코프 `~/.claude/okf/study`**에 보장한다 — vault엔 `.okf-study`를
        만들지 않는다, #114 U2. 판정·편집 모두 코드 경로). 성공 시 vault repo에서 바뀐
        `.okf-wiki.json`을 **커밋**하도록 안내한다(런타임은 유저 스코프라 커밋 대상 아님).
        동의하지 않으면 주입 전용으로 두고 H2로(강제하지 않는다 — 캡처는 vault 설정에
        쓰기이므로 사용자 선택이다).
      - **URL vault(관리형 clone)**: `enable-capture`는 관리형 clone의 커밋된 설정을 편집해 origin과
        diverge시키므로 **거부된다**(`reason: "managed-clone"` + `guidance`, #153 U2-6). 스크립트가
        낸 `guidance`를 그대로 전해 **원격 repo에 `study.capture`(review/auto)를 커밋**하도록
        안내하고(다음 세션 fetch로 반영) H2로. clone은 순수 소비 미러로 둔다.
H2. **trust 안내**: vault repo에 핸들러가 배선돼 있으면 로컬 승인이 필요함을 알리고
    vault에서 `/study --trust` 실행을 안내한다(미승인이면 디스패치만 보류됨).
H3. **확인 출력**: `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/core/okf_doctor.py" .`를 실행해
    "지금 이 위치에서 캡처/주입이 어디로 가는지"(결정 트레이스·건강·회복 안내)를
    그대로 보여준다.

**인자가 없으면 아래를 순서대로 수행하라.**

1. **study 런타임 스캐폴드(가드 게이트 — 반드시 첫 단계)**: 프로젝트 루트에서
   `"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/study/study_scaffold.py"`를 실행한다.
   - **exit 3(가드 거부, #104)**: cwd가 git repo가 아니다. 스크립트가 출력한 거부
     사유·대안(`/okf-init --vault`)을 **그대로** 사용자에게 전하고 **종료한다** —
     아래 2단계(번들)도 수행하지 않는다(로컬 산출물 0). 사용자가 사유를 보고도
     로컬 스캐폴드를 명시적으로 원할 때만 `--force`로 재실행하고 2단계로 진행.
   - exit 0: `.okf-study/.gitignore`(`*` + `!.gitignore`) 생성, `.okf-wiki.json`에
     `study` 블록(`capture: "off"`, `handlers: []`) 보장(기존 키 보존). 출력에
     vault 포인터 공존 고지("로컬 파이프라인이 vault 캡처보다 우선")가 있으면 그대로 전달.

2. **번들 스캐폴드**: `.okf-wiki.json`의 `bundlePath`(없으면 `.okf`)가 가리키는
   디렉터리가 없으면 `"${CLAUDE_PLUGIN_ROOT}/bin/okf" init <bundlePath>`를 실행한다.
   이미 있으면 건너뛴다(엔진 `init`은 비어있지 않은 디렉터리를 거부한다).

3. **결과 요약**: 각 단계 수행/유지 상태를 알리고, `study.capture` 기본값이
   `off`(자동 캡처 꺼짐)임을 안내한다. 자동 캡처를 원하면 `review`/`auto`로 올리고,
   핸들러는 **git에 커밋된 경로**에 두어야 함을 덧붙인다(설정 상세는
   `skills/okf/reference/CONFIG.md`).
