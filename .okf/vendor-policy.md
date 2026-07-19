---
type: concept
title: Vendor Policy
description: 스펙·오라클 벤더 반입과 무수정 원칙.
tags: [vendor, policy]
---

# 원칙

`okf-core/vendor/`의 스펙(SPEC.md)과 오라클(okf_validate.py)은 업스트림
바이트 그대로 반입한다 — 헤더 추가·개행 정리 등 일체의 수정 금지.

* 핀: `vendor.lock`이 name/source/ref/upstream_path/license/imported와 파일별
  sha256을 기록한다.
* 검증: `scripts/vendor_sync_check.py`가 lock 대비 해시를 재계산하고 CI에서
  상시 실행된다 — 벤더 파일이 1바이트라도 달라지면 실패.
* 수정이 필요하면 벤더 파일을 고치지 않고 `vendor/patches/`에 패치로 둔다.

판정의 원천은 벤더 스펙 §9이며, 우리 해석은
[Conformance Decisions](/conformance-decisions.md)에 기록한다.
