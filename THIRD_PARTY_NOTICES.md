# Third-Party Notices

이 저장소는 아래 서드파티 자산을 **무수정 벤더링**한다. 핀·해시의 기계가독 원장은 `okf-core/vendor/vendor.lock`, 수정 필요 시 절차는 `okf-core/vendor/patches/` 참조.

## okf-spec (`okf-core/vendor/spec/SPEC.md`)
- 출처: <https://github.com/GoogleCloudPlatform/knowledge-catalog> — `okf/SPEC.md`
- 핀: 커밋 `d44368c15e38e7c92481c5992e4f9b5b421a801d` (2026-07-18 반입, 바이트 동일 검증)
- 라이선스: Apache-2.0 — 전문 사본 `okf-core/vendor/spec/LICENSE-APACHE-2.0`

## okf-validate-oracle (`okf-core/vendor/oracle/okf_validate.py`)
- 출처: <https://github.com/scaccogatto/okf-skills> — `skills/validate/scripts/okf_validate.py`
- 핀: 태그 `okf--v0.4.0` = 커밋 `b8d9b1a21c577310f3293d33c9c6f12b82c507ab` (2026-07-18 반입)
- 라이선스: MIT — 전문 사본 `okf-core/vendor/oracle/LICENSE-MIT`
- 역할: CI 차동 오라클(불일치는 빌드 실패가 아닌 리포트 전용)

## 참고 고지
guhcostan/claude-mega-brain: 코드 미사용. 발상만 독자 재구현하였으며 코드 유입 시 MIT 고지를 추가한다.
