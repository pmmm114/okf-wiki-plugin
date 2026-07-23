# okf-core/vendor — CLAUDE.md

**정지. 이 디렉토리는 업스트림 바이트 그대로다 — 1바이트도 수정 금지.**

`spec/SPEC.md`(OKF v0.1 스펙, Apache-2.0)·`oracle/okf_validate.py`(차동 오라클, MIT)는
`vendor.lock`에 파일별 sha256으로 핀돼 있고, `../scripts/vendor_sync_check.py`가 CI `core` 잡에서
상시 대조한다 — 어떤 편집(헤더 추가·개행 정리·포맷·오타 수정 포함)도 해시를 바꿔 빌드를 red로
만든다. ruff도 이 디렉토리를 lint/format에서 제외하니 "포맷 정리"조차 하지 말 것.

수정이 필요하면:

- 벤더 파일을 직접 고치지 말고 `patches/`에 패치를 두고 `vendor.lock`을 갱신한다.
- 업스트림 변경 반영은 `../../.github/workflows/upstream-watch.yml`이 이슈로 알린 뒤, 사람이 검토해
  수동 반입한다(자동 반영 없음).

오라클은 CI 차동용 리포트 전용이다(불일치는 빌드 실패가 아님). §9.3 예약구조 위반을 warn으로만 내는
업스트림과의 차이는 `../scripts/oracle_diff.py` 어댑터가 흡수하니, 판정을 맞추려고 오라클을 고치지 말 것.
정본 서술은 자기 번들 `../../.okf/vendor-policy.md`.
