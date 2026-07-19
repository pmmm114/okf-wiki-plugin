# okf-wiki-plugin

[OKF(Open Knowledge Format) v0.1](okf-core/vendor/spec/SPEC.md) 지식 번들을
만들고, 검증하고, Claude Code 세션에 주입하는 도구 모음입니다.

> **비공식 고지** — 이 프로젝트는 Google과 무관한 비공식 도구이며, OKF
> 스펙 원문은 Apache-2.0으로 무수정 벤더링했습니다
> ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

| 구성 | 경로 | 역할 |
| --- | --- | --- |
| 엔진 | `okf-core/` | 파서·§9 검사기·index/graph/context 생성기 + `okf` CLI |
| 플러그인 | `plugins/okf/` | Claude Code 플러그인 — 스킬 + 세션 컨텍스트 주입 훅 |
| CI 소비면 | `actions/validate/` | 소비 repo용 composite action |
| pre-commit | `.pre-commit-hooks.yaml` | 소비 repo용 pre-commit 훅 정의 |

## 빠른 시작 — 번들 검증

```bash
# uv 사용 (권장)
uv run --project okf-core okf validate <번들경로> --strict

# 또는 pip
pip install ./okf-core   # repo 루트 설치도 동작: pip install .
okf validate <번들경로> --strict
```

종료코드: `0` 컨포먼트 / `1` 비컨포먼트 / `2` 실행 오류. `--format json`은
발견 1건당 `{"file","rule","level","msg"}` 객체를 출력합니다.

`okf` CLI 서브커맨드 6종:

```
okf validate <path> [--strict] [--format json]   # §9 컨포먼스 검사
okf index    <path> [--write]                    # §6 형식 index.md 재생성
okf graph    <path> --json [--linked-to P]       # 링크 그래프·역링크 조회
okf context  <path> [--max-chars N]              # 주입용 압축 인덱스
okf log      append <path> -m MSG                # log.md 항목 추가(§7)
okf init     <dir>                               # §9 컨포먼트 최소 번들 스캐폴드
```

## Claude Code 플러그인 설치

```
/plugin marketplace add pmmm114/okf-wiki-plugin
/plugin install okf@okf-wiki-plugin
```

번들을 쓰는 repo 루트에 `.okf-wiki.json`을 두면 세션 시작 시 번들 압축
컨텍스트(`<okf-context>`)가 자동 주입됩니다:

```json
{
  "bundlePath": ".okf",
  "context": { "maxChars": 8000 },
  "inject": true
}
```

전체 스키마: [plugins/okf/skills/okf/reference/CONFIG.md](plugins/okf/skills/okf/reference/CONFIG.md)

## CI에서 번들 검증 (소비 repo)

```yaml
steps:
  - uses: actions/checkout@<SHA>
  - uses: pmmm114/okf-wiki-plugin/actions/validate@<v태그>
    with: { path: .okf, strict: true }
```

pre-commit으로도 같은 검사를 걸 수 있습니다:

```yaml
repos:
  - repo: https://github.com/pmmm114/okf-wiki-plugin
    rev: <v태그>
    hooks:
      - id: okf-validate
```

## 개발

```bash
uv run --with pytest --with pyyaml python -m pytest okf-core/tests -q  # 테스트
uvx ruff check . && uvx ruff format --check .                          # 린트·포맷
```

PR은 CI(`core` 잡: 린트 → 빌드 → 테스트 → 픽스처 스냅샷 → 오라클 차동 →
벤더 동기화 → 라이선스 → 플러그인 검증) 녹색 후 머지합니다. 브랜치·커밋·머지
전략은 [docs/branching.md](docs/branching.md), 배포·버전관리 전략은
[docs/releasing.md](docs/releasing.md), 에이전트 작업 규칙은
[CLAUDE.md](CLAUDE.md), 벤더 반입 원칙은 [.okf/](.okf/) 자기 번들 참조.

## 라이선스

MIT ([LICENSE](LICENSE)). 벤더 반입물의 출처·라이선스는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 고지되어 있습니다.
