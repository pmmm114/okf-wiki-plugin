# 배포·버전관리 전략

이 repo의 **"다음 버전 선정 → 컷 → 태그 → 배포"** 전략 정본이다. 브랜치·PR·머지·
태그 **메커니즘**은 [`branching.md`](branching.md), 에이전트 규칙 요약은
[`../CLAUDE.md`](../CLAUDE.md)에 있다. 이 문서는 그 위에서 **무엇을 언제 릴리스로
묶고 버전을 어떻게 매기는가**를 담당한다.

> 이 전략은 의사결정 분석(대안 4종 · 가중 평가 · 민감도)으로 선정됐다. 요지:
> 트렁크 기반·선형 main·불변 SemVer 태그라는 기존 제약에 가장 적합하고, 뒤에
> 케이던스·릴리스 브랜치를 *되돌리지 않고 얹을 수 있는* 옵션 가치가 가장 크다.
>
> **버전 넘버링 갱신(#164)**: 버전을 사전 확정하지 않는다. main은 버전-중립
> 플레이스홀더(`0.0.0.dev0`)로 굴러가고, 번호는 **컷 때 실제 랜딩분에서 도출**한다
> (`feat`→minor · `fix`→patch · `feat!`/`BREAKING`→minor(0.x)). 마일스톤은
> **스코프 전용**이 되어 버전명을 뗐다. 이로써 "이번이 patch냐 minor냐"를 사이클
> 시작에 미리 베팅하던 마찰이 사라진다. 완전 무인 자동화(release-please)는 재검토
> 트리거로 유예했다(§예외·재검토 트리거).

## 결정 요약

| 축 | 채택 | 유예(트리거 시) |
| --- | --- | --- |
| 릴리스 모델 | **마일스톤 게이트 트렁크** — 마일스톤이 **스코프**, 완성 커밋에 태그(번호는 컷 때 도출) | 릴리스 브랜치(다중 버전 유지 필요 시) |
| 버전 체계 | **커밋-도출 SemVer** — 버전-중립 main + 컷 때 랜딩분에서 번호 도출, 0.x 관례 | release-please 자동화(무인 릴리스 요구 시) · CalVer(부적합) |
| 배포 | **repo 직배달(태그 핀) + GitHub Release** | PyPI publish(외부 Python 소비자 등장 시) |
| 케이던스 | 이벤트 구동(마일스톤 완성) + 목표일은 가이드 | 고정 트레인(소비자 출시일 요구 시) |

두 불변을 동시에 만족시키는 방식:

- **main은 집합소(G2)** — 버전-중립 플레이스홀더(`0.0.0.dev0`)로 굴러간다. 릴리스는
  그 위의 **라벨(태그)**일 뿐, "지금 main 전부"가 아니라 "마일스톤 완성 커밋의
  스냅샷"이며, 그 스냅샷의 **번호는 컷 때 도출**한다.
- **릴리스엔 정해진 항목만(G1)** — 범위는 **마일스톤 멤버십**으로 정의. 먼저
  랜딩해야 하는 미완성분은 **비활성 기본값**(예: `study.capture: off`)으로 재워
  릴리스 동작에 영향이 없게 한다 → 집합소이면서도 활성은 선별된다.

## 버전 체계 — SemVer

태그 `vX.Y.Z` 하나가 **repo 전체 묶음**(엔진 + 플러그인 + `actions/validate` +
pre-commit)의 직배달(D2) 릴리스다.

- **숫자 SoT**: `okf-core/pyproject.toml`의 `version`. ⚠️ 루트 `pyproject.toml`
  셔틀에도 같은 버전이 **하드코딩**돼 있으니 릴리스 시 **둘 다** 올린다(체크리스트).
  두 값의 동기 + main의 버전-중립(`0.0.0.dev0`)은 `scripts/test_version_sync.py`
  (CI `core` 잡의 `pytest scripts`)가 강제한다 — 사전 minor 상향은 red로 막힌다.
- **플러그인**은 version 필드 없음(SHA 추적, `plugin.json` 불변식) → 플러그인의
  "버전"은 소비처가 핀하는 태그 그 자체다.
- **버전-중립 main(#164)**: 개발 중 main은 실버전을 얹지 않고 플레이스홀더
  `0.0.0.dev0`으로 굴러간다. **다음 번호를 사전 확정하지 않기 위해서다** — 예전처럼
  `0.(Y+1).0.dev0`로 미리 올려 minor를 사전 베팅하지 않는다. 릴리스 때 컷 커밋에
  도출된 실번호를 두 pyproject에 기입 → 태그 `vX.Y.Z` → **직후 main을 `0.0.0.dev0`로
  복귀**한다. "직전 실버전"의 단일 원천은 **최신 태그**다.

### 무엇이 어느 자리를 올리나 (0.x 관례)

소비자 **계약 표면** = §9 컨포먼스 규칙(`rules/v0_1.json`) · `okf` CLI(서브커맨드·
플래그·종료코드) · `actions/validate` 입력 · `.okf-wiki.json` 스키마 ·
`index`/`context` 출력 형식.

번호는 **랜딩된 스쿼시 커밋 타입**에서 도출한다(사전 결정하지 않는다). 컷 때
`scripts/next_version.py`가 직전 태그 이후 로그를 읽어 아래 매핑으로 다음 번호를
제안한다:

| 변화 | 커밋 타입 | pre-1.0 | 1.0 이후 |
| --- | --- | --- | --- |
| 계약 파괴(제거·의미 변경) | `feat!` · `fix!` · `BREAKING` | MINOR `0.Y` | MAJOR `X` |
| 하위호환 기능 추가 | `feat` | MINOR `0.Y` | MINOR |
| 버그 수정·계약 무변화 | `fix` | PATCH `0.0.Z` | PATCH |

- **0.x 승격 관례**: pre-1.0에선 계약 파괴도 MAJOR가 아니라 **MINOR**로 올린다
  (`bump-minor-pre-major`). `next_version.py`가 이 관례를 그대로 구현한다.
- `docs`·`chore`·`ci` 등 계약 무변화 타입만 쌓였으면 번호를 올릴 이유가 없다 —
  `next_version.py`는 "범프 신호 없음"으로 현행을 제안한다(릴리스 보류 신호).

> **주의**: `rules/v0_1.json`의 "v0.1"은 **OKF 스펙 버전**(벤더된 스펙 준거 레벨)
> 이지 이 툴셋 릴리스 버전이 아니다. 둘은 독립적으로 움직인다.

## 릴리스 범위 = 마일스톤 (스코프 전용)

마일스톤은 **무엇을 만들지(스코프/기획)**만 담고 **버전명을 붙이지 않는다**(#164).
버전 번호는 컷 때 랜딩분에서 도출하므로(위 §0.x 관례), 마일스톤 제목에 `vX.Y.Z`를
박아 patch/minor를 미리 베팅하지 않는다.

- GitHub **마일스톤**(제목 = 스코프명, 예: `study 스테이징 재설계`)을 만들고 들어갈
  이슈/Epic/유닛을 붙인다. 이 목록이 "이번 릴리스 포함 항목"의 단일 원천이다.
- **릴리스 준비 완료 = 마일스톤 100% 닫힘.** 목록에 없으면 이번 대상이 아니다.
  버전 번호는 이 시점에 도출한다 — 마일스톤이 정하는 건 범위지 번호가 아니다.
- 목표일을 둘 수 있으나 **컷을 강제하지 않는다** — 게이트는 날짜가 아니라
  마일스톤 완성이다. 그래서 "정해진 항목만"이 구조적으로 지켜진다.

### 마일스톤 생성·부착 (실무)

- **언제**: 사이클 시작 시(범위가 정해지면) 마일스톤을 먼저 만들고 이슈를 붙인다.
  Epic이면 Epic·유닛을 모두 같은 마일스톤에 부착한다.
- **생성 방법** — Title은 **스코프명**(버전명 금지):
  - UI: repo → **Issues → Milestones → New milestone**.
  - `gh`: `gh api repos/<owner>/<repo>/milestones -f title="<스코프명>" -f state=open -f description="<한 줄 요약>"`.
- **부착**: 이슈/PR의 Milestone 필드를 그 마일스톤으로. `gh issue edit <N> --milestone "<스코프명>"`
  또는 API(`PATCH .../issues/<N>` `milestone=<번호>`). 닫힌 이슈도 부착된다(사후 그룹핑 가능).
- ⚠️ **에이전트 주의**: GitHub MCP에는 **마일스톤 생성 도구가 없다**(조회·이슈·PR·
  브랜치만). 마일스톤 만들기는 **사람이 UI/`gh`로** 하고, 에이전트는 그 뒤 이슈
  부착(issue update의 `milestone` **번호**)만 한다. 번호는 마일스톤 URL
  `.../milestone/<N>`에 있다 — **제목이 아니라 번호**이고, 기존 마일스톤 번호와
  헷갈리지 않도록 URL로 확인한다.

## 릴리스 컷 절차

```mermaid
flowchart LR
  M[마일스톤 100%] --> F[짧은 머지 프리즈]
  F --> V[next_version.py<br/>번호 도출]
  V --> R[릴리스 PR<br/>실번호 기입 + CHANGELOG]
  R --> S[스쿼시 머지<br/>= 릴리스 커밋]
  S --> T[release-tag.yml<br/>mode=create]
  T --> G[GitHub Release<br/>자동 노트 · release_notes.py]
  G --> N[main → 0.0.0.dev0 복귀]
```

1. **프리즈** — 마일스톤이 닫히면 다음 버전용 머지를 잠깐 멈춘다(스필오버 차단).
2. **번호 도출** — `python3 scripts/next_version.py`로 직전 태그 이후 랜딩분에서
   다음 `vX.Y.Z`를 도출한다(사전 결정 아님). 제안이니 검토해 확정한다.
3. **릴리스 PR** — `okf-core/pyproject.toml`(+루트 셔틀)의 플레이스홀더에 도출된
   **실번호를 기입**, `CHANGELOG.md` 갱신, `okf validate .okf --strict`·테스트 재확인.
   스쿼시 머지 → 이 커밋이 릴리스 지점.
4. **태그** — `release-tag.yml`을 `mode=create tag=vX.Y.Z`로 dispatch(main에서).
   태그는 Actions 내부에서 그 커밋에 생성·**불변**이다(원격 세션 프록시가
   `refs/tags` 푸시를 막으므로 이 경로가 유일). 이미 있으면 워크플로가 중단.
5. **(관례) 보호 실증** — 필요 시 `mode=verify-protection`으로 태그 삭제·이동
   차단을 파괴 실증한다(branching.md §파괴 감지).
6. **GitHub Release** — `release-tag.yml mode=create`가 태그 직후 `scripts/release_notes.py`
   출력으로 **자동 발행**한다(소비처 참조 지점). 별도 수동 발행 불필요 — 문구를 다듬고
   싶으면 발행된 Release를 편집.
7. **버전-중립 복귀** — main의 두 pyproject를 **`0.0.0.dev0`으로 되돌리는** 후속
   커밋. 다음 minor를 미리 박지 않는다(#164).

### 릴리스 체크리스트

- [ ] 마일스톤(**스코프명**, 버전명 금지) 생성(사람이 UI/`gh`) + 대상 이슈·Epic·유닛 부착
- [ ] 마일스톤 100% 닫힘, 스코프 밖 항목 없음
- [ ] `python3 scripts/next_version.py`로 다음 `vX.Y.Z` 도출·확정
- [ ] `okf-core/pyproject.toml`에 도출 번호 기입 **+ 루트 `pyproject.toml` 동기**
- [ ] `CHANGELOG.md` 갱신(`scripts/release_notes.py` 출력 검수·붙여넣기 — 아래 생성법)
- [ ] `core` 잡 녹색 + `okf validate .okf --strict` error·warn 0
- [ ] `release-tag.yml mode=create`로 태그 생성 **+ GitHub Release 자동 발행**(로컬 태그 푸시 금지)
- [ ] 발행된 Release 본문 확인, 소비 예시 핀 갱신 확인(`actions/validate@vX.Y.Z`, pre-commit `rev`)
- [ ] main의 두 pyproject를 **`0.0.0.dev0`으로 복귀**

## CHANGELOG / 릴리스 노트

스쿼시라 태그 사이 `main` 로그가 **PR 1건 = 한 줄**(`type(scope): 제목 (#NN)`)이다.
`scripts/release_notes.py`가 이 로그를 파싱해 **추가(feat)·수정(fix)·문서(docs)·기타**로
묶어 마크다운으로 낸다 — 예전에 수기로 하던 `git log --pretty` + prefix 그룹핑의 자동화다
(stdlib·무의존·오프라인·결정론). `chore`·`release`(버전 범프·릴리스 커밋)는 기본 제외:

```bash
python3 scripts/release_notes.py                # 직전 태그..HEAD (다음 릴리스 미리보기)
python3 scripts/release_notes.py --to v0.4.0    # v0.3.0..v0.4.0 (특정 릴리스 재생성)
python3 scripts/release_notes.py --from v0.1.0 --to HEAD --all   # 범위·제외타입 지정
```

- **GitHub Release 본문은 태그 생성 시 자동 발행**된다 — `release-tag.yml mode=create`가
  태그를 만든 직후 이 스크립트 출력으로 Release를 만든다(아래 컷 절차 5번, 더는 수동 아님).
  문구를 다듬고 싶으면 발행된 Release를 편집한다.
- `CHANGELOG.md`(파일) 섹션은 아직 **사람이 이 출력을 검수·붙여넣기** 한다(릴리스 PR
  스텝) — 산문 다듬기·Epic 묶기 여지를 남긴다. 파일까지의 완전 자동화는 후속 과제(#142 향후).

## 배포·소비

- **repo 직배달(D2)** — 소비처는 태그를 핀한다:
  `uses: pmmm114/okf-wiki-plugin/actions/validate@vX.Y.Z`, pre-commit
  `rev: vX.Y.Z`. 레지스트리 publish는 없다(엔진은 `pip install ./okf-core` 또는
  repo 루트 설치).
- **GitHub Release**가 사람용 진입점(노트·자산)이다. CI의 `uv build` 산출 wheel을
  Release 자산으로 첨부할 수 있다(선택).

## 예외·재검토 트리거

아래 신호가 오면 이 전략을 다시 검토한다(그 전엔 얹지 않는다):

- **다중 버전 유지 필요**(구 메이저 패치 + 새 메이저 개발) → 해당 메이저에 한해
  `release/vX.Y` 릴리스 브랜치 + 백포트 도입.
- **외부 Python 소비자 등장** → `okf-core` PyPI publish(trusted publishing) 추가.
- **소비자가 고정 출시일 요구** → 마일스톤 위에 고정 케이던스(트레인) 오버레이.
- **완전 무인 릴리스·잦은 케이던스 요구** → 커밋-도출 자동화(release-please)를 이
  수동 도출(`next_version.py`) 위에 얹는다. 지금은 리뷰 게이트 문화 + 단일 패키지 +
  프록시 태그 제약(`release-tag.yml`)에 견줘 과해 유예한다(#164 검토 결론).
- **1.0 도달** → 계약 파괴가 MAJOR로 승격, 안정성 약속을 문서화.
