---
type: concept
title: Architecture
description: okf-core 파이프라인과 모듈 경계.
tags: [engine, architecture]
---

# 파이프라인

파스는 한 곳에서만 일어난다: `parser.parse`가 파일당 1회 실행되어 ParsedDoc을
만들고, [Conformance Decisions](/conformance-decisions.md)에 따른 §9 검사와
정책 검사가 같은 ParsedDoc을 재사용한다(재파싱 금지 — 호출 카운터 테스트로
고정). index·graph·context도 동일한 순회(`walk_bundle`)를 공유한다.

# 모듈 경계

* parser — frontmatter·본문·인라인 링크 추출(펜스 내부 제외)
* validate — §9 3규칙만 error, 거부 금지 항목은 warn(§5.3·§9)
* policy — 권장 필드 warn 뼈대(판정 상수는 규칙 데이터에서)
* index / graph / context — §6 재생성, 링크 그래프, 주입용 압축 인덱스
* cli — 서브커맨드 5종을 각 모듈 main으로 위임

판정 상수는 코드가 아니라 `rules/v0_1.json`에 있고, 엔진은 특정 소비자를
모른다(무참조 grep 불변식).
