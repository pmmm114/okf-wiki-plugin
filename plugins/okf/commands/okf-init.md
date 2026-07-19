---
description: OKF 지식 번들과 study 런타임을 이 repo에 세팅(멱등)
---

이 repo에 OKF 번들과 study 런타임을 세팅한다. **멱등**하므로 여러 번 실행해도
안전하고, 기존 파일은 덮어쓰지 않는다. 아래를 순서대로 수행하라.

1. **번들 스캐폴드**: `.okf-wiki.json`의 `bundlePath`(없으면 `.okf`)가 가리키는
   디렉터리가 없으면 `"${CLAUDE_PLUGIN_ROOT}/bin/okf" init <bundlePath>`를 실행한다.
   이미 있으면 건너뛴다(엔진 `init`은 비어있지 않은 디렉터리를 거부한다).

2. **study 런타임 스캐폴드**: 프로젝트 루트에서
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/study_scaffold.py"`를 실행한다.
   - `.okf-study/.gitignore`(`*` + `!.gitignore`)를 생성 — inbox·ledger·trust 등
     런타임 상태는 커밋되지 않고 무시 규칙만 커밋된다.
   - `.okf-wiki.json`에 `study` 블록(`capture: "off"`, `handlers: []`)이 없으면
     추가한다(기존 키 보존, 이미 있으면 무변경).

3. **결과 요약**: 각 단계 수행/유지 상태를 알리고, `study.capture` 기본값이
   `off`(자동 캡처 꺼짐)임을 안내한다. 자동 캡처를 원하면 `review`/`auto`로 올리고,
   핸들러는 **git에 커밋된 경로**에 두어야 함을 덧붙인다(설정 상세는
   `skills/okf/reference/CONFIG.md`).
