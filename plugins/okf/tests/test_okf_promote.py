"""층간 승격 파이프라인 (Epic #197 U3) — 게이트 차단·집행·롤백 검증.

게이트는 순수 판정(tmp 번들 + 합성 layer_map)으로, 집행은 가짜 엔진 러너 주입으로
hermetic하게 검증한다(엔진 서브프로세스 없음 — 실번들 E2E는 별도 스모크).
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
    def __init__(self, bundle, fail_validate=False):
        self.bundle = str(bundle)
        self.fail_validate = fail_validate
        self.calls: list[list[str]] = []

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
