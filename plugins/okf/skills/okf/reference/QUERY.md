# QUERY — 지식 SQL 레시피 (okf query)

`okf query <번들경로> <SQL|-> [--json]`의 레시피 정본. 새 조회가 필요하면 코드를 짜지 말고 여기에 SQL을 한 줄 더한다 — 이 문서의 모든 ```sql 블록은 게이트가 실제 번들에서 그대로 실행한다(죽은 예시 방지). 따옴표를 품은 SQL은 stdin으로 넣는다.

```bash
okf query <번들경로> - --json <<'SQL'
SELECT path, summary FROM valid ORDER BY path
SQL
```

## 스키마

```
concept(path, dir, type, summary, body, frontmatter_json, conforms)
axis_value(path, axis, value, kind)   -- 축은 행. kind: str|list|date|num
edge(src, dst, via)                   -- via NULL=본문 링크, 아니면 그 축 이름
valid  뷰                             -- conforms=1 (일반 질의는 이것을 쓴다)
wide   뷰                             -- 단일값 축이 컬럼으로 승격(번들에서 귀납)
```

wide의 승격 축은 번들마다 귀납되므로 컬럼을 먼저 확인한다(`PRAGMA`는 차단 — `sqlite_master`로 읽는다).

```sql
SELECT sql FROM sqlite_master WHERE name = 'wide' ORDER BY name
```

## 레시피

**복합 조건** — 두 축을 동시에 만족하는 개념(`axis_value` 자기조인).

```sql
SELECT a.path FROM axis_value a
JOIN axis_value b ON b.path = a.path AND b.axis = 'layer' AND b.value = 'wisdom'
WHERE a.axis = 'tags' AND a.value = 'python'
ORDER BY a.path
```

**부정** — 특정 태그가 없는 개념.

```sql
SELECT v.path FROM valid v
WHERE NOT EXISTS (
  SELECT 1 FROM axis_value a
  WHERE a.path = v.path AND a.axis = 'tags' AND a.value = 'python'
)
ORDER BY v.path
```

**범위·정렬·절단** — 날짜 값은 isoformat 표기다(`+00:00`, `Z` 아님). 코드포인트순 정렬이 시간순과 일치하는 것은 **동일 오프셋 전제**(오프셋이 섞인 축은 보장 없음). 절단은 엔진이 아니라 이 `LIMIT`의 몫이다.

```sql
SELECT a.path, a.value FROM axis_value a
WHERE a.axis = 'timestamp' AND a.value >= '2026-01-01'
ORDER BY a.value DESC, a.path
LIMIT 10
```

**교차표** — 축 × 축 분포.

```sql
SELECT l.value AS layer, t.value AS type, count(*) AS n
FROM axis_value l
JOIN axis_value t ON t.path = l.path AND t.axis = 'type'
WHERE l.axis = 'layer'
GROUP BY l.value, t.value
ORDER BY l.value, t.value
```

**축 카디널리티** — 묶는 축(값이 수렴)과 좁히는 축(값이 개념 수에 근접)을 가르는 재료. 숫자 축(`kind='num'`)을 크기순으로 다루려면 `CAST(value AS INTEGER)` — 문자열 정렬은 `'9' > '10'`이다.

```sql
SELECT axis, count(DISTINCT path) AS docs, count(DISTINCT value) AS vals
FROM axis_value
GROUP BY axis
ORDER BY axis
```

**정초 사슬 추적** — 타입 엣지(`via`가 축 이름)를 재귀 CTE로 하향 순회.

```sql
WITH RECURSIVE chain(path, depth) AS (
  SELECT dst, 1 FROM edge WHERE src = 'cluster/a.md' AND via IS NOT NULL
  UNION
  SELECT e.dst, c.depth + 1 FROM edge e JOIN chain c ON e.src = c.path
  WHERE e.via IS NOT NULL AND c.depth < 10
)
SELECT path, depth FROM chain ORDER BY depth, path
```

**역링크** — 이 개념을 참조하는 개념(요약 동봉).

```sql
SELECT e.src, e.via, c.summary
FROM edge e JOIN valid c ON c.path = e.src
WHERE e.dst = 'cluster/a.md'
ORDER BY e.src, COALESCE(e.via, '')
```

**본문 검색** — 전량 스캔 `LIKE`(실측 0.11ms/186KB — 색인 불요, 조사가 붙어도 잡힌다).

```sql
SELECT path, summary FROM valid
WHERE body LIKE '%링크%'
ORDER BY path
```

**규격 미달 진단** — §9 탈락 문서(일반 질의 우주 밖)를 들여다볼 때만 `concept`을 직접 쓴다.

```sql
SELECT path, type, summary FROM concept
WHERE conforms = 0
ORDER BY path
```

**dangling 파생 대상** — `derived_from` 기재 수와 해소된 엣지 수의 불일치 목록. 오탈자인지 의도적 미작성("미작성 지식 신호")인지의 판정은 #328 소관 — 이 질의는 재료만 낸다.

```sql
SELECT a.path, count(*) AS declared,
       (SELECT count(*) FROM edge e WHERE e.src = a.path AND e.via = a.axis) AS resolved
FROM axis_value a
WHERE a.axis = 'derived_from'
GROUP BY a.path
HAVING declared > resolved
ORDER BY a.path
```

## 판정 금지 경계

query는 census와 같은 **재료 제공자**다. SQL은 `ORDER BY`·`HAVING`으로 순위·임계값을 만들기 쉬워 이 규율을 위반하기 쉽다 — 레시피가 내는 것은 판단의 재료이지 판단이 아니다.

| | 예 |
| --- | --- |
| 허용 | `layer='wisdom'`인 개념 목록 · 층별 개수 · 미접지 개념 목록 · 참조 유입 계수 |
| 금지 | "이 개념은 wisdom으로 승격해야 함" (판정) |
| 금지 | `HAVING count(*) < 3` 로 "고립 개념" 딱지 (임계값) |
| 금지 | 승격 파이프라인의 게이트 입력으로 쓰는 것 (자문의 판정 승격 — 배선 게이트가 차단) |

## 관례

- 모든 레시피에 `ORDER BY`를 명시한다 — 인덱스 유무만으로 순서가 반전된다(게이트: 이 문서의 sql 블록 전수 검사)
- 엔진은 절단하지 않는다 — 결과가 크면 소비자가 `LIMIT`을 쓴다
- 쓰기·`ATTACH`·`PRAGMA`는 차단돼 있다(읽기 전용 봉인) — 스키마 탐색은 `sqlite_master`로
- 다중값 축(tags류)의 그룹핑·필터는 `axis_value` 조인이 정답 경로다 — `okf context --group-by`는 다중값 축을 묶지 않는다
