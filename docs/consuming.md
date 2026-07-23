# 소비 repo에서 번들 검증하기 (CI · pre-commit)

OKF 번들을 쓰는 repo는 엔진과 똑같은 §9 컨포먼스 검사를 배포면에 걸 수 있다.
검증을 CI와 커밋 단계로 당겨 두면, 형식이 어긋난 번들이 조용히 병합되는 것을 막는다.

## GitHub Actions

이 repo가 제공하는 composite action(`actions/validate`)을 그대로 가져다 쓴다.

```yaml
steps:
  - uses: actions/checkout@<SHA>
  - uses: pmmm114/okf-wiki-plugin/actions/validate@<v태그>
    with: { path: .okf, strict: true }
```

- `path` — 검증할 번들 경로(기본값 `.okf`).
- `strict` — 권장 필드 위반까지 error로 올리는 `--strict` 모드.

## pre-commit

커밋할 때마다 로컬에서 같은 검사를 돌리려면 pre-commit 훅을 건다.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pmmm114/okf-wiki-plugin
    rev: <v태그>
    hooks:
      - id: okf-validate
```

두 방식 모두 속으로는 엔진의 `okf validate`를 부른다. 종료코드와 검사 규칙이
같으니, CI에서 통과한 번들은 pre-commit에서도 통과한다.

엔진 CLI를 직접 부르는 방법과 종료코드는 [CONTRIBUTING.md](../CONTRIBUTING.md)에
정리되어 있다.
