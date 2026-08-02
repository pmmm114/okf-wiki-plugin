"""층간 승격 파이프라인 (Epic #197 U3) — 게이트 차단·집행·롤백 검증.

게이트는 순수 판정(tmp 번들 + 합성 layer_map)으로, 집행은 가짜 엔진 러너 주입으로
hermetic하게 검증한다(엔진 서브프로세스 없음 — 실번들 왕복은 test_context_roundtrip.py).
게이트 각 항목은 §9 금지의 파괴 감지 성격이라 위반 제안이 실제로 반려되는
고의-red를 각 테스트가 실증한다.
"""

from __future__ import annotations

import json

import okf_promote
import pytest

SPEC = {
    "field": "layer",
    "order": ["information", "knowledge", "wisdom"],
    "derivation_field": "derived_from",
    "source_fields": ["resource", "citations"],
    "rules": {
        "derivation_strictly_downward": True,
        "information_requires_source": True,
        "upper_requires_derived_from": True,
    },
}


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / "info.md").write_text(
        "---\ntype: fact\ndescription: 사실.\nlayer: information\n"
        "resource: https://ex.org\n---\n\n# 답\n",
        encoding="utf-8",
    )
    return tmp_path


def _material(bundle, rel="info.md"):
    return {"path": rel, "sha256": okf_promote.sha256_file(str(bundle / rel))}


def _proposal(bundle, **over):
    base = {
        "target_layer": "knowledge",
        "path": "/model.md",
        "type": "concept",
        "description": "사실들의 연결.",
        "body": "# 연결\n\n[근거](/info.md) 위의 이해.",
        "derived_from": ["/info.md"],
        "materials": [_material(bundle)],
        # 상위 층 제안은 자기검증이 필수다(#307) — 문서 계약이 코드에 걸린 뒤로
        # 픽스처도 계약을 지켜야 한다.
        "rubric": {
            "new_insight": "두 사실을 잇는 이해.",
            "falsification": "근거가 틀리면 무너진다.",
        },
    }
    base.update(over)
    return base


LAYERS = {"info.md": "information", "wise.md": "wisdom"}


# --- 게이트: §9 금지 각각의 고의-red -----------------------------------------


def test_gate_passes_valid_proposal(bundle):
    assert okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), _proposal(bundle)) == []


def test_gate_rejects_unknown_layer_vocab(bundle):
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, target_layer="gold")
    )
    assert any("어휘 위반" in r for r in reasons)


def test_gate_rejects_derivation_inversion(bundle):
    # 정보가 지혜에서 파생 — 역전(§9 금지 5)
    (bundle / "wise.md").write_text(
        "---\ntype: decision\ndescription: 판단.\nlayer: wisdom\n---\n\n# 판단\n", encoding="utf-8"
    )
    proposal = _proposal(
        bundle,
        target_layer="information",
        resource="https://ex.org",
        derived_from=["/wise.md"],
        materials=[_material(bundle, "wise.md")],
    )
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("정초 역전" in r for r in reasons)


def test_gate_rejects_relabel_of_existing_file(bundle):
    # 기존 파일 경로로 제안 — 재라벨·덮어쓰기(§9 금지 2)
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, path="/info.md")
    )
    assert any("신설 아님" in r for r in reasons)


def test_gate_rejects_path_escaping_bundle(bundle):
    # `..`가 든 path는 번들 밖에 쓰인다 — 엔진은 번들 안만 보므로 탐지도 롤백도 없다(#267)
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, path="../escaped.md")
    )
    assert any("번들 경계" in r for r in reasons)


def test_gate_rejects_absolute_path_escaping_bundle(bundle):
    # norm_rel은 선두 `/`만 벗기므로 남은 `..`가 그대로 트리를 벗어난다
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, path="/../../x.md")
    )
    assert any("번들 경계" in r for r in reasons)


def test_gate_rejects_symlink_escape(bundle, tmp_path_factory):
    # 문자열엔 `..`가 없지만 실제 쓰기는 번들 밖 — 엔진의 번들 순회는 심링크 디렉터리로
    # 내려가지 않아 이렇게 쓰인 개념을 영영 보지 못한다(`..` 탈출과 동급의 침묵 실패).
    (bundle / "link").symlink_to(tmp_path_factory.mktemp("outside"))
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, path="link/x.md")
    )
    assert any("번들 경계" in r for r in reasons)


def test_gate_accepts_bundle_under_symlink(tmp_path):
    # 번들 **자체**가 심링크 아래인 배치는 정상이다 — 루트도 함께 해소하므로 오탐이 없다.
    real = tmp_path / "real"
    real.mkdir()
    (real / "info.md").write_text(
        "---\ntype: fact\ndescription: 사실.\nlayer: information\n"
        "resource: https://ex.org\n---\n\n# 답\n",
        encoding="utf-8",
    )
    link = tmp_path / "via-link"
    link.symlink_to(real, target_is_directory=True)
    proposal = _proposal(real, path="/model.md")
    assert okf_promote.gate_proposal(SPEC, LAYERS, str(link), proposal) == []


@pytest.mark.parametrize("path", ["/model.md", "model.md", "sub/model.md", "./model.md"])
def test_gate_accepts_in_bundle_paths(bundle, path):
    # LAYERS 권장 절대표기·엔진 출력 표기 모두 번들 안 — 경계 검사가 삼키면 안 된다
    assert okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), _proposal(bundle, path=path)) == []


def test_gate_rejects_escaping_material_paths(bundle):
    # 재료 경로도 같은 판정 — 번들 밖을 근거로 인정하면 사슬이 트리를 벗어난다
    reasons = okf_promote.gate_proposal(
        SPEC,
        LAYERS,
        str(bundle),
        _proposal(
            bundle,
            derived_from=["../outside.md"],
            materials=[{"path": "../outside.md", "sha256": "0" * 64}],
            allow_dangling=["../outside.md"],
        ),
    )
    for field in ("derived_from", "materials", "allow_dangling"):
        assert any("번들 경계" in r and field in r for r in reasons), field


def test_gate_rejects_modified_material(bundle):
    proposal = _proposal(bundle)
    (bundle / "info.md").write_text("변조", encoding="utf-8")  # snapshot 이후 수정(§9 금지 3)
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("재료 수정됨" in r for r in reasons)


def test_gate_rejects_missing_snapshot(bundle):
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), _proposal(bundle, materials=[]))
    assert any("재료 스냅샷 누락" in r for r in reasons)


def test_gate_rejects_fabricated_grounds_unless_declared(bundle):
    # 없는 근거는 반려(§9 금지 1) — allow_dangling으로 명시하면 미작성 신호로 허용
    proposal = _proposal(bundle, derived_from=["/ghost.md"], materials=[])
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("근거 부재" in r for r in reasons)
    declared = okf_promote.gate_proposal(
        SPEC,
        LAYERS,
        str(bundle),
        _proposal(bundle, derived_from=["/ghost.md"], materials=[], allow_dangling=["/ghost.md"]),
    )
    assert declared == []


def test_gate_requires_resource_for_information(bundle):
    proposal = _proposal(bundle, target_layer="information", derived_from=[], materials=[])
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("정보층 출처 필요" in r for r in reasons)


def test_gate_requires_grounding_for_upper(bundle):
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, derived_from=[], materials=[])
    )
    assert any("미접지 제안" in r for r in reasons)


def test_gate_rejects_unclassified_material(bundle):
    (bundle / "plain.md").write_text(
        "---\ntype: note\ndescription: 미분류.\n---\n\n# 메모\n", encoding="utf-8"
    )
    proposal = _proposal(
        bundle, derived_from=["/plain.md"], materials=[_material(bundle, "plain.md")]
    )
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("재료 미분류" in r for r in reasons)


def test_gate_exempts_snapshot_for_batch_promoted(bundle):
    # §9 원칙 3 캐스케이드 — 같은 배치에서 방금 승격된 재료는 스냅샷 면제
    (bundle / "new-info.md").write_text("---\ntype: fact\n---\n\n# 답\n", encoding="utf-8")
    layers = {**LAYERS, "new-info.md": "information"}
    proposal = _proposal(bundle, derived_from=["/new-info.md"], materials=[])
    assert any(
        "재료 스냅샷 누락" in r
        for r in okf_promote.gate_proposal(SPEC, layers, str(bundle), proposal)
    )
    assert (
        okf_promote.gate_proposal(
            SPEC, layers, str(bundle), proposal, batch_promoted={"new-info.md"}
        )
        == []
    )


# --- snapshot · render -------------------------------------------------------


def test_snapshot_collects_hashes_and_missing(bundle):
    result = okf_promote.snapshot_materials(str(bundle), ["/info.md", "ghost.md"])
    assert result["materials"] == [_material(bundle)]
    assert result["missing"] == ["ghost.md"]


def test_render_concept_materializes_frontmatter():
    proposal = {
        "target_layer": "knowledge",
        "type": "concept",
        "description": "연결.",
        "derived_from": ["info.md"],
        "resource": "",
        "body": "# 본문",
    }
    text = okf_promote.render_concept(SPEC, proposal)
    assert text.startswith("---\ntype: concept\ndescription: 연결.\nlayer: knowledge\n")
    assert "derived_from:\n  - /info.md\n" in text  # LAYERS 권장 절대(번들 상대) 표기
    assert "resource:" not in text  # 빈 출처는 쓰지 않는다
    assert text.endswith("---\n\n# 본문\n")


# --- apply: 가짜 엔진 러너로 집행·롤백 ---------------------------------------


class FakeEngine:
    def __init__(self, bundle, fail_validate=False, frontmatter=None, query_rows=None):
        self.bundle = str(bundle)
        self.fail_validate = fail_validate
        self.calls: list[list[str]] = []
        # update 로더(#351 U1)가 부르는 query 응답 — 기본은 bundle 픽스처 info.md.
        # query_rows로 행 자체를 대체한다([] = valid 뷰 밖, 규격 미달·비개념).
        self.query_rows = query_rows
        self.frontmatter = frontmatter or {
            "type": "fact",
            "description": "사실.",
            "layer": "information",
            "resource": "https://ex.org",
            "timestamp": "2026-01-01",
        }

    def __call__(self, args):
        self.calls.append(args)
        op = args[0]
        if op == "context":
            return "<okf-context>\n## information\ninfo.md [fact] — 사실.\n</okf-context>"
        if op == "validate":
            if self.fail_validate:
                raise RuntimeError("okf validate 실패(rc=1): error POL.broken-link")
            return "컨포먼트: error 0건, warn 0건\n"
        if op == "graph":
            if "--chain" in args:
                return "info.md\n"
            return json.dumps({"nodes": [], "edges": [], "typed_edges": []})
        if op == "query":
            if self.query_rows is not None:
                return json.dumps(self.query_rows)
            return json.dumps([{"frontmatter_json": json.dumps(self.frontmatter)}])
        return ""


def test_apply_promotes_writes_and_records(bundle):
    engine = FakeEngine(bundle)
    report = okf_promote.apply_proposals(str(bundle), [_proposal(bundle)], run=engine)
    assert [p["path"] for p in report["promoted"]] == ["model.md"]
    assert report["rejected"] == []
    written = (bundle / "model.md").read_text(encoding="utf-8")
    assert "layer: knowledge" in written and "  - /info.md" in written
    ops = [call[0] for call in engine.calls]
    assert ops.count("validate") == 1 and "log" in ops and "index" in ops
    log_call = next(call for call in engine.calls if call[0] == "log")
    assert "Promotion" in log_call and "layer knowledge ← 하위 1건" in " ".join(log_call)


def test_apply_rolls_back_on_validate_failure(bundle):
    engine = FakeEngine(bundle, fail_validate=True)
    report = okf_promote.apply_proposals(str(bundle), [_proposal(bundle)], run=engine)
    assert report["promoted"] == []
    assert not (bundle / "model.md").exists()  # 반려 제안은 번들을 오염시키지 않는다
    assert any("validate --strict 실패" in r for r in report["rejected"][0]["reasons"])
    ops = [call[0] for call in engine.calls]
    assert "log" not in ops and "index" not in ops  # 집행 기록 없음


def test_apply_gate_reject_skips_engine_execution(bundle):
    engine = FakeEngine(bundle)
    bad = _proposal(bundle, target_layer="gold")
    report = okf_promote.apply_proposals(str(bundle), [bad], run=engine)
    assert report["promoted"] == [] and len(report["rejected"]) == 1
    ops = [call[0] for call in engine.calls]
    assert "validate" not in ops  # 게이트가 집행 전에 죽인다


def test_apply_escaping_path_writes_nothing_outside_bundle(bundle):
    # 롤백은 번들 밖을 되돌리지 못한다(validate가 그 파일을 못 보므로 실패조차 안 한다) —
    # 쓰기 0건이 유일한 답이라, 게이트가 집행 전에 죽이는지를 잠근다.
    engine = FakeEngine(bundle)
    report = okf_promote.apply_proposals(
        str(bundle), [_proposal(bundle, path="../escaped.md")], run=engine
    )
    assert report["promoted"] == [] and len(report["rejected"]) == 1
    assert not (bundle.parent / "escaped.md").exists()
    ops = [call[0] for call in engine.calls]
    assert "validate" not in ops and "log" not in ops and "index" not in ops


def test_apply_cascade_grounds_on_same_batch_promotion(bundle):
    # 정보 먼저, 그 위의 지식 — §9 원칙 3("같은 변경에서 먼저 승격") 캐스케이드
    engine = FakeEngine(bundle)
    info = {
        "target_layer": "information",
        "path": "/facts/new.md",
        "type": "fact",
        "description": "새 사실.",
        "body": "# 값",
        "derived_from": [],
        "materials": [],
        "resource": "https://ex.org/src",
    }
    knowledge = _proposal(
        bundle, path="/facts/model.md", derived_from=["/facts/new.md"], materials=[]
    )
    report = okf_promote.apply_proposals(str(bundle), [info, knowledge], run=engine)
    assert [p["path"] for p in report["promoted"]] == ["facts/new.md", "facts/model.md"]
    assert report["rejected"] == []


# --- 미지 층은 크래시가 아니라 반려 · 크래시는 반려와 다른 exit code (#295) ------
#
# 번들 전역 layer 어휘 검사는 repo 어디에도 없다 — 엔진은 taxonomy-neutral이라
# `validate --strict`가 `layer: infomation`(오타)을 error 0 / warn 0으로 통과시킨다.
# 그 개념이 어떤 제안의 derived_from에 들어가면 `rank[dep_layer]`가 KeyError로 죽고,
# exit 1은 계약상 "반려가 있음"과 **같은 코드**라 소비처가 둘을 구분할 수 없다.


def test_gate_rejects_unknown_dep_layer_without_crash(bundle):
    """rank 밖 층은 `is None`과 **같은 반려 사유**로 흡수된다 — 크래시가 아니다."""
    spec = okf_promote.okf_layers.load_layers_spec()
    reasons = okf_promote.gate_proposal(
        spec, {"info.md": "infomation"}, str(bundle), _proposal(bundle)
    )
    assert reasons, "미지 층 재료는 반려돼야 한다"
    assert any("infomation" in r for r in reasons), reasons


def test_apply_returns_contract_json_on_engine_failure(bundle):
    """엔진 호출이 죽어도 계약 JSON이 나온다 — stdout 0바이트는 소비처 계약 파기다.

    `validate`만 감싸여 있어 `log`·`index`·`graph`·`context` 실패는 traceback으로
    빠져나갔다. 그때 배치 앞부분은 이미 파일로 쓰이고 log에도 남은 상태다.
    """
    engine = FakeEngine(bundle)
    original = engine.__call__

    def boom(args):
        if args[0] == "log":
            raise RuntimeError("엔진 폭발(주입)")
        return original(args)

    report = okf_promote.apply_proposals(str(bundle), [_proposal(bundle)], run=boom)
    assert set(report) >= {"promoted", "rejected", "lint_warns", "error"}, report
    assert report["error"]["stage"] == "log", report["error"]
    assert report["error"]["code"] and report["error"]["detail"], report["error"]


def test_main_separates_crash_from_rejection(bundle, monkeypatch, capsys):
    """크래시(3)와 반려(1)와 전량 승격(0)이 서로 다른 종료코드다."""
    proposals = bundle / "p.json"
    proposals.write_text("[]", encoding="utf-8")

    def with_error(_bundle, _proposals, run=None):
        return {
            "promoted": [],
            "rejected": [],
            "lint_warns": [],
            "error": {"code": "engine_failed", "stage": "log", "detail": "d"},
        }

    monkeypatch.setattr(okf_promote, "apply_proposals", with_error)
    rc = okf_promote.main(["apply", str(bundle), "--proposals", str(proposals)])
    assert rc == 3, "크래시는 반려(1)와 다른 코드여야 한다"
    assert json.loads(capsys.readouterr().out)["error"]["stage"] == "log"

    monkeypatch.setattr(
        okf_promote,
        "apply_proposals",
        lambda *_a, **_k: {"promoted": [], "rejected": [{"path": "x"}], "lint_warns": []},
    )
    assert okf_promote.main(["apply", str(bundle), "--proposals", str(proposals)]) == 1

    monkeypatch.setattr(
        okf_promote,
        "apply_proposals",
        lambda *_a, **_k: {"promoted": [{"path": "x"}], "rejected": [], "lint_warns": []},
    )
    assert okf_promote.main(["apply", str(bundle), "--proposals", str(proposals)]) == 0


def test_apply_preserves_rejections_on_engine_failure(bundle):
    """크래시해도 그때까지의 **반려**가 남는다(#295 리뷰) — 고칠 근거가 사라지면 안 된다.

    배치가 [게이트 반려, 정상] 순일 때 두 번째에서 엔진이 죽어도, 첫 번째의 반려 사유는
    사용자가 제안을 고치는 유일한 단서다.
    """
    engine = FakeEngine(bundle)
    original = engine.__call__

    def boom(args):
        if args[0] == "log":
            raise RuntimeError("엔진 폭발(주입)")
        return original(args)

    bad = _proposal(bundle, target_layer="nonexistent-layer")
    report = okf_promote.apply_proposals(str(bundle), [bad, _proposal(bundle)], run=boom)
    assert report["error"]["stage"] == "log", report["error"]
    assert report["rejected"], "크래시 전에 결정된 반려가 사라졌다"
    assert any("어휘 위반" in r for r in report["rejected"][0]["reasons"]), report["rejected"]


# --- #351 U1: mode create|update · layer 별칭·미기재 일반화 -------------------


def _layer_only(proposal):
    proposal = dict(proposal)
    proposal["layer"] = proposal.pop("target_layer")
    return proposal


def test_gate_accepts_layer_alias(bundle):
    assert (
        okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), _layer_only(_proposal(bundle))) == []
    )


def test_gate_rejects_alias_conflict(bundle):
    reasons = okf_promote.gate_proposal(
        SPEC, LAYERS, str(bundle), _proposal(bundle, layer="wisdom")
    )
    assert any("불일치" in r for r in reasons)


def test_gate_rejects_invalid_mode(bundle):
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), _proposal(bundle, mode="patch"))
    assert any("모드 위반" in r for r in reasons)


def test_gate_layerless_create_skips_layer_gates_keeps_material_gates(bundle):
    # 층 미기재 허용(#351) — rubric·미접지·정초는 안 걸리고 재료 해시는 여전히 걸린다
    proposal = _proposal(bundle)
    proposal.pop("target_layer")
    proposal.pop("rubric")
    assert okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal) == []
    (bundle / "info.md").write_text("변조", encoding="utf-8")
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("재료 수정됨" in r for r in reasons)


def test_gate_update_requires_existing_target(bundle):
    proposal = {
        "mode": "update",
        "path": "/ghost.md",
        "type": "fact",
        "description": "d.",
        "body": "b",
    }
    reasons = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), proposal)
    assert any("갱신 대상 없음" in r for r in reasons)


def test_gate_update_blocks_relabel_allows_same_layer(bundle):
    base = {"mode": "update", "path": "/info.md", "type": "fact", "description": "d.", "body": "b"}
    relabel = okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), {**base, "layer": "knowledge"})
    assert any("재라벨 금지" in r for r in relabel)
    assert (
        okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), {**base, "layer": "information"}) == []
    )
    assert okf_promote.gate_proposal(SPEC, LAYERS, str(bundle), base) == []  # 미기재 = 기존 유지


def test_render_concept_without_layer_omits_layer_line():
    proposal = {"type": "fact", "description": "d.", "body": "# 본문", "derived_from": []}
    text = okf_promote.render_concept(SPEC, proposal)
    assert "layer:" not in text and text.startswith("---\ntype: fact\n")


def test_apply_update_merges_frontmatter_and_preserves_axes(bundle):
    engine = FakeEngine(bundle)
    proposal = {
        "mode": "update",
        "path": "/info.md",
        "type": "fact",
        "description": "갱신된 사실.",
        "body": "# 새 답",
    }
    report = okf_promote.apply_proposals(str(bundle), [proposal], run=engine)
    assert report["rejected"] == [] and report["promoted"][0]["mode"] == "update"
    assert report["promoted"][0]["layer"] == "information"  # 기존 층 유지
    written = (bundle / "info.md").read_text(encoding="utf-8")
    assert "description: 갱신된 사실." in written and "# 새 답" in written
    assert "timestamp: 2026-01-01" in written  # 미지 축 보존
    assert "layer: information" in written and "resource: https://ex.org" in written
    log_call = next(call for call in engine.calls if call[0] == "log")
    assert "Update" in log_call and "(갱신)" in " ".join(log_call)
    assert [c[0] for c in engine.calls].count("query") == 1


def test_apply_update_restores_original_on_validate_failure(bundle):
    original = (bundle / "info.md").read_text(encoding="utf-8")
    engine = FakeEngine(bundle, fail_validate=True)
    proposal = {
        "mode": "update",
        "path": "/info.md",
        "type": "fact",
        "description": "d.",
        "body": "b",
    }
    report = okf_promote.apply_proposals(str(bundle), [proposal], run=engine)
    assert report["promoted"] == [] and report["rejected"]
    assert (bundle / "info.md").read_text(encoding="utf-8") == original  # 삭제가 아니라 복원


def test_apply_update_rejects_unrenderable_frontmatter(bundle):
    original = (bundle / "info.md").read_text(encoding="utf-8")
    engine = FakeEngine(bundle, frontmatter={"type": "fact", "nested": {"a": 1}})
    proposal = {
        "mode": "update",
        "path": "/info.md",
        "type": "fact",
        "description": "d.",
        "body": "b",
    }
    report = okf_promote.apply_proposals(str(bundle), [proposal], run=engine)
    assert any("실체화 불가" in r for r in report["rejected"][0]["reasons"])
    assert (bundle / "info.md").read_text(encoding="utf-8") == original  # 파일 불변


def test_apply_update_rejects_nonconforming_target(bundle):
    # valid 뷰 밖(규격 미달·비개념)은 빈 행으로 온다 — 반려·파일 불변·집행 기록 없음.
    # 질의 우주가 정말 valid 뷰인지는 test_context_roundtrip.py의 로더 왕복이 잠근다.
    original = (bundle / "info.md").read_text(encoding="utf-8")
    engine = FakeEngine(bundle, query_rows=[])
    proposal = {
        "mode": "update",
        "path": "/info.md",
        "type": "fact",
        "description": "d.",
        "body": "b",
    }
    report = okf_promote.apply_proposals(str(bundle), [proposal], run=engine)
    assert any("개념 아님" in r for r in report["rejected"][0]["reasons"])
    assert (bundle / "info.md").read_text(encoding="utf-8") == original  # 파일 불변
    ops = [call[0] for call in engine.calls]
    assert "log" not in ops and "index" not in ops


def test_render_updated_concept_empty_derived_is_explicit_clear():
    """미제공 = 유지, 빈 리스트 = 명시적 소거 — #358 리뷰가 물은 계약의 선언.

    상위층은 미접지 게이트(`derived_given`)가 소거를 반려하므로, 이 계약이 실제로
    작동하는 곳은 층 게이트가 없는 대상뿐이다.
    """
    existing = {"type": "fact", "description": "d.", "derived_from": ["/info.md"]}
    proposal = {"type": "fact", "description": "d.", "body": "b"}
    keep = okf_promote.render_updated_concept(SPEC, proposal, dict(existing))
    assert "derived_from:\n  - /info.md" in keep  # 미제공 = 유지
    clear = okf_promote.render_updated_concept(
        SPEC, {**proposal, "derived_from": []}, dict(existing)
    )
    assert "derived_from" not in clear  # 빈 리스트 = 명시적 소거
