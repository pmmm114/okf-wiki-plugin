---
type: decision
title: Conformance Decisions
description: "§9 해석 결정: error는 3규칙만, 예약 파일 구조 위반은 error, 오라클과의 차이."
layer: wisdom
derived_from:
  - /architecture.md
  - /spec-facts.md
tags: [conformance, decision]
---

# 결정

* error는 §9의 컨포먼트 조건 3개에서만 나온다 — OKF9.1(파싱 가능한
  frontmatter), OKF9.2(비어있지 않은 필수 필드), OKF9.3(예약 파일 구조).
* §9가 거부 금지로 못박은 항목(옵션 필드 누락, 미지 type, 미지 키, 깨진
  링크, 인덱스 부재)은 warn까지만. `--strict`는 깨진 링크·권장 필드 부재
  warn만 error로 승격한다.
* 예약 파일 구조 위반(비루트 인덱스의 frontmatter, 비ISO 날짜 헤딩)은 §9
  조건 3을 문면대로 읽어 **error**로 판정한다.

# 오라클과의 차이

벤더 오라클은 §9.1·§9.2만 error로 내고 예약 파일 구조는 §6/§7/§11 warning으로
보고한다. 차동 비교(oracle diff)에서는 §9 위반 집합을 비교하되 이 차이를
매핑해야 한다. [Vendor Policy](vendor-policy.md)의 무수정 원칙에 따라
오라클을 고치지 않고 어댑터가 흡수한다.
