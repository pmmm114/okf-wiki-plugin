"""okf_doctor core⊥study 경계 — study 부재 생존 게이트 (#145 U4).

doctor는 study 진단을 try-import 심 1개로 선택 위임한다("있으면 실행, 없으면
생략"). okf_* 파일만 배치된 환경에서 import·실행이 생존하고 core 섹션(위치·주입·
vault)만 출력하는지 subprocess로 고정한다 — U1 이전엔 이 시나리오가 study_inbox
경유 ModuleNotFoundError로 죽었다(#145 사전 검증 실증).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import okf_doctor

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS_CORE = PLUGIN / "scripts" / "core"
SCRIPTS_STUDY = PLUGIN / "scripts" / "study"
# okf_remote는 core 인프라(URL 모드 관리형 clone, #153) — core-only 배치에도 함께 온다.
CORE_ONLY = ["okf_doctor.py", "okf_vault.py", "okf_remote.py"]


def _src(name: str) -> Path:
    # core/study 물리 분리(#145 U5) — 파일명 접두사로 원본 디렉토리를 찾는다
    return (SCRIPTS_STUDY if name.startswith("study") else SCRIPTS_CORE) / name


def _run_doctor_with(tmp_path, files, *, with_vault_pointer=False):
    scripts = tmp_path / "partial-deploy"
    scripts.mkdir()
    for name in files:
        shutil.copy2(_src(name), scripts / name)
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(tmp_path / "isolated-vault")}
    env.pop("OKF_HOME_PROJECT", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if with_vault_pointer:
        vault = tmp_path / "vault-kb"
        (vault / ".git").mkdir(parents=True)
        (vault / ".okf-wiki.json").write_text("{}", encoding="utf-8")
        env["OKF_HOME_PROJECT"] = str(vault)
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
    for core_section in ("[위치]", "[주입]", "[Vault]"):
        assert core_section in out
    # study 섹션은 심 부재로 전부 생략 — 캡처 트레이스·입구·스토어·inbox·이력·회복
    for study_section in ("[캡처]", "[캡처 입구]", "[스토어]", "[inbox]", "[최근 이력]", "[회복]"):
        assert study_section not in out


def test_doctor_core_vault_notes_without_study(tmp_path):
    # 유효 vault이면 generic vault 메모(포인터·번들 부합)는 study 없이도 나온다
    res = _run_doctor_with(tmp_path, CORE_ONLY, with_vault_pointer=True)
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


def test_doctor_url_pointer_shows_managed_clone_notes(tmp_path):
    # #153: URL 포인터면 [Vault] 섹션에 관리형 clone 상태(모드·미생성)를 무네트워크로 표기한다.
    scripts = tmp_path / "url-deploy"
    scripts.mkdir()
    for name in CORE_ONLY:
        shutil.copy2(_src(name), scripts / name)
    project = tmp_path / "proj2"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path / "isolated-home2")}
    env.pop("CLAUDE_CONFIG_DIR", None)
    env["OKF_HOME_PROJECT"] = "git@example.com:o/r.git"  # clone 미생성 URL 포인터
    out = subprocess.run(
        [sys.executable, str(scripts / "okf_doctor.py"), str(project)],
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    text = out.stdout.decode("utf-8")
    assert "URL(관리형 clone)" in text and "미생성" in text


def test_doctor_full_sections_with_study_present(tmp_path):
    # 정상 배치에서는 심이 로드되어 study 섹션이 전부 출력된다 — 실배선과 동일하게
    # bin/okf-py 셔틀 경유(셔틀이 core/·study/를 PYTHONPATH로 노출, #145 U5)
    project = tmp_path / "proj"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path / "isolated-vault"), "OKF_PYTHON": sys.executable}
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
    for section in ("[위치]", "[캡처]", "[주입]", "[Vault]", "[캡처 입구]", "[스토어]", "[inbox]"):
        assert section in out


# --- 실행 전제 진단 (#299) ------------------------------------------------------
#
# 훅·커맨드는 `bin/okf`(uv 경유 엔진)와 `bin/okf-py`(인터프리터 셔틀)를 통해서만
# 동작한다. 그 전제가 깨지면(uv 부재·venv 손상) 엔진 호출이 `except OSError: return None`
# 으로 흡수되어 **"설정이 없다"와 구분되지 않는다** — 사용자가 보는 것은 양쪽 다 무출력이다.
# doctor가 그 전제를 직접 점검하지 않으면 진단할 방법이 없다(전문에 uv 참조 0건이었다).


def test_doctor_reports_execution_prerequisites(tmp_path):
    """doctor 출력에 실행 전제 절이 있다."""
    out = okf_doctor.run(str(tmp_path))
    assert "[실행 전제]" in out, out


def test_doctor_names_missing_uv(tmp_path, monkeypatch):
    """`uv`가 안 보이면 그 사실을 말한다 — 무음이면 '설정 없음'과 구분되지 않는다."""
    monkeypatch.setattr(okf_doctor.shutil, "which", lambda name: None)
    out = okf_doctor.run(str(tmp_path))
    assert "uv" in out
    assert "⚠" in out.split("[실행 전제]", 1)[1].split("[", 1)[0]


def test_doctor_reports_shuttle_smoke(tmp_path, monkeypatch):
    """셔틀 스모크 실패를 별도 사실로 보고한다(uv 유무와 별개 축)."""
    monkeypatch.setattr(okf_doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(okf_doctor, "_smoke_okf", lambda: (False, "rc=127"))
    section = okf_doctor.run(str(tmp_path)).split("[실행 전제]", 1)[1].split("[", 1)[0]
    assert "rc=127" in section, section
