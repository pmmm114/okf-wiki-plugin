"""플러그인 테스트 공통 — `scripts/` import 경로 추가 + 홈 스코프 격리.

`plugins/okf/scripts/`는 패키지가 아니라 훅·스캐폴드 스크립트 모음이므로,
테스트가 모듈로 직접 import할 수 있게 sys.path 선두에 넣는다.

테스트는 "깨끗한 tmp 프로젝트"를 가정하는데, 실행 머신에 홈 포인터
(`~/.claude/okf/home-project`)나 유저 스코프 런타임(`~/.claude/okf/study`)이
실존하면 okf_home의 스코프 해소 경유로 그 상태가 새어 들어와 결과가 머신마다
달라진다(#156). 세션 전체에서 HOME을 빈 tmp로 고정하고 스코프 해소 환경변수를
제거해 hermetic을 보장한다 — subprocess 스폰 테스트(test_hook_parity 등)도
os.environ 복사로 spawn하므로 같은 격리를 물려받는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/는 core/(plugin-core)·study/(feature)로 물리 분리돼 있다(#145 U5) —
# 런타임 spawn은 bin/okf-py가 PYTHONPATH로 같은 두 경로를 노출한다.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS / "study"))
sys.path.insert(0, str(_SCRIPTS / "core"))

# 스코프 해소에 관여하는 환경변수 — 홈 포인터 오버라이드 · 설정 디렉토리 ·
# 프로젝트 폴백 · doctor 자동메모리 판정. 필요한 테스트는 명시적으로 다시 설정한다.
_SCOPE_ENV_VARS = (
    "OKF_HOME_PROJECT",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_home_scope(tmp_path_factory: pytest.TempPathFactory):
    mp = pytest.MonkeyPatch()
    mp.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    for name in _SCOPE_ENV_VARS:
        mp.delenv(name, raising=False)
    yield
    mp.undo()
