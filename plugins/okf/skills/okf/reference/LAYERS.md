# 인식층 — 정보·지식·지혜 (layer 축 단일 원천)

개념의 **인식층**(epistemic layer)과 **정초·출처 사슬**의 정본이다. `type`(무엇의
개념인가)과 **직교**하는 축으로, 개념이 담은 **인식 고도**를 표시한다. 어휘·정초 순서의
단일 원천은 이 문서다 — 훅·헬퍼·린트는 값을 하드코딩하지 말고 아래 [기계 판독
단일 원천](#기계-판독-단일-원천)을 읽는다.

이 축은 **선택(opt-in)** 이다. `layer`가 없는 개념도 컨포먼트이고 정상 조회된다 —
OKF 스펙은 미지·미기재 필드를 거부하지 않는다(§4.1·§9).

## 1. 세 인식층

| 층 | 정의 | 토마토 예시 | OKF 개념에서의 실체 | 본문 성격 |
|---|---|---|---|---|
| **정보** `information` | 객관적 사실 데이터에 **목적을 더해 가공**한 것 | "토마토는 과일이다"라는 사실을 **아는 것** | 단일 사실·값·스키마 행 — **답 그 자체** | 표·컬럼·수치 |
| **지식** `knowledge` | 이를 **체계적으로 내재화**한 상태(이해) | 토마토가 식물학적으로 과일임을 **이해**하는 것 | 관계·구조·모델 — **왜/어떻게 연결되는가** | 링크·조인·구조 서술 |
| **지혜** `wisdom` | 지식 위에서 **통찰을 얻어 상황에 맞게 판단·적용**하는 능력 | 과일이지만 맛의 조화를 고려해 **과일 샐러드엔 넣지 않는 것** | 판단·결정·규약·플레이북 — **언제/언제 하지 말 것** | 조건→행동, 안티패턴 |

**두 가지 성질**이 이 축의 사용을 규정한다:

- **`type`과 직교하고 누적적** — 한 주제가 세 층을 동시에 가질 수 있다. `layer`는 파일을
  종류로 가르는 분할이 아니라, 같은 대상에 대한 인식 고도다. 그래서 `BigQuery
  Table`(type)이 `information`(layer)일 수도, `Playbook`(type)이 `wisdom`(layer)일 수도 있다.
- **정초 관계로 쌓인다 — 지혜←지식←정보** ([§2](#2-정초출처-사슬)).

## 2. 정초·출처 사슬

지혜는 지식을, 지식은 정보를 **토대로** 만들어진다. 각 개념은 **무엇을 토대로 했는지의
연결점**(내부 파생)을, 그리고 정보(및 상위 층)는 **외부 출처**를 가져, 사슬 전체가
객관적 실재까지 추적된다.

```
지혜(wisdom) ──derived_from──▶ 지식(knowledge) ──derived_from──▶ 정보(information)
                 │                     │                     │
                 └──── resource / # Citations (외부 출처) ─────┴──▶ 객관적 실재
```

이 사슬이 **"AI가 만들어내고 추론하지 않았다는 근거 및 자료"** 다. 어떤 판단(지혜)이든
사슬을 따라 내려가 **출처 있는 사실(정보)** 까지 감사할 수 있어야 하며, 근거 없이 떠 있는
개념은 접지 신호로 잡는다([§4](#4-정초출처-불변식)).

## 3. 필드

### `layer` — 인식층 (선택)

frontmatter에 `layer: information | knowledge | wisdom`. 미기재는 "미분류"로 취급.

### `derived_from` — 내부 파생(연결점, 선택)

**정초 타입 엣지 전용** frontmatter 필드. 이 개념이 토대로 삼은 **하위 층 개념들의 번들
상대 경로 리스트**다.

```yaml
derived_from:
  - /produce/fruit-vs-culinary.md      # 지식
  - /produce/tomato-classification.md  # 정보
```

- 왜 frontmatter인가 — OKF §5.3은 본문 크로스링크를 **무타입**("관계의 종류는 링크가
  아니라 주변 산문이 전달")으로 규정한다. 정초 순서 검증·근거 사슬 순회는 *기계가 읽는
  타입 엣지*를 요구하므로, **파생 관계만** 산문에서 들어올려 frontmatter 리스트로 둔다.
  일반 "관련/참조"는 그대로 본문 §5 링크로 남긴다.
- 경로는 **절대(번들 상대) 형식**(`/`로 시작, §5.1)을 권장 — 문서 이동에 강하다.
- 지혜는 지식·정보 **양쪽**에서 직접 파생할 수 있다. 동일 층 개념 사이의 관계는
  `derived_from`이 아니라 일반 본문 링크로 표현한다(정초는 엄격 하향).

### 외부 출처 — 기존 OKF 재사용 (신규 필드 없음)

- `resource`(§4.1) — 개념이 서술하는 자산의 정규 URI.
- `# Citations`(§8) — 본문 주장을 뒷받침하는 외부 자료 목록.

출처는 사슬의 뿌리다. **정보 층은 반드시 출처(`resource` 또는 `# Citations`)를 가진다** —
이것이 객관성·무결성의 근거이자 "AI 비-생성"의 증거다.

## 4. 정초·출처 불변식

접지 린트 `scripts/core/okf_layers.py`(layer-aware, 플러그인측)가 아래를 **warn**으로
신호한다. OKF §9 컨포먼스 error로 승격하지 않는다(스펙 관용 — 미지·미기재 필드 거부
금지). 어휘·순서·규칙은 위 [기계 판독 단일 원천](#기계-판독-단일-원천)에서 데이터로
로드한다(하드코딩 없음). 층·파생·출처 데이터는 엔진 출력(`okf context --group-by`·
`okf graph --edges-from`)에서 소비한다.

1. **정초 순서** — `derived_from` 대상은 **출처 개념보다 엄격히 낮은 층**이어야 한다
   (지혜→{지식,정보}, 지식→{정보}). 상위·역방향 파생(예: 정보가 지혜에서 파생)은 사다리
   위반이다.
2. **접지(출처)** — 정보 층은 `resource`를 가져야 하고, 상위 층(지식·지혜)은 비어있지
   않은 유효 `derived_from`을 가져야 한다(떠 있는 판단 방지). *린트는 `resource`만 확인한다
   — `# Citations`만으로 접지한 정보 개념은 아직 감지하지 못한다(엔진이 §8을 표면화하면 보강).*
3. **깨진 파생** — `derived_from` 대상이 번들에 없으면 유효 근거 엣지가 안 생겨 그 상위
   개념이 2번 "미접지"로 잡힌다(§5.3 미작성 지식 신호).

실행: `bin/okf-py scripts/core/okf_layers.py <번들> [--strict]`. 기본은 자문(exit 0),
`--strict`면 발견 시 exit 1.

## 5. 저작 가이드

- 개념을 쓸 때 담은 인식 고도에 맞춰 `layer`를 (선택) 부여한다 — 사실이면 `information`,
  이해·구조면 `knowledge`, 판단·규약·안티패턴이면 `wisdom`.
- 상위 층이면 그 **근거가 되는 하위 개념을 `derived_from`으로 연결**한다. 근거 개념이 아직
  없으면 먼저 그 정보/지식 개념을 쓰고 링크한다(깨진 링크는 미작성 지식 신호).
- **정보 개념에는 출처**(`resource` 또는 `# Citations`)를 반드시 매단다.

## 6. 소비 가이드

- 작업 성격대로 층을 골라 당긴다 — **판단이 필요하면 지혜**, **이해가 필요하면 지식**,
  **사실값이 필요하면 정보**.
- 판단(지혜)을 근거로 쓸 때는 **그 근거 사슬**(`derived_from` → 지식·정보 → 출처)까지 함께
  확인해, 근거 있는 판단인지 감사한다.
- 주입 컨텍스트가 층으로 구분돼 오면(`context.groupBy`) 이 선택이 값싸진다.

## 7. 예시 — 토마토 사슬

세 파일이 하나의 정초 사슬을 이룬다.

`/produce/tomato-classification.md` (정보 — 출처를 매단 사실):
```markdown
---
type: Fact
layer: information
description: 토마토는 식물학적으로 과일(열매)로 분류된다.
resource: https://en.wikipedia.org/wiki/Tomato
---

# 분류

토마토는 씨방이 성숙한 열매이므로 식물학적으로 **과일**이다.

# Citations

[1] [Tomato — botanical classification](https://en.wikipedia.org/wiki/Tomato)
```

`/produce/fruit-vs-culinary.md` (지식 — 정보를 토대로 한 이해):
```markdown
---
type: Model
layer: knowledge
description: 식물학적 분류와 요리적 분류는 서로 다른 기준을 쓴다.
derived_from:
  - /produce/tomato-classification.md
---

# 두 분류 기준

* 식물학: 씨방·씨앗 유무 → 토마토는 과일.
* 요리: 단맛·용도 → 토마토는 채소처럼 쓰인다.
```

`/produce/fruit-salad-guideline.md` (지혜 — 지식·정보 위의 판단):
```markdown
---
type: Convention
layer: wisdom
description: 토마토는 과일이지만 과일 샐러드에는 넣지 않는다.
derived_from:
  - /produce/fruit-vs-culinary.md
  - /produce/tomato-classification.md
---

# 지침

토마토는 식물학적으로 과일이나, 단맛이 약해 **과일 샐러드의 맛 조화를 해친다**.
→ 과일 샐러드에는 넣지 않는다.
```

## 기계 판독 단일 원천

어휘·정초 순서의 **정본**이다. 훅·헬퍼·접지 린트는 값을 하드코딩하지 말고 이 블록을
읽는다(소비처가 데이터로 필요하면 이 블록을 형제 데이터 파일로 들어낼 수 있다 — 이
문서가 여전히 단일 원천). 플러그인 스크립트는 stdlib `json`으로 파싱한다.

```json
{
  "field": "layer",
  "values": ["information", "knowledge", "wisdom"],
  "order": ["information", "knowledge", "wisdom"],
  "derivation_field": "derived_from",
  "source_fields": ["resource", "citations"],
  "rules": {
    "derivation_strictly_downward": true,
    "information_requires_source": true,
    "upper_requires_derived_from": true
  }
}
```

`order`는 낮은 층(더 정초적)에서 높은 층 순이다 — `derived_from` 대상의 `order` 인덱스는
출처 개념의 인덱스보다 **작아야**(엄격히 하위) 한다.
