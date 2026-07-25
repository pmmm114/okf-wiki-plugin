#!/usr/bin/env python3
"""탐색 제공자 리졸버 (Epic #197 U5) — ``explore.provider`` 오버라이드 배선.

EXPLORE.md 계약 §4의 교체 메커니즘이다. ``.okf-wiki.json``의 ``explore.provider``로
외부 탐색 명령을 주입하면 내장 제공자(okf_layers) 대신 그 명령이 계약 연산
(``signals``/``map``)을 수행한다. 미설정이면 내장 — **파리티**(동작 무변경).

보안은 study 핸들러 trust와 동일 원리다: 커밋되는 설정만으로 코드가 실행되지
않는다 — 승인은 **비커밋 유저 스코프**(``~/.claude/okf/explore-trust.json``)에
프로젝트별 **명령 문자열 해시**로 저장되고, 설정이 바뀌면 해시가 달라져 재승인이
강제되며, 프레시 클론은 항상 untrusted다. 미승인 외부 제공자는 **실행하지 않고**
stderr 1줄 안내와 함께 내장으로 가시적 폴백한다(fail-visible 저하).

외부 응답은 소비 전에 **계약 검증기**(okf_layers.validate_*)를 통과해야 한다 —
위반이면 폴백 없이 오류로 죽는다(잘못된 제공자가 조용히 판정에 스며들지 않게).
제공자는 read-only여야 한다(계약 불변식 3 — 번들 변경은 승격 파이프라인의 독점).
특정 외부 도구는 참조하지 않는다 — 제공자 실체는 소비처가 주입한다.

CLI: ``okf_explore.py status <project> [--json]`` · ``approve <project>`` ·
``run <bundle> {signals|map} [--topic T] [--layer L] [--project P]``(항상 계약 JSON
출력 — 기계 소비 전용). 어휘·검증기는 okf_layers 재사용(하드코딩 0, core⊥study).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys

import okf_layers

CONFIG_NAME = ".okf-wiki.json"
_TRUST_FILE = os.path.join("~", ".claude", "okf", "explore-trust.json")


def load_provider(project: str) -> str | None:
    """``explore.provider``(비어있지 않은 문자열)를 읽는다 — 부재·파스 실패·형식
    오류는 전부 None(미설정 취급, stderr 1줄)로 흡수한다(침묵 정책과 정합)."""
    path = os.path.join(project, CONFIG_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"okf_explore: {CONFIG_NAME} 로드 실패 — 내장 사용: {exc}", file=sys.stderr)
        return None
    explore = config.get("explore") if isinstance(config, dict) else None
    provider = explore.get("provider") if isinstance(explore, dict) else None
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        print("okf_explore: explore.provider 형식 오류(문자열) — 내장 사용", file=sys.stderr)
        return None
    return provider.strip() if isinstance(provider, str) else None


def provider_hash(provider: str) -> str:
    return hashlib.sha256(provider.encode("utf-8")).hexdigest()


def _trust_store() -> str:
    return os.path.expanduser(_TRUST_FILE)


def _load_trust() -> dict:
    try:
        with open(_trust_store(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}  # 깨진 승인 파일은 미승인과 같다 — fail-closed


def _project_key(project: str) -> str:
    return os.path.realpath(project)


def is_trusted(project: str, provider: str) -> bool:
    return _load_trust().get(_project_key(project)) == provider_hash(provider)


def approve(project: str, provider: str) -> str:
    """현재 설정된 제공자 명령을 이 프로젝트에 승인 기록한다(명령이 바뀌면 무효)."""
    store = _trust_store()
    os.makedirs(os.path.dirname(store), exist_ok=True)
    trust = _load_trust()
    trust[_project_key(project)] = provider_hash(provider)
    with open(store, "w", encoding="utf-8") as f:
        json.dump(trust, f, ensure_ascii=False, indent=2, sort_keys=True)
    return store


def resolve(project: str) -> dict:
    """제공자 해소 상태 — {provider: str|None, trusted: bool}."""
    provider = load_provider(project)
    return {
        "provider": provider,
        "trusted": bool(provider and is_trusted(project, provider)),
    }


def _builtin_payload(bundle: str, op: str, topic: str, layer: str | None) -> dict:
    spec = okf_layers.load_layers_spec()
    meta = okf_layers.parse_context_meta(okf_layers._grouped_context(bundle, spec))
    graph = okf_layers._typed_graph(bundle, spec)
    if op == "signals":
        return okf_layers.build_signals(spec, meta, graph)
    return okf_layers.build_map(spec, meta, graph, topic=topic, layer=layer)


def _execute(argv: list[str]) -> str:
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"제공자 실행 실패(rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def run_op(
    bundle: str,
    op: str,
    topic: str = ".",
    layer: str | None = None,
    project: str | None = None,
    execute=_execute,
    builtin=_builtin_payload,
) -> tuple[dict, str, str | None]:
    """계약 연산 1회 — (payload, source, notice)를 반환한다.

    source는 ``builtin``/``external``. notice는 가시적 저하 안내(미승인 폴백 시).
    외부 응답이 계약 검증기를 통과하지 못하면 RuntimeError(폴백 없음 — fail-visible).
    ``execute``/``builtin``은 테스트 주입점이다.
    """
    project = project or os.path.dirname(os.path.abspath(bundle)) or "."
    status = resolve(project)
    provider = status["provider"]
    if provider is None:
        return builtin(bundle, op, topic, layer), "builtin", None
    if not status["trusted"]:
        notice = (
            f"외부 탐색 제공자 미승인 — 내장으로 폴백: {provider!r} "
            f"(승인: okf_explore.py approve {project})"
        )
        return builtin(bundle, op, topic, layer), "builtin", notice

    argv = shlex.split(provider) + [op, bundle]
    if op == "map":
        argv += ["--topic", topic]
        if layer is not None:
            argv += ["--layer", layer]
    argv.append("--json")
    try:
        payload = json.loads(execute(argv))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"제공자 응답이 JSON이 아님: {exc}") from exc
    validator = (
        okf_layers.validate_signals_payload if op == "signals" else okf_layers.validate_map_payload
    )
    errors = validator(payload)
    if errors:
        raise RuntimeError(f"제공자 응답 계약 위반(EXPLORE §2): {errors}")
    return payload, "external", None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="okf_explore", description="탐색 제공자 리졸버(계약 §4)")
    sub = ap.add_subparsers(dest="op", required=True)
    st = sub.add_parser("status", help="제공자 해소 상태")
    st.add_argument("project", help="프로젝트 루트(.okf-wiki.json 위치)")
    st.add_argument("--json", action="store_true")
    apv = sub.add_parser("approve", help="현재 설정된 외부 제공자 명령을 로컬 승인")
    apv.add_argument("project", help="프로젝트 루트")
    rn = sub.add_parser("run", help="계약 연산 실행(리졸버 경유, 항상 계약 JSON)")
    rn.add_argument("bundle", help="번들 디렉터리 경로")
    rn.add_argument("contract_op", choices=["signals", "map"], help="계약 연산")
    rn.add_argument("--topic", default=".")
    rn.add_argument("--layer")
    rn.add_argument("--project", help="프로젝트 루트(기본: 번들의 부모)")
    args = ap.parse_args(argv)

    if args.op == "status":
        status = resolve(args.project)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        elif status["provider"] is None:
            print("탐색 제공자: 내장(okf_layers) — explore.provider 미설정")
        else:
            state = "승인됨" if status["trusted"] else "미승인(내장 폴백)"
            print(f"탐색 제공자: {status['provider']!r} — {state}")
        return 0

    if args.op == "approve":
        provider = load_provider(args.project)
        if provider is None:
            print("오류: explore.provider가 설정돼 있지 않다 — 승인 대상 없음", file=sys.stderr)
            return 2
        store = approve(args.project, provider)
        print(f"승인 기록: {provider!r} → {store}")
        return 0

    if not os.path.isdir(args.bundle):
        print(f"오류: 번들 디렉터리가 아님: {args.bundle}", file=sys.stderr)
        return 2
    try:
        payload, _source, notice = run_op(
            args.bundle, args.contract_op, topic=args.topic, layer=args.layer, project=args.project
        )
    except (RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    if notice:
        print(f"okf_explore: {notice}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
