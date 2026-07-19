"""플러그인 테스트 공통 — `scripts/`를 import 경로에 추가한다.

`plugins/okf/scripts/`는 패키지가 아니라 훅·스캐폴드 스크립트 모음이므로,
테스트가 모듈로 직접 import할 수 있게 sys.path 선두에 넣는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
