"""개념 블록 원자 + 캡처 경로 통일 + A2′ 자식 병존 테스트 (U2, #131).

- concept_blocks 경계 규칙(불릿 그룹핑·문단·헤딩/빈 줄 구분자)
- 다중 줄 개념 = 후보 1개(과집계 해소)
- 훅이 마지막 줄만이 아니라 모든 블록을 적재(과소 캡처 해소)
- 훅·scan 동일 후보 집합(불일치 회귀 차단)
- 블록/자식 원장 연속성: 혼합-이력은 리뷰로, 전부 처리면 skip, 과거 줄-id 재부상 차단
"""

from __future__ import annotations

import json

import okf_home
import okf_inbox
import pytest
import study
import study_blocks
import study_hook

MEM = "/home/u/.claude/projects/proj/memory/MEMORY.md"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv(okf_home.POINTER_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _cfg(project, capture="review"):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": []}}), encoding="utf-8"
    )


def _rt(project):
    return okf_home.resolve_capture(project)["runtime_root"]


# --- 블록 경계 규칙 ---------------------------------------------------------


def test_flat_bullets_are_separate_blocks():
    text = "## H\n- fact one\n- fact two\n- fact three\n"
    assert study_blocks.concept_blocks(text) == [["fact one"], ["fact two"], ["fact three"]]


def test_bullet_with_subbullets_is_one_block():
    text = "- decision X\n  - because Y\n  - fallback Z\n"
    assert study_blocks.concept_blocks(text) == [["decision X", "because Y", "fallback Z"]]


def test_prose_paragraph_is_one_block():
    text = "first line\nsecond line\n\nnext para\n"
    assert study_blocks.concept_blocks(text) == [["first line", "second line"], ["next para"]]


def test_heading_and_blank_are_separators():
    assert study_blocks.concept_blocks("# T\n- a\n\n## Sub\n- b\n") == [["a"], ["b"]]


def test_pure_headings_yield_nothing():
    assert study_blocks.concept_blocks("# only\n## headings\n") == []


# --- 원자·자식(A2′) ---------------------------------------------------------


def test_multiline_block_is_single_candidate(tmp_path):
    block = ["decision X", "because Y"]
    lh = [okf_inbox.content_hash(line)[:12] for line in block]
    okf_inbox.append(tmp_path, " ".join(block), "M.md", line_hashes=lh)
    cands = okf_inbox.list_candidates(tmp_path)
    assert len(cands) == 1  # 두 줄이 한 후보로(과집계 해소)
    assert okf_inbox.candidate_lines(tmp_path, cands[0]["id"]) == lh


def test_block_resolved_only_when_all_children_resolved(tmp_path):
    block = ["line a", "line b"]
    lh = [okf_inbox.content_hash(line)[:12] for line in block]
    bid = okf_inbox.content_hash(" ".join(block))[:12]
    assert okf_inbox.block_resolved(tmp_path, bid, lh) is False
    okf_inbox.record(tmp_path, lh[0], "promoted")
    assert okf_inbox.block_resolved(tmp_path, bid, lh) is False  # 혼합 → 리뷰로
    okf_inbox.record(tmp_path, lh[1], "discarded")
    assert okf_inbox.block_resolved(tmp_path, bid, lh) is True  # 전부 처리 → skip


def test_promote_records_children_and_blocks_resurface(tmp_path):
    block = ["shared fact", "other fact"]
    lh = [okf_inbox.content_hash(line)[:12] for line in block]
    bid = okf_inbox.content_hash(" ".join(block))[:12]
    okf_inbox.append(tmp_path, " ".join(block), "M.md", line_hashes=lh)
    okf_inbox.record(tmp_path, bid, "promoted", ".okf/x.md")
    # 승격 시 자식 줄도 원장에 기록된다(A2′ 연속성)
    assert okf_inbox.is_resolved(tmp_path, lh[0]) and okf_inbox.is_resolved(tmp_path, lh[1])
    # 그 줄만 담은 새 블록 → 전부 resolved → 재부상 안 함
    only_lh = [okf_inbox.content_hash("shared fact")[:12]]
    only_bid = okf_inbox.content_hash("shared fact")[:12]
    assert okf_inbox.block_resolved(tmp_path, only_bid, only_lh) is True
    # 그 줄 + 신규 줄 → 혼합 → 리뷰로 올리되 아는 줄은 표식
    mixed = ["shared fact", "brand new fact"]
    mixed_lh = [okf_inbox.content_hash(m)[:12] for m in mixed]
    mixed_bid = okf_inbox.content_hash(" ".join(mixed))[:12]
    assert okf_inbox.block_resolved(tmp_path, mixed_bid, mixed_lh) is False
    okf_inbox.append(tmp_path, " ".join(mixed), "M.md", line_hashes=mixed_lh)
    assert okf_inbox.block_known_lines(tmp_path, mixed_bid) == [
        okf_inbox.content_hash("shared fact")[:12]
    ]


# --- 캡처 경로 통일 ---------------------------------------------------------


def test_hook_captures_all_blocks_not_just_last(tmp_path):
    _cfg(tmp_path, "review")
    content = "## Notes\n- decision X\n  - because Y\n- separate fact\n"
    study_hook.run({"tool_input": {"file_path": MEM, "content": content}}, tmp_path)
    snippets = sorted(c["snippet"] for c in okf_inbox.list_candidates(_rt(tmp_path)))
    # 마지막 줄만이 아니라 두 블록 모두 + 다중 줄은 하나로 묶임
    assert snippets == ["decision X because Y", "separate fact"]


def test_hook_and_scan_agree_on_block_ids(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    memdir = cfg / "projects" / "p" / "memory"
    memdir.mkdir(parents=True)
    content = "## N\n- alpha fact\n- beta fact\n  - beta detail\n"
    memfile = memdir / "MEMORY.md"
    memfile.write_text(content, encoding="utf-8")
    _cfg(tmp_path, "review")
    rt = _rt(tmp_path)

    scan_ids = sorted(c["id"] for c in study.scan_memory(tmp_path, rt, enqueue=False)["unqueued"])
    study_hook.run({"tool_input": {"file_path": str(memfile), "content": content}}, tmp_path)
    hook_ids = sorted(c["id"] for c in okf_inbox.list_candidates(rt))
    assert scan_ids == hook_ids and len(hook_ids) == 2  # {alpha}, {beta + detail}
