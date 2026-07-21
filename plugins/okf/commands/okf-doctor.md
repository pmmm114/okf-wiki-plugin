---
description: okf 홈 폴백·캡처 입구 진단 — 현재 위치의 스코프 해소 결과와 이유
---

`"${CLAUDE_PLUGIN_ROOT}/bin/okf-py" "${CLAUDE_PLUGIN_ROOT}/scripts/okf_doctor.py" .`를 실행하고 출력을 **그대로**
보여준다. 판정·안내는 전부 스크립트가 한다(§5 원칙) — 출력에 자체 해석을 덧붙이지
말 것. 출력의 `[회복]` 절에 명령이 안내되어 있으면, 사용자가 원할 때 그 명령
실행을 도와준다(`study scan --enqueue` 재적재 후 `/study` 선별 승격).
