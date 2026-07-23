---
type: fact
title: Spec Facts
description: OKF §9 컨포먼스는 error 조건 3개(9.1·9.2·9.3)이고 나머지는 거부 금지다.
layer: information
resource: okf-core/vendor/spec/SPEC.md
tags: [spec, conformance]
---

# §9 컨포먼스 사실

벤더 스펙 §9가 정의하는 객관적 사실(출처는 frontmatter `resource`):

* error 조건은 **정확히 3개** — 9.1(파싱 가능한 frontmatter), 9.2(비어있지 않은
  `type` 필드), 9.3(예약 파일 구조).
* 거부 금지 항목 — 옵션 필드 누락, 미지 `type` 값, 미지 추가 키, 깨진 링크, 인덱스 부재.
* frontmatter는 임의 생산자 키를 허용하고, 소비자는 미지 키를 거부하지 않는다(§4.1 Extensions).

이 사실의 해석·판정은 [Conformance Decisions](conformance-decisions.md)에 기록한다.
