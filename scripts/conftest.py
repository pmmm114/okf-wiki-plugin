"""scripts 테스트의 git 환경 격리.

git은 환경에 ``GIT_DIR``·``GIT_WORK_TREE``·``GIT_INDEX_FILE``이 있으면 ``cwd``와
``-C``를 **무시하고** 그쪽을 대상으로 동작한다. 그런데 git 훅은 바로 그 변수들을
설정한 채 자식 프로세스를 띄우고, lefthook `pre-push`가 여기 테스트를 돌린다
(`docs/branching.md` 로컬 훅 표). 격리하지 않으면 tmp에 세우려던 픽스처 repo가
**훅을 띄운 실제 repo**에 세워진다 — 실측 신호는 `warning: re-init: ignored
--initial-branch=main`이고, 실제 피해는 `.git/config`에 픽스처 identity와
``core.bare=true``가 주입되고 그 뒤 커밋들의 author가 오염된 것이었다.

격리를 호출부(각 subprocess에 `env=`)가 아니라 여기 autouse fixture에 두는 이유는
**누락이 곧 사고**이기 때문이다. 새 테스트가 git을 부를 때마다 규율을 지켜야 하는
설계보다, 한 곳에서 강제하고 회귀를 게이트로 고정하는 쪽이 싸다
(게이트: `test_branch_policy.py::test_git_env_is_isolated` ·
`::test_repo_fixture_stays_in_tmp`).
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_git_env(monkeypatch):
    """모든 `scripts` 테스트에서 상속된 ``GIT_*``를 벗긴다(monkeypatch가 자동 복원).

    테스트가 특정 ``GIT_*``를 의도적으로 쓰려면 자기 몸통에서 `setenv`하면 된다 —
    autouse fixture가 먼저 돌므로 그쪽이 이긴다.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
