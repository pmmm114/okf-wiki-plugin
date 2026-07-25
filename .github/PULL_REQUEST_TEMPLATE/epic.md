<!-- Epic 통합 PR — epic/<이슈번호> → main. 유닛 PR은 기본 템플릿(pull_request_template.md)을
     씁니다. 이 템플릿은 `?template=epic.md`로 부르거나, 게이트가 통합 PR에 요구합니다. -->

## Epic 요약
<!-- 이 Epic이 하나의 기능으로서 무엇을 남겼는지 1~3문장 + 대응 Epic 이슈 -->

## 닫는 Epic
Closes #<Epic 번호>
<!-- base가 main이라 머지 시 자동으로 닫힙니다. 유닛 sub-issue는 각 유닛 PR이
     epic/* 로 머지될 때 이미 닫혔습니다(자동닫힘). -->

## 구성 유닛
<!-- 스쿼시로 main에서 사라질 유닛 경계를 여기에 박제합니다 = 추적성의 단일 지점.
     scripts/epic_prs.py가 생성·갱신합니다. -->
| sub-issue | 유닛 PR | 요약 | 상태 | 담당 |
| --- | --- | --- | --- | --- |

## 통합 검증
<!-- 유닛별 검증은 각 유닛 PR에 있습니다. 여기는 Epic 전체가 main 위에서 성립하는지만 -->
- [ ] epic 브랜치가 최신 main과 동기(up-to-date)
- [ ] core 잡 녹색
- [ ] 모든 sub-issue 닫힘(완결성)

## 체크리스트
- [ ] 공개 repo 불변식: 소비 조직·저장소 실명 및 내부 정보가 diff·PR 본문·커밋 메시지에 없다 (참조 방향 정책)
- [ ] 벤더 파일(`okf-core/vendor/`)을 수정하지 않았다
- [ ] Epic→main **스쿼시** 머지로 merge-0 불변식을 지킨다
