"""탐색 제공자 리졸버 (Epic #197 U5) — 파리티·trust 게이트·계약 검증 배선.

conftest가 HOME을 tmp로 고정하므로 trust 저장(유저 스코프)이 hermetic하다.
외부 실행·내장 산출은 주입점(execute/builtin)으로 대체해 엔진 서브프로세스 없이
검증한다. 핵심 계약: 미설정 파리티 / 미승인 시 외부 미실행·가시적 폴백 /
승인 후 실행·응답 계약 검증(fail-visible) / 설정 변경 시 재승인 강제.
"""

from __future__ import annotations

import json

import okf_explore
import pytest


def _project(tmp_path, provider=None):
    if provider is not None:
        (tmp_path / ".okf-wiki.json").write_text(
            json.dumps({"explore": {"provider": provider}}), encoding="utf-8"
        )
    bundle = tmp_path / ".okf"
    bundle.mkdir(exist_ok=True)
    return tmp_path, bundle


def _stub_builtin(payload=None):
    calls = []

    def builtin(bundle, op, topic, layer):
        calls.append((bundle, op, topic, layer))
        return payload if payload is not None else {"topics": [{"topic": "."}]}

    return builtin, calls


def test_load_provider_absent_and_malformed(tmp_path, capsys):
    assert okf_explore.load_provider(str(tmp_path)) is None  # 파일 없음 = 미설정(무음)
    (tmp_path / ".okf-wiki.json").write_text('{"explore": {"provider": 3}}', encoding="utf-8")
    assert okf_explore.load_provider(str(tmp_path)) is None  # 형식 오류 → 내장(1줄 안내)
    assert "형식 오류" in capsys.readouterr().err


def test_trust_roundtrip_and_reapproval_on_change(tmp_path):
    project, _ = _project(tmp_path, provider="mytool explore")
    assert not okf_explore.is_trusted(str(project), "mytool explore")  # 프레시 = untrusted
    okf_explore.approve(str(project), "mytool explore")
    assert okf_explore.is_trusted(str(project), "mytool explore")
    # 설정(명령 문자열)이 바뀌면 해시가 달라져 재승인 강제
    assert not okf_explore.is_trusted(str(project), "mytool explore --deep")


def test_run_op_unset_uses_builtin_parity(tmp_path):
    project, bundle = _project(tmp_path)
    builtin, calls = _stub_builtin()

    def never_execute(argv):
        raise AssertionError("미설정인데 외부 실행")

    payload, source, notice = okf_explore.run_op(
        str(bundle), "signals", project=str(project), execute=never_execute, builtin=builtin
    )
    assert source == "builtin" and notice is None
    assert calls == [(str(bundle), "signals", ".", None)]
    assert payload["topics"][0]["topic"] == "."


def test_run_op_untrusted_falls_back_visibly_without_exec(tmp_path):
    project, bundle = _project(tmp_path, provider="mytool explore")
    builtin, calls = _stub_builtin()
    executed = []

    def record_execute(argv):
        executed.append(argv)
        return "{}"

    payload, source, notice = okf_explore.run_op(
        str(bundle), "signals", project=str(project), execute=record_execute, builtin=builtin
    )
    assert executed == []  # 미승인 외부 명령은 실행되지 않는다
    assert source == "builtin" and calls  # 내장 폴백
    assert notice and "미승인" in notice and "approve" in notice  # 가시적 저하


def test_run_op_trusted_executes_contract_argv_and_validates(tmp_path):
    project, bundle = _project(tmp_path, provider="mytool explore")
    okf_explore.approve(str(project), "mytool explore")
    executed = []

    def fake_execute(argv):
        executed.append(argv)
        return json.dumps({"topic": ".", "concepts": [{"path": "a.md", "score": 1}]})

    payload, source, notice = okf_explore.run_op(
        str(bundle),
        "map",
        topic="produce",
        layer="wisdom",
        project=str(project),
        execute=fake_execute,
    )
    assert source == "external" and notice is None
    assert executed[0][:2] == ["mytool", "explore"]  # shlex 분해된 제공자 명령
    assert executed[0][2:4] == ["map", str(bundle)]  # 계약 호출 규약
    assert "--topic" in executed[0] and "--layer" in executed[0] and "--json" in executed[0]
    assert payload["concepts"][0]["path"] == "a.md"  # 확장 필드(score)는 관용 통과


def test_run_op_rejects_contract_violation_fail_visible(tmp_path):
    project, bundle = _project(tmp_path, provider="mytool explore")
    okf_explore.approve(str(project), "mytool explore")

    def bad_execute(argv):
        return json.dumps({"concepts": [{"layer": "wisdom"}]})  # path 누락

    with pytest.raises(RuntimeError, match="계약 위반"):
        okf_explore.run_op(str(bundle), "map", project=str(project), execute=bad_execute)

    def not_json(argv):
        return "oops"

    with pytest.raises(RuntimeError, match="JSON이 아님"):
        okf_explore.run_op(str(bundle), "signals", project=str(project), execute=not_json)


# --- 중첩 bundlePath에서 project 해소 (#301) -----------------------------------
#
# `run_op`가 `dirname(abspath(bundle))`로 프로젝트 루트를 추정했다. 중첩
# `bundlePath`(`docs/.okf` 등)에서는 그 디렉토리에 `.okf-wiki.json`이 없어, **승인된**
# 외부 제공자가 안내 한 줄 없이 미실행되고 내장 폴백으로 조용히 떨어진다 —
# '설정을 못 찾은 것'이 '설정 없음'으로 읽히는 같은 계열이다.


def _nested_project(tmp_path, provider):
    """설정은 repo 루트에, 번들은 `docs/.okf`에 있는 배치."""
    root = tmp_path / "repo"
    bundle = root / "docs" / ".okf"
    bundle.mkdir(parents=True)
    (root / ".okf-wiki.json").write_text(
        json.dumps({"bundlePath": "docs/.okf", "explore": {"provider": provider}}),
        encoding="utf-8",
    )
    return root, bundle


def test_nested_bundle_resolves_to_trusted_provider(tmp_path):
    """중첩 번들에서도 `--project` 없이 **승인된** 제공자로 해소된다.

    예전에는 `dirname(bundle)`(=`docs/`)에 설정이 없어 `provider: None`이 나왔고,
    그러면 안내 한 줄 없이 내장 폴백이다 — 사용자는 자기 제공자가 안 돌았다는 사실
    자체를 모른다.
    """
    provider = "mytool explore"
    root, bundle = _nested_project(tmp_path, provider)
    okf_explore.approve(str(root), provider)

    status = okf_explore.resolve(okf_explore.project_root(str(bundle)))
    assert status["provider"] == provider, status
    assert status["trusted"] is True, status


def test_run_op_project_resolution_prefers_config_bearing_ancestor(tmp_path):
    """해소 결과가 **설정이 있는 조상**이다 — dirname 추정이 아니라."""
    root, bundle = _nested_project(tmp_path, "mytool explore")
    assert okf_explore.project_root(str(bundle)) == str(root.resolve())


def test_project_root_falls_back_to_parent_when_no_config(tmp_path):
    """설정이 어디에도 없으면 기존 추정으로 폴백한다(무회귀)."""
    bundle = tmp_path / "loose" / ".okf"
    bundle.mkdir(parents=True)
    assert okf_explore.project_root(str(bundle)) == str(bundle.parent)
