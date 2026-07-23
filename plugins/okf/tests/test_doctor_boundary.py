"""okf_doctor core⊥study 경계 — study 부재 생존 게이트 (#145 U4).

doctor는 study 진단을 try-import 심 1개로 선택 위임한다("있으면 실행, 없으면
생략"). okf_* 파일만 배치된 환경에서 import·실행이 생존하고 core 섹션(위치·주입·
홈)만 출력하는지 subprocess로 고정한다 — U1 이전엔 이 시나리오가 study_inbox
경유 ModuleNotFoundError로 죽었다(#145 사전 검증 실증).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS_CORE = PLUGIN / "scripts" / "core"
SCRIPTS_STUDY = PLUGIN / "scripts" / "study"
CORE_ONLY = ["okf_doctor.py", "okf_home.py"]


def _src(name: str) -> Path:
    # core/study 물리 분리(#145 U5) — 파일명 접두사로 원본 디렉토리를 찾는다
    return (SCRIPTS_STUDY if name.startswith("study") else SCRIPTS_CORE) / name


def _run_doctor_with(tmp_path, files, *, with_home_pointer=False):
    scripts = tmp_path / "partial-deploy"
    scripts.mkdir()
    for name in files:
        shutil.copy2(_src(name), scripts / name)
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(tmp_path / "isolated-home")}
    env.pop("OKF_HOME_PROJECT", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if with_home_pointer:
        home = tmp_path / "home-kb"
        (home / ".git").mkdir(parents=True)
        (home / ".okf-wiki.json").write_text("{}", encoding="utf-8")
        env["OKF_HOME_PROJECT"] = str(home)
    return subprocess.run(
        [sys.executable, str(scripts / "okf_doctor.py"), str(project)],
        capture_output=True,
        env=env,
        timeout=60,
    )


def test_doctor_survives_without_study_modules(tmp_path):
    res = _run_doctor_with(tmp_path, CORE_ONLY)
    assert res.returncode == 0, res.stderr
    out = res.stdout.decode("utf-8")
    for core_section in ("[위치]", "[주입]", "[홈]"):
        assert core_section in out
    # study 섹션은 심 부재로 전부 생략 — 캡처 트레이스·입구·스토어·inbox·이력·회복
    for study_section in ("[캡처]", "[캡처 입구]", "[스토어]", "[inbox]", "[최근 이력]", "[회복]"):
        assert study_section not in out


def test_doctor_core_home_notes_without_study(tmp_path):
    # 유효 홈이면 generic 홈 메모(포인터·번들 부합)는 study 없이도 나온다
    res = _run_doctor_with(tmp_path, CORE_ONLY, with_home_pointer=True)
    assert res.returncode == 0, res.stderr
    out = res.stdout.decode("utf-8")
    assert "(유효)" in out
    assert "부합" in out  # 번들 부재 경고까지 generic 소관
    assert "캡처 활성 제안" not in out  # study 관점 메모는 심 소관


def test_doctor_partial_deployment_names_missing_module(tmp_path):
    # 심(study_doctor.py)은 있으나 연쇄 모듈이 결손인 부분 배치 — 조용히 '미배치'로
    # 위장하지 않고 stderr에 결손 모듈명을 남긴다(#166 리뷰: 진단 도구의 은폐 금지).
    res = _run_doctor_with(tmp_path, [*CORE_ONLY, "study_doctor.py"])
    assert res.returncode == 0, res.stderr
    out = res.stdout.decode("utf-8")
    assert "[위치]" in out and "[캡처]" not in out  # core-only 저하는 유지
    err = res.stderr.decode("utf-8")
    assert "모듈 결손(study" in err  # study_doctor의 첫 결손 연쇄 import 이름 노출


def test_doctor_full_sections_with_study_present(tmp_path):
    # 정상 배치에서는 심이 로드되어 study 섹션이 전부 출력된다 — 실배선과 동일하게
    # bin/okf-py 셔틀 경유(셔틀이 core/·study/를 PYTHONPATH로 노출, #145 U5)
    project = tmp_path / "proj"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path / "isolated-home"), "OKF_PYTHON": sys.executable}
    env.pop("OKF_HOME_PROJECT", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    res = subprocess.run(
        [str(PLUGIN / "bin" / "okf-py"), str(SCRIPTS_CORE / "okf_doctor.py"), str(project)],
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.decode("utf-8")
    for section in ("[위치]", "[캡처]", "[주입]", "[홈]", "[캡처 입구]", "[스토어]", "[inbox]"):
        assert section in out
