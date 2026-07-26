"""study 디스패처 코어 (S3, #75).

검증된 개념을 담은 study 아이템을 소비처가 주입한 핸들러 배열로 흘려보낸다.
각 핸들러는 stdin으로 아이템(JSON)을 받고, 트리거·개념 정보를 env var로도 받는다:
``OKF_TRIGGER``(현재 ``manual`` 하나 — 훅은 디스패치하지 않으므로 자동 트리거가 없다)·
``OKF_CONCEPT_TYPE``·``OKF_CONCEPT_TOPIC``·``OKF_CONCEPT_PATH``·``OKF_CONCEPT_LAYER``
(인식층 값 — 어휘는 LAYERS.md 단일원천, 한국어 정보·지식·지혜는 그 **라벨**이다, #189 U5).

실행 전 게이트는 3축이고 판정은 ``_verdict`` 하나에만 산다 — ``dispatch``(실행)와
``dispatchability``(질의)가 **같은 판정**을 쓴다(#266 U1). 두 곳에서 판정하면 한쪽만
고쳐지는 드리프트가 생기고, 설정 존재 여부 같은 프록시로 답하면 "배선은 됐는데 나가지
못하는" 구간이 통과로 읽힌다.

1. **경로 검사**(``escape``) — ``command``는 repo 트리 안으로 정규화돼야 한다(심링크·
   ``..`` 탈출 거부).
2. **git 추적**(``untracked``) — 미추적 거부, fail-closed. 스캐폴드 직후가 이 상태다.
3. **trust 게이트**(``untrusted``) — ``trust_check(name, path)``. 미승인이면 보류.

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


# 디스패치 차단 사유 코드 — 소비처가 분기하는 **기계 축**이다(#266 U1).
# 자연어 ``reason``은 사람에게 보이는 표시일 뿐 판정 입력이 아니다: 문구를 다듬는 일이
# 기능 고장이 되지 않게 한다. ``dispatch``의 ``failed[].code``는 프로세스 exit code로
# 의미가 다르다 — 배열이 달라 문맥으로 구분된다.
CODE_ESCAPE = "escape"
CODE_UNTRACKED = "untracked"
CODE_UNTRUSTED = "untrusted"
CODE_UNWIRED = "unwired"
CODE_OK = "ok"

# 차단 코드 → **실행 가능한 복구 지시**. 문서 게이트가 이 집합 **전체**를 대조한다(#266 U2).
#
# ``unwired``는 ``dispatchability``가 내지 않는다 — 핸들러 배열이 비면 판정할 대상이 없다.
# 그런데 Epic이 지목한 본체가 바로 그 상태라, 코드 단일원천을 함수 반환값이 아니라 **이
# 상수**로 두어야 게이트가 세 상태를 전부 덮는다. 반환값만 대조하면 미배선이 조용히 샌다.
BLOCKERS: dict[str, str] = {
    CODE_ESCAPE: "핸들러 command가 repo 트리 밖을 가리킨다 — .okf-wiki.json의 command를 고쳐라",
    CODE_UNTRACKED: (
        "핸들러 파일이 git 미추적이다 — vault repo에 커밋하라(관리형 clone이면 브랜치→PR)"
    ),
    CODE_UNTRUSTED: "이 머신에서 핸들러가 미승인이다 — `/study --trust`로 승인하라",
    CODE_UNWIRED: "원격 반영 경로가 없다 — `/okf-init --vault`로 핸들러를 배선하라",
}


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


def _verdict(project: str | Path, handler: dict, trust_check=None) -> dict:
    """핸들러 **하나**의 디스패치 가능성 판정 — ``{name, code, reason, path}``.

    게이트 순서(경로 → git 추적 → trust)와 사유 문자열을 그대로 옮긴다. 사유는 문서·
    테스트·소비처가 걸고 있는 표면이라 **바이트 그대로** 보존한다.

    ``trust_check``가 None이면 trust 축을 **평가하지 않는다**. 그때의 ``ok``는 "경로·추적
    2축 기준 준비됨"이지 "실행된다"가 아니다 — trust는 머신별 승인이라 별도 층이고,
    마법사는 그것을 따로 안내한다.
    """
    name = str(handler.get("name", "?"))
    command = handler.get("command", "")
    try:
        path = resolve_command(project, command)
    except CommandError as exc:
        return {"name": name, "code": CODE_ESCAPE, "reason": str(exc), "path": None}
    if not is_git_tracked(project, path):
        return {
            "name": name,
            "code": CODE_UNTRACKED,
            "reason": f"미추적 경로 거부: {command}",
            "path": None,
        }
    if trust_check is not None and not trust_check(name, path):
        return {"name": name, "code": CODE_UNTRUSTED, "reason": "trust 미승인", "path": None}
    return {"name": name, "code": CODE_OK, "reason": "", "path": path}


def dispatchability(project: str | Path, handlers: list[dict], trust_check=None) -> list[dict]:
    """핸들러 배열의 디스패치 가능성 — **판정 단일원천**(#266 U1). 실행하지 않는다.

    ``dispatch``와 **같은 판정**을 쓰므로 마법사·진단이 "왜 안 나가는가"를 프록시(설정
    존재 여부)가 아니라 실제 게이트로 답할 수 있다. 설정에 핸들러가 있어도 파일이
    미커밋이면 나가지 못하는데, 스캐폴드 직후가 정확히 그 상태다.

    **핸들러가 비면 빈 리스트다** — "미배선"은 여기가 아니라 호출자(cmd 계층)의 조건이다.
    이 함수의 code 집합을 "전부"로 오해하면 미배선 상태가 판정 밖으로 빠진다.
    """
    return [_verdict(project, handler, trust_check) for handler in handlers]


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
        # 판정은 `_verdict` 하나에만 산다. 다만 **루프 안에서** 부른다 — 전량 선계산하면
        # 판정이 첫 실행 이전에 고정되는데, 핸들러가 워킹트리를 커밋·push하는 계약이라
        # 앞 핸들러의 실행이 뒤 핸들러의 추적 상태를 바꿀 수 있다.
        verdict = _verdict(project, handler, trust_check)
        name = verdict["name"]
        if verdict["code"] != CODE_OK:
            skipped.append({"name": name, "reason": verdict["reason"], "code": verdict["code"]})
            continue
        path = verdict["path"]
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
