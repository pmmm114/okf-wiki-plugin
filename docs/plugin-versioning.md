# 플러그인 배포·버저닝 (마켓플레이스 채널)

`.claude-plugin/marketplace.json`을 통해 배포되는 **okf 플러그인의 버전을 git에 맞게 항상
올바르게 유지하는** 시스템의 정본이다. 릴리스 **전략**(스코프 마일스톤·커밋-도출 SemVer·컷
절차)은 [`releasing.md`](releasing.md), 브랜치·태그 **메커니즘**은 [`branching.md`](branching.md),
에이전트 규칙 요약은 [`../CLAUDE.md`](../CLAUDE.md)에 있다. 이 문서는 그 위에서 **플러그인
마켓플레이스 채널이 어떤 버전을 어떻게 소비처에 노출하는가**를 담당한다.

## 배경 — Claude Code의 버전 해석

소비처는 이렇게 플러그인을 붙인다:

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf@okf-wiki-plugin
```

이때 Claude Code는 repo의 `.claude-plugin/marketplace.json`을 읽고, 플러그인의 **버전을
다음 순서로** 해석한다(공식 문서 `plugin-marketplaces` 기준):

1. 플러그인 `plugin.json`의 `version`
2. marketplace 엔트리의 `version`
3. 소스의 **git 커밋 SHA**(github·url·git-subdir·git-호스팅 마켓플레이스의 상대경로)

버전은 **캐시 키이자 업데이트 감지 기준**이다 — 해석된 버전이 사용자가 가진 것과 같으면
`/plugin update`·자동 업데이트가 그 플러그인을 건너뛴다.

- **version을 생략하면** 매 커밋이 새 버전(SHA)이 되어 소비처는 커밋마다 자동 업데이트된다.
- **version을 박으면** 그 문자열이 바뀔 때만 업데이트된다(같은 문자열로 커밋을 쌓아도 무효).

> **불변식**: `plugin.json`엔 version을 두지 않는다. 위 순서상 `plugin.json`이 있으면
> marketplace 엔트리를 **조용히 덮어** 핀을 무력화하기 때문이다(경고도 없다). 그래서
> 사람이 읽는 버전은 **marketplace 엔트리**가 진다. `claude plugin validate`는 비-strict로
> 돌리고, `scripts/test_marketplace_version.py`가 version 부재를 못박는다.

## 두 단계 수명주기

marketplace.json의 okf 엔트리는 **딱 두 형태만** 허용한다. 태그 유무가 단계를 가른다.

### Phase P — 첫 릴리스 전 (태그 0개)

```json
{ "name": "okf", "source": "./plugins/okf" }
```

version 없음 + 상대경로 소스 → **커밋 SHA 추적**. 소비처는 main HEAD를 따라 매 커밋
자동 업데이트받는다. 아직 핀할 릴리스가 없으니 이게 정상이다(공식 문서가 "활발히 개발 중인
플러그인의 가장 단순한 설정"으로 부르는 형태).

### Phase R — 릴리스 후 (태그 ≥1개)

```json
{
  "name": "okf",
  "source": {
    "source": "git-subdir",
    "url": "pmmm114/okf-wiki-plugin",
    "path": "plugins/okf",
    "ref": "vX.Y.Z"
  },
  "version": "X.Y.Z"
}
```

엔트리를 **최신 릴리스 태그에 핀**한다 — `source`를 git-subdir(자기 참조)로 바꾸고
`ref=vX.Y.Z`와 `version=X.Y.Z`를 **동기**로 단다. 소비처는 main HEAD가 아니라 그 태그의
**큐레이션된 릴리스**를 받고, 다음 컷에서 version이 바뀔 때만 업데이트된다. 이로써 플러그인
채널이 `actions/validate@vX.Y.Z`·pre-commit `rev: vX.Y.Z`와 **같은 태그-핀 소비 계층**에
합류한다(releasing.md의 태그-핀 철학).

## 왜 이렇게 — 두 가지 함정

- **상대경로 소스에 정적 version을 박으면 자동 업데이트가 동결된다.** 상대경로는 소스가
  main HEAD를 가리키는데, 정적 version은 "안 바뀜"이라 캐시가 갱신되지 않는다. 라벨과 내용이
  어긋난 채 얼어붙는다 — 게이트가 막는 대표 실수다.
- **version만 두고 소스를 안 핀하면 라벨이 내용을 속인다.** 상대경로 소스의 내용은 소비처가
  마켓플레이스를 붙인 ref(=main HEAD)에서 오는데, version은 릴리스 번호를 말한다 → "0.1.0을
  쓰는 중"이라면서 실제론 그 뒤 main을 받는다. 그래서 Phase R은 **version과 함께 source도
  태그에 핀**해 라벨=내용을 보장한다.

핀 형태로 `ref`(가독성)만 두고 `sha`는 생략한다 — 이 repo의 태그는 룰셋으로 **불변**이라
(release-tag.yml) ref만으로 충분하다. 더 강한 고정이 필요하면 `sha`(40자)를 함께 둘 수 있고,
둘 다 있으면 `sha`가 유효 핀이 된다.

## 도구 — 게이트와 릴리스 헬퍼 (기존 버전 도구와 대칭)

버전 시스템의 두 계층을 그대로 미러링한다:

| 계층 | pyproject 버전 | 마켓플레이스 버전 |
| --- | --- | --- |
| 무git CI 게이트(회귀 계약) | `scripts/test_version_sync.py` | `scripts/test_marketplace_version.py` |
| git 도출·검증(릴리스 때) | `scripts/next_version.py` | `scripts/marketplace_version.py` |

- **게이트** `test_marketplace_version.py`는 `pytest scripts`(CI `core` 잡)가 자동 수집한다.
  **내부 정합만**(무git) 본다 — 엔트리가 Phase P/R 중 유효한 형태인지, Phase R이면 `version↔ref`가
  동기이고 clean SemVer이며 url이 자기 참조인지. `plugin.json`의 version 부재도 함께 못박는다.
- **헬퍼** `marketplace_version.py`는 git 태그가 필요한 "최신 태그와 일치"를 릴리스 때 다룬다:

```bash
python3 scripts/marketplace_version.py            # 검증: 최신 태그와 정합 확인(릴리스 후)
python3 scripts/marketplace_version.py 0.1.0      # 도출: 0.1.0으로 핀한 marketplace.json 출력
```

도출은 **제안**이다 — 사람이 검토해 marketplace.json에 기입한다(next_version.py와 같은 원리).

## 릴리스 통합

릴리스 컷 절차([`releasing.md`](releasing.md) §릴리스 컷 절차·체크리스트)에서:

- **릴리스 PR**이 pyproject 실번호 기입과 함께 marketplace.json okf 엔트리를 **새 태그로
  핀**한다(`marketplace_version.py X.Y.Z`로 도출). 첫 릴리스면 Phase P→R 전환이다.
- **태그 생성 후** `marketplace_version.py`(무인자)로 최신 태그와 정합을 확인한다.
- **버전-중립 복귀는 marketplace.json에 적용하지 않는다.** pyproject는 릴리스 직후
  `0.0.0.dev0`으로 되돌리지만(다음 dev를 사전 확정 안 함), marketplace.json은 **"최신 배포
  버전"을 뜻하므로 핀을 유지**한다. 둘은 의미가 다르다(개발 버전 vs 최신 배포).
