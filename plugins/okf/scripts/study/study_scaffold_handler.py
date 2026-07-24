"""writable-vault 스캐폴드 (딸깍 저술 — origin 타깃 핸들러 + 배선).

``/okf-init --vault`` 마법사가 clone은 있으나 writable 셋업(핸들러·capture)이 없을 때
동의를 받아 호출한다. vault repo에 **목적지 무참조**(origin push · 그 repo 안 PR) 핸들러를
깔고 ``study.handlers``·``study.capture``를 배선해 원격 vault 저술을 한 커맨드로 줄인다.

경계·불변식:
- 핸들러는 ``origin``(vault 자기 원격)에 push하고 그 repo 안에서 PR을 연다 — 특정 목적지
  repo명을 하드코딩하지 않는다(무참조 원칙, CLAUDE.md).
- 멱등·비파괴: 이미 있는 핸들러 파일·배선·capture(review/auto)는 덮거나 격하하지 않는다.
- URL vault(관리형 clone)면 여기 커밋이 origin과 diverge하므로 **커밋하지 않고**
  브랜치→PR 절차만 안내한다(로컬 경로 vault는 그냥 커밋). trust는 별도(머신별 보안 동의).

study feature 층 — import는 study→okf_vault 단방향(#145 경계). stdlib 전용, 실패는 관용.
"""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

import okf_vault

DEFAULT_NAME = "kb-pr"
DEFAULT_COMMAND = "scripts/okf-open-pr.py"
CONFIG_NAME = ".okf-wiki.json"

# 스캐폴드가 vault repo에 까는 참조 핸들러(Python). origin에 push하고 그 repo 안에서 PR을
# 열어 특정 목적지 repo명을 하드코딩하지 않는다(무참조). git worktree 격리로 관리형 clone
# 체크아웃을 건드리지 않고, push 성공 후 원 워킹트리의 승격 잔재를 되돌려 clean으로 남긴다
# — 이후 ff 신선도 갱신을 막지 않는다. 바깥 따옴표는 '''…''', 핸들러 docstring은 """…"""로
# 분리해 충돌을 피한다. 이 텍스트가 docs/examples/okf-open-pr.py.example의 정본이다(예시는
# 여기서 생성 — test_handler_template_matches_docs_example가 바이트 동기화를 잠근다).
HANDLER_TEMPLATE = '''#!/usr/bin/env python3
"""참조 study 핸들러 — 승격 개념을 브랜치에 담아 origin에 PR을 연다(목적지 무참조).

소비처가 자기 커밋 경로(예: scripts/okf-open-pr.py)로 두고 쓴다. 목적지는 origin
(vault 자기 원격)이라 특정 repo명을 하드코딩하지 않는다 — 아래 정책 상수만 채운다.

계약(플러그인 → 핸들러):
  stdin : study 아이템 JSON { source, project, concept:{path,type,topic} }
  env   : OKF_CONCEPT_PATH · OKF_CONCEPT_TYPE · OKF_CONCEPT_TOPIC · OKF_PROJECT(대상 repo 루트)
  cwd   : 승격 대상 repo 루트(URL vault면 관리형 clone). 호출자 위치를 가정하지 않는다.
  exit  : 0 성공 / 비0 실패(디스패처가 격리)

관리형 clone 안전: 임시 worktree에서만 커밋·push하고, push 성공 후 원 워킹트리의 승격
잔재를 되돌려 clean으로 남긴다 — 관리형 clone의 체크아웃을 건드리지 않는다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --- 목적지 정책: 소비처가 채우는 유일한 부분 (전부 무참조 기본값) ---------------
BASE = ""            # 비우면 origin/HEAD에서 자동 도출
REVIEWERS = []       # 예: ["octocat", "org/team"] — 빈 리스트면 미지정
LABELS = []          # 예: ["study", "knowledge"]
DRAFT = False        # 초안 PR로 열려면 True
# ----------------------------------------------------------------------------


def _git(args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def _default_base(repo):
    """origin의 기본 브랜치(refs/remotes/origin/HEAD) — 못 구하면 main."""
    try:
        out = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo).stdout.strip()
    except subprocess.CalledProcessError:
        return "main"
    return out.split("/", 1)[1] if "/" in out else (out or "main")


def main():
    sys.stdin.read()  # study 아이템 JSON(계약) — 여기선 env만으로 충분
    concept = os.environ.get("OKF_CONCEPT_PATH")
    if not concept:
        print("OKF_CONCEPT_PATH 필요", file=sys.stderr)
        return 2
    repo = os.environ.get("OKF_PROJECT") or os.getcwd()
    topic = os.environ.get("OKF_CONCEPT_TOPIC") or "uncategorized"
    ctype = os.environ.get("OKF_CONCEPT_TYPE") or "concept"
    slug = Path(concept).stem
    branch = f"study/{topic}/{slug}"
    base = BASE or _default_base(repo)

    # 승격이 남긴 미커밋 변경(개념+log.md+index.md 등) — 파일명을 가정하지 않는다.
    # -u(untracked=all): 새 개념이 신규 디렉터리 안이면 git이 디렉터리 하나로 접는데,
    # 그러면 아래 is_file 가드에 걸려 개념을 통째로 놓친다 — 파일별로 펴서 받는다.
    lines = _git(["status", "--porcelain", "-u"], repo).stdout.splitlines()
    tracked, untracked = [], []
    for line in lines:
        if not line.strip():
            continue
        (untracked if line[:2] == "??" else tracked).append(line[3:])
    changed = tracked + untracked
    if not changed:
        print("승격 산출물 없음(변경 0)", file=sys.stderr)
        return 1

    wt = tempfile.mkdtemp(prefix="okf-pr-")
    try:
        _git(["worktree", "add", "--quiet", "-b", branch, wt, "HEAD"], repo)
        for rel in changed:
            src, dst = Path(repo) / rel, Path(wt) / rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        _git(["add", "-A"], wt)
        _git(["commit", "--quiet", "-m", f"study: promote {ctype} {slug}"], wt)
        _git(["push", "--quiet", "-u", "origin", branch], wt)
        # 승격 잔재는 이제 PR 브랜치(origin)에 있으니 원 clone 워킹트리를 clean으로 되돌린다.
        if tracked:
            _git(["checkout", "--", *tracked], repo, check=False)
        for rel in untracked:
            try:
                (Path(repo) / rel).unlink()
            except OSError:
                pass
        if shutil.which("gh"):
            cmd = ["gh", "pr", "create", "--fill", "--base", base, "--head", branch]
            for reviewer in REVIEWERS:
                cmd += ["--reviewer", reviewer]
            for label in LABELS:
                cmd += ["--label", label]
            if DRAFT:
                cmd.append("--draft")
            subprocess.run(cmd, cwd=wt, check=True)
        else:
            print(f"push 완료: {branch} → origin. PR은 gh 설치 후 또는 API로 연다.")
    finally:
        _git(["worktree", "remove", "--force", wt], repo, check=False)
        shutil.rmtree(wt, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_config(vault: str | Path) -> tuple[dict, Path]:
    """쓰기 전 비관용 읽기 — 깨진 config는 덮지 않게 예외를 던진다."""
    path = Path(vault) / CONFIG_NAME
    if not path.is_file():
        return {}, path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONFIG_NAME} 파싱 실패 — {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_NAME} 최상위가 객체가 아님")
    return data, path


def writable_state(vault: str | Path) -> dict:
    """vault의 writable 준비 상태 — 마법사가 스캐폴드 제안 여부를 기계 판정하는 데 쓴다."""
    config = okf_vault.load_config(vault) or {}
    block = config.get("study")
    study = block if isinstance(block, dict) else {}
    handlers = study.get("handlers") or []
    capture = study.get("capture", "off")
    return {
        "handler_wired": bool(handlers),
        "capture": capture,
        "ready": bool(handlers) and capture in ("review", "auto"),
        "managed": okf_vault.is_managed_clone(vault),
    }


def ensure_handler(vault: str | Path, command: str = DEFAULT_COMMAND) -> str:
    """참조 핸들러를 커밋 경로에 보장한다(비파괴) — 이미 있으면 유지."""
    dst = Path(vault) / command
    if dst.exists():
        return f"{command}: 유지(기존 핸들러)"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(HANDLER_TEMPLATE, encoding="utf-8")
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"{command}: 생성(참조 핸들러)"


def ensure_wiring(
    vault: str | Path,
    name: str = DEFAULT_NAME,
    command: str = DEFAULT_COMMAND,
    level: str = "review",
) -> list[str]:
    """``.okf-wiki.json``에 handlers·capture를 배선한다(멱등·비파괴, 기존 키 보존)."""
    data, path = _read_config(vault)
    block = data.get("study")
    study = block if isinstance(block, dict) else {}
    data["study"] = study
    out: list[str] = []
    handlers = study.get("handlers")
    if not isinstance(handlers, list):
        handlers = []
        study["handlers"] = handlers
    if any(isinstance(h, dict) and h.get("command") == command for h in handlers):
        out.append(f"handlers: 유지({command} 이미 배선)")
    else:
        handlers.append({"name": name, "command": command})
        out.append(f"handlers: 추가({name} → {command})")
    capture = study.get("capture", "off")
    if capture in ("review", "auto"):
        out.append(f"capture: 유지({capture} — 격하 안 함)")
    else:
        study["capture"] = level
        out.append(f"capture: {level}(설정)")
    _write_json(path, data)
    return out


def _guidance(vault: Path, command: str, managed: bool) -> list[str]:
    g: list[str] = []
    if managed:
        g.append("관리형 clone이라 커밋이 origin과 diverge — 브랜치→PR로 반영:")
        g.append(f"  git -C {vault} checkout -b setup/okf-writable")
        g.append(f"  git -C {vault} add {command} {CONFIG_NAME}")
        g.append(f'  git -C {vault} commit -m "okf: writable vault 셋업(핸들러+capture)"')
        g.append(f"  git -C {vault} push -u origin setup/okf-writable   # 이후 PR 생성")
    else:
        g.append("vault repo에 diff 검수 후 커밋:")
        g.append(f"  git -C {vault} add {command} {CONFIG_NAME}")
        g.append(f'  git -C {vault} commit -m "okf: writable vault 셋업(핸들러+capture)"')
        g.append(f"  git -C {vault} push")
    g.append("핸들러의 리뷰어·라벨 정책은 필요하면 스크립트에서 채운다(목적지=origin, 무참조).")
    g.append("실행 승인은 머신별: 반영 후 vault에서 /study --trust.")
    return g


def scaffold(
    vault: str | Path,
    name: str = DEFAULT_NAME,
    command: str = DEFAULT_COMMAND,
    level: str = "review",
) -> dict:
    """핸들러 생성 + study 배선을 멱등 수행하고 수행 상태·안내를 반환한다."""
    vault = Path(vault)
    if not okf_vault.valid_vault(vault):
        return {"ok": False, "reason": "유효 vault 아님(.okf-wiki.json·git 필요)"}
    done = [ensure_handler(vault, command)]
    done += ensure_wiring(vault, name, command, level)
    managed = okf_vault.is_managed_clone(vault)
    return {
        "ok": True,
        "done": done,
        "managed": managed,
        "command": command,
        "guidance": _guidance(vault, command, managed),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="study_scaffold_handler", description="writable-vault 스캐폴드(핸들러+배선)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="vault의 writable 준비 상태(JSON)")
    st.add_argument("vault")
    sc = sub.add_parser("scaffold", help="핸들러 생성 + study 배선(멱등)")
    sc.add_argument("vault")
    sc.add_argument("--name", default=DEFAULT_NAME)
    sc.add_argument("--command", default=DEFAULT_COMMAND)
    sc.add_argument("--level", default="review", choices=["review", "auto"])
    args = ap.parse_args(argv)
    if args.cmd == "status":
        result = writable_state(args.vault)
    else:
        try:
            result = scaffold(args.vault, args.name, args.command, args.level)
        except ValueError as exc:
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
