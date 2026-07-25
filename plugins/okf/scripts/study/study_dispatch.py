"""study 디스패처 코어 (S3, #75).

검증된 개념을 담은 study 아이템을 소비처가 주입한 핸들러 배열로 흘려보낸다.
각 핸들러는 stdin으로 아이템(JSON)을 받고, 트리거·개념 정보를 env var로도 받는다:
``OKF_TRIGGER``(memory|manual)·``OKF_CONCEPT_TYPE``·``OKF_CONCEPT_TOPIC``·``OKF_CONCEPT_PATH``·
``OKF_CONCEPT_LAYER``(인식층 정보/지식/지혜, #189 U5).

실행 전 두 관문을 통과해야 한다:

1. **경로 검사** — ``command``는 repo 트리 안으로 정규화돼야 하고(심링크·``..`` 탈출
   거부), **git 추적** 상태여야 한다(미추적 거부, fail-closed).
2. **trust 게이트** — ``trust_check(name, path)`` 훅 지점(S4에서 구현). 미승인이면 보류.

한 핸들러의 실패·거부가 나머지를 막지 않는다(실패 격리). 이 모듈은 디스패치를
스스로 트리거하지 않는 **라이브러리**이며, 안전 기본값 없이 실행하지 않도록
CLI 진입점을 두지 않는다 — 호출자(S5 스킬)가 실제 ``trust_check``를 넘긴다.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import okf_remote
import okf_vault


class CommandError(ValueError):
    """핸들러 command가 경로/추적 검사를 통과하지 못함."""


def resolve_command(project: str | Path, command: str) -> Path:
    """command를 repo 트리 안 절대경로로 정규화한다. 밖이면 CommandError."""
    root = Path(project).resolve()
    target = (root / command).resolve()  # 심링크·`..`까지 해소
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CommandError(f"repo 트리 밖 경로 거부: {command}") from exc
    return target


def is_git_tracked(project: str | Path, path: str | Path) -> bool:
    """path가 git 추적(커밋) 대상인지 여부."""
    root = Path(project).resolve()
    rel = os.path.relpath(Path(path).resolve(), root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=root,
        capture_output=True,
    )
    return result.returncode == 0


def _handler_env(item: dict) -> dict:
    concept = item.get("concept") or {}
    env = dict(os.environ)
    env["OKF_TRIGGER"] = str(item.get("source", ""))
    env["OKF_CONCEPT_TYPE"] = str(concept.get("type", ""))
    env["OKF_CONCEPT_TOPIC"] = str(concept.get("topic", ""))
    env["OKF_CONCEPT_PATH"] = str(concept.get("path", ""))
    env["OKF_CONCEPT_LAYER"] = str(concept.get("layer", ""))  # 인식층(정보/지식/지혜, #189 U5)
    # 승격 대상 repo 루트 — cwd와 함께 명시(#153 U2-4). URL 모드에선 관리형 clone이라
    # cwd≠호출자이고, 핸들러가 stdin 파싱 없이 base repo를 알 수 있게 한다.
    env["OKF_PROJECT"] = str(item.get("project", ""))
    return env


def _reclaim_sealed_residue(project: str | Path) -> list[str] | None:
    """관리형 clone에서 **내구성이 증명된** 잔재를 회수한다. 대상이 아니면 None(#216 V2).

    판정 근거는 핸들러의 exit code가 **아니라** 봉인(내용이 원격추적 ref에 담김)이다.
    exit code는 push 영수증이 될 수 없다 — 계약상 0은 "성공"일 뿐이고(정본 템플릿조차
    '변경 0'이면 push 없이 0을 낸다), trust 미승인은 ``skipped``라 ``failed``가 비어
    '성공'과 구분되지 않는다. 봉인을 보면 그 함정들이 **자동으로** 안전해진다: push되지
    않은 것은 봉인될 수 없으므로 폐기 후보에 오르지 않는다.

    관리형 clone 밖에서는 아무것도 하지 않는다 — ``scope=project``면 여기 오는 경로가
    사용자의 실작업 repo이고, 거기서의 폐기는 미커밋 작업의 파괴다. 락은 refresh와
    공유한다(별도 프로세스라 락 없이는 ff와 경합한다).
    """
    if not okf_vault.is_managed_clone(project):
        return None
    with okf_remote.clone_lock(project) as acquired:
        if not acquired:  # 다른 세션이 worktree를 만지는 중 — 다음 기회로 미룬다
            return None
        return okf_remote.reclaim_sealed(project)


def dispatch(
    project: str | Path,
    item: dict,
    handlers: list[dict],
    trust_check: Callable[[str, Path], bool],
) -> dict:
    """핸들러 배열을 검사·게이트 후 실행하고 {ran, failed, skipped}를 반환한다.

    ``trust_check(name, resolved_path)``는 S4가 넘기는 로컬 승인 판정이다.
    호출자는 반드시 실제 판정을 넘겨야 하며, 실패 격리를 위해 개별 예외를 흡수한다.

    관리형 clone이면 전 핸들러 실행 **후** 봉인 잔재를 회수하고 ``reclaimed``를 덧붙인다
    (#216 V2) — 잔재 누적이 신선도 갱신을 막지 않게 하는 보상 계층이다. 회수는 아이템
    단위가 아니라 봉인 단위라, 아직 디스패치되지 않은 다른 개념을 건드리지 않는다.
    """
    payload = json.dumps(item, ensure_ascii=False)
    env = _handler_env(item)
    ran: list[str] = []
    failed: list[dict] = []
    skipped: list[dict] = []

    for handler in handlers:
        name = str(handler.get("name", "?"))
        command = handler.get("command", "")
        try:
            path = resolve_command(project, command)
        except CommandError as exc:
            skipped.append({"name": name, "reason": str(exc)})
            continue
        if not is_git_tracked(project, path):
            skipped.append({"name": name, "reason": f"미추적 경로 거부: {command}"})
            continue
        if not trust_check(name, path):
            skipped.append({"name": name, "reason": "trust 미승인"})
            continue
        try:
            result = subprocess.run(
                [str(path)],
                input=payload,
                text=True,
                env=env,
                capture_output=True,
                cwd=str(Path(project).resolve()),  # 핸들러 cwd = 승격 대상 repo 루트(#153 U2-4)
            )
        except OSError as exc:  # 실행 불가도 격리
            failed.append({"name": name, "reason": str(exc)})
            continue
        if result.returncode == 0:
            ran.append(name)
        else:
            failed.append({"name": name, "code": result.returncode})
    outcome = {"ran": ran, "failed": failed, "skipped": skipped}
    reclaimed = _reclaim_sealed_residue(project)
    if reclaimed is not None:
        outcome["reclaimed"] = reclaimed
    return outcome
