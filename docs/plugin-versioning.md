# 플러그인 배포·버저닝 (마켓플레이스 채널)

`.claude-plugin/marketplace.json`으로 배포되는 okf 플러그인이 **소비처에서 올바른 버전으로
해소되도록** 하는 배포 형태의 정본이다. 릴리스 **전략**(스코프 마일스톤·커밋-도출 SemVer·컷
절차)은 [`releasing.md`](releasing.md), 브랜치·태그 **메커니즘**은 [`branching.md`](branching.md),
에이전트 규칙 요약은 [`../CLAUDE.md`](../CLAUDE.md)에 있다.

## 배경 — Claude Code의 버전 해석

소비처는 이렇게 붙인다:

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf@okf-wiki-plugin
```

Claude Code는 repo의 `.claude-plugin/marketplace.json`을 읽고, 플러그인 **버전을 다음
순서로** 해석한다(공식 문서 `plugin-marketplaces` 기준):

1. 플러그인 `plugin.json`의 `version`
2. marketplace 엔트리의 `version`
3. 소스의 **git 커밋 SHA**

버전은 캐시 키이자 업데이트 감지 기준이다. version을 생략하면 매 커밋이 새 버전(SHA)이 되어
소비처는 커밋마다 자동 업데이트된다(공식 문서가 "활발히 개발 중인 플러그인의 가장 단순한
설정"으로 부르는 형태).

> **불변식**: `plugin.json`엔 version을 두지 않는다. 위 순서상 `plugin.json`이 있으면
> marketplace 엔트리를 **조용히 덮기** 때문이다(경고 없음). `claude plugin validate`는
> 비-strict로 돌리고, `scripts/test_marketplace_version.py`가 version 부재를 못박는다.

## 이 repo의 배포 형태 — 모노레포 + 심링크 + 상대경로

이 repo는 **모노레포**다 — 엔진(`okf-core`) + 플러그인(`plugins/okf`) + `actions/validate` +
pre-commit을 **태그 하나로 묶어** 배포한다(releasing.md §버전 체계). 그래서 플러그인은 repo
루트가 아니라 `plugins/okf` **하위**에 있고, 엔진을 **심링크**로 공유한다:

```
plugins/okf/core -> ../../okf-core     # bin/okf 셔틀이 이 심링크로 엔진을 실행
```

이 심링크 공유는 공식 문서가 권장하는 기법이다 — *"share files across plugins, use symlinks"*.
그 결과 marketplace 엔트리는 **한 형태만** 동작하며, 게이트가 이를 고정한다:

```json
{ "name": "okf", "source": "./plugins/okf" }
```

- **상대경로 소스만 심링크를 해소한다.** git 마켓플레이스는 상대경로일 때 **repo 전체를
  클론**하므로(공식 문서) 형제 디렉터리 `okf-core`가 함께 와서 심링크가 산다.
- **git-subdir 소스 금지.** git-subdir는 하위 디렉터리만 **sparse 클론**해 `okf-core`가
  빠지고 심링크가 dangling → 플러그인이 깨진다.
- **엔트리 version 금지.** 상대경로(SHA 추적) 소스에 정적 version을 박으면 소비처 자동
  업데이트가 동결되고 라벨이 내용과 어긋난다.

내부 형태(상대경로·무version)는 `scripts/test_marketplace_version.py`(CI `core` 잡의
`pytest scripts`)가 무git으로 강제한다.

## 소비처의 버전 선택 — add 시점의 ref

marketplace.json은 항상 "최신 main"을 상대경로로 가리키므로, **어느 버전을 받을지는
소비처가 add 시점에** 고른다(공식 문서: GitHub shorthand에 `@ref`를 붙여 핀).

```
# 최신(개발 트렁크) — main HEAD를 커밋마다 자동 업데이트받는다
/plugin marketplace add pmmm114/okf-wiki-plugin

# 특정 릴리스에 고정 — 그 태그의 repo 전체를 클론(심링크 해소), 큐레이션된 릴리스
/plugin marketplace add pmmm114/okf-wiki-plugin@v0.5.1
```

`@vX.Y.Z` 핀은 repo 전체를 그 태그로 클론하므로 심링크가 그대로 해소된다. 이것이
`actions/validate@vX.Y.Z`·pre-commit `rev: vX.Y.Z`와 같은 **"소비처가 태그를 핀한다"**
계층이다(releasing.md §배포·소비). 즉 플러그인 채널도 태그-핀을 지원하되, **marketplace.json이
아니라 소비처의 add 명령**에서 고정한다.

## 릴리스와의 관계

- **릴리스마다 marketplace.json을 바꾸지 않는다.** 소스는 계속 `./plugins/okf`(SHA 추적)이고,
  소비처가 원하면 `@vX.Y.Z`로 그 태그 스냅샷을 받는다. 태그 자체가 핀 지점이다.
- pyproject 버전(`0.0.0.dev0` ↔ 릴리스 컷의 `X.Y.Z`)은 엔진 배포용이고, 마켓플레이스
  채널과는 독립이다 — 릴리스 컷 절차(releasing.md)에 marketplace.json 손질 스텝은 없다.
