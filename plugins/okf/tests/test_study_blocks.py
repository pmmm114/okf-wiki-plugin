"""개념 블록 원자 + 캡처 경로 통일 + A2′ 자식 병존 테스트 (U2, #131).

- concept_blocks 경계 규칙(불릿 그룹핑·문단·헤딩/빈 줄 구분자)
- 다중 줄 개념 = 후보 1개(과집계 해소)
- 훅이 마지막 줄만이 아니라 모든 블록을 적재(과소 캡처 해소)
- 훅·scan 동일 후보 집합(불일치 회귀 차단)
- 블록/자식 원장 연속성: 혼합-이력은 리뷰로, 전부 처리면 skip, 과거 줄-id 재부상 차단
"""

from __future__ import annotations

import json
import os

import okf_vault
import pytest
import study
import study_blocks
import study_hook
import study_inbox
import study_scope
import study_store


def _mem() -> str:
    """캡처 입구로 인정되는 메모리 경로 — **실행 시점 HOME 기준**.

    예전에는 `/home/u/…` 리터럴이었다. 레거시 느슨형 정규식에 앵커가 없어 어느
    프리픽스든 통과했기 때문이다(#305에서 홈·설정 디렉토리 하위로 앵커). 리터럴로
    두면 테스트가 그 느슨함 자체를 계약으로 고정하게 된다.
    """
    return os.path.join(
        os.path.expanduser("~"), ".claude", "projects", "proj", "memory", "MEMORY.md"
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-vault"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def _cfg(project, capture="review"):
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": capture, "handlers": []}}), encoding="utf-8"
    )


def _rt(project):
    return study_scope.resolve_capture(project)["runtime_root"]


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


# --- 노이즈 필터 (#256) ------------------------------------------------------


def test_leading_frontmatter_fence_is_skipped():
    text = "---\nname: some-slug\ndescription: d\n---\n\n- real fact\n"
    assert study_blocks.concept_blocks(text) == [["real fact"]]


def test_body_attached_to_closing_fence_is_preserved():
    # 닫는 펜스 직후 빈 줄 없이 본문이 붙어도 본문은 독립 블록으로 살아남는다 —
    # 텍스트 패턴 판정이었다면 펜스+본문 한 블록을 통째로 버렸을 케이스
    text = "---\nname: x\n---\nreal fact right after fence\n"
    assert study_blocks.concept_blocks(text) == [["real fact right after fence"]]


def test_unclosed_leading_fence_is_not_frontmatter():
    # 닫는 펜스가 없으면 frontmatter가 아니다 — 본문은 보수적으로 유지
    text = "---\nnot frontmatter, just prose\n"
    assert study_blocks.concept_blocks(text) == [["not frontmatter, just prose"]]


def test_bare_rule_is_separator_not_content():
    text = "- fact one\n---\n- fact two\n"
    assert study_blocks.concept_blocks(text) == [["fact one"], ["fact two"]]
    # 산문 사이 수평선도 내용이 아니라 경계다
    assert study_blocks.concept_blocks("para one\n----\npara two\n") == [
        ["para one"],
        ["para two"],
    ]


def test_diff_style_content_line_is_preserved():
    # '--- a/file'처럼 내용이 있는 줄은 수평선이 아니다(위치 기준 필터의 안전 범위)
    text = "--- a/some/file.py 헤더를 인용한 메모\n"
    assert study_blocks.concept_blocks(text) == [["--- a/some/file.py 헤더를 인용한 메모"]]


def test_label_only_blocks_are_dropped():
    # 라벨-단독 고정 셋({Why, How to apply}, 콜론 볼드 안/밖 변형 포함)만 제외
    text = (
        "**Why:**\n\n실제 근거 설명\n\n**How to apply:**\n\n적용 방법 설명\n\n**How to apply**:\n"
    )
    assert study_blocks.concept_blocks(text) == [["실제 근거 설명"], ["적용 방법 설명"]]


def test_indented_rule_stays_in_block():
    # 들여쓴 '---'는 블록 내용의 일부 — 최상위 수평선만 구분자다. 다중 줄 블록을
    # 중간에서 쪼개면 블록 id가 바뀌어 기존 인박스·원장 dedup과 어긋난다(DA 재현)
    text = "- fact\n  - detail A\n  ---\n  - detail B\n"
    assert study_blocks.concept_blocks(text) == [["fact", "detail A", "---", "detail B"]]


def test_bom_prefixed_frontmatter_is_skipped():
    # UTF-8 BOM이 붙은 파일에서도 선두 펜스 판정이 무력화되지 않는다(DA 재현)
    text = "﻿---\nname: x\n---\n- real fact\n"
    assert study_blocks.concept_blocks(text) == [["real fact"]]


def test_label_marker_variants_are_dropped():
    # 마커 개수 변형(***X:***·*X*)도 같은 라벨 키로 정규화돼 필터를 우회하지 못한다(DA 재현)
    assert study_blocks.concept_blocks("***Why:***\n") == []
    assert study_blocks.concept_blocks("*Why*\n") == []


def test_label_like_real_fact_is_preserved():
    # 라벨처럼 보여도 내용이 있는 단일 줄 블록은 실사실 — 일반 휴리스틱 기각의 근거
    text = "**동기화는 merge가 상시 관례(예외 아님):**\n"
    assert study_blocks.concept_blocks(text) == [["**동기화는 merge가 상시 관례(예외 아님):**"]]


# --- 원자·자식(A2′) ---------------------------------------------------------


def test_multiline_block_is_single_candidate(tmp_path):
    block = ["decision X", "because Y"]
    lh = [study_inbox.content_hash(line)[:12] for line in block]
    study_inbox.append(tmp_path, " ".join(block), "M.md", line_hashes=lh)
    cands = study_inbox.list_candidates(tmp_path)
    assert len(cands) == 1  # 두 줄이 한 후보로(과집계 해소)
    assert study_store.candidate_lines(tmp_path, cands[0]["id"]) == lh


def test_block_resolved_only_when_all_children_resolved(tmp_path):
    block = ["line a", "line b"]
    lh = [study_inbox.content_hash(line)[:12] for line in block]
    bid = study_inbox.content_hash(" ".join(block))[:12]
    assert study_inbox.block_resolved(tmp_path, bid, lh) is False
    study_inbox.record(tmp_path, lh[0], "promoted")
    assert study_inbox.block_resolved(tmp_path, bid, lh) is False  # 혼합 → 리뷰로
    study_inbox.record(tmp_path, lh[1], "discarded")
    assert study_inbox.block_resolved(tmp_path, bid, lh) is True  # 전부 처리 → skip


def test_promote_records_children_and_blocks_resurface(tmp_path):
    block = ["shared fact", "other fact"]
    lh = [study_inbox.content_hash(line)[:12] for line in block]
    bid = study_inbox.content_hash(" ".join(block))[:12]
    study_inbox.append(tmp_path, " ".join(block), "M.md", line_hashes=lh)
    study_inbox.record(tmp_path, bid, "promoted", ".okf/x.md")
    # 승격 시 자식 줄도 원장에 기록된다(A2′ 연속성)
    assert study_inbox.is_resolved(tmp_path, lh[0]) and study_inbox.is_resolved(tmp_path, lh[1])
    # 그 줄만 담은 새 블록 → 전부 resolved → 재부상 안 함
    only_lh = [study_inbox.content_hash("shared fact")[:12]]
    only_bid = study_inbox.content_hash("shared fact")[:12]
    assert study_inbox.block_resolved(tmp_path, only_bid, only_lh) is True
    # 그 줄 + 신규 줄 → 혼합 → 리뷰로 올린다
    mixed = ["shared fact", "brand new fact"]
    mixed_lh = [study_inbox.content_hash(m)[:12] for m in mixed]
    mixed_bid = study_inbox.content_hash(" ".join(mixed))[:12]
    assert study_inbox.block_resolved(tmp_path, mixed_bid, mixed_lh) is False


# --- 캡처 경로 통일 ---------------------------------------------------------


def test_hook_captures_all_blocks_not_just_last(tmp_path):
    _cfg(tmp_path, "review")
    content = "## Notes\n- decision X\n  - because Y\n- separate fact\n"
    study_hook.run({"tool_input": {"file_path": _mem(), "content": content}}, tmp_path)
    snippets = sorted(c["snippet"] for c in study_inbox.list_candidates(_rt(tmp_path)))
    # 마지막 줄만이 아니라 두 블록 모두 + 다중 줄은 하나로 묶임
    assert snippets == ["decision X because Y", "separate fact"]


def test_hook_and_scan_agree_on_block_ids(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    memdir = cfg / "projects" / "p" / "memory"
    memdir.mkdir(parents=True)
    # 노이즈(frontmatter·라벨-단독) 포함 픽스처 — 필터 후에도 훅·scan 동일 후보 집합(#256)
    content = "---\nname: p\n---\n## N\n- alpha fact\n- beta fact\n  - beta detail\n\n**Why:**\n"
    memfile = memdir / "MEMORY.md"
    memfile.write_text(content, encoding="utf-8")
    _cfg(tmp_path, "review")
    rt = _rt(tmp_path)

    scan_ids = sorted(c["id"] for c in study.scan_memory(tmp_path, rt, enqueue=False)["unqueued"])
    study_hook.run({"tool_input": {"file_path": str(memfile), "content": content}}, tmp_path)
    hook_ids = sorted(c["id"] for c in study_inbox.list_candidates(rt))
    assert scan_ids == hook_ids and len(hook_ids) == 2  # {alpha}, {beta + detail}


# ── 코드 조각 단독 노이즈 (#352) ─────────────────────────────────────────────


def test_code_artifact_blocks_are_dropped():
    # 펜스 마커·닫는 태그 단독 줄은 지식이 아니라 마크업 잔재다(실코퍼스 위양성 0)
    text = "사실 하나\n\n```tsx\n\n</Flex>\n\n~~~bash\n\n사실 둘"
    blocks = study_blocks.concept_blocks(text)
    assert [b[0] for b in blocks] == ["사실 하나", "사실 둘"]


def test_inline_code_mentions_are_preserved():
    # 본문 안 백틱·태그 언급은 fullmatch가 아니라 살아남는다
    text = "- ```tsx 펜스는 노이즈로 거른다\n- </Flex>로 닫는 태그를 설명한다"
    assert len(study_blocks.concept_blocks(text)) == 2


def test_noise_snippet_covers_code_artifacts():
    # prune측도 같은 fullmatch — 기적재 잔재(펜스 4·태그 3, 전수 실측)를 잇는다
    assert study_blocks.is_noise_snippet("```bash")
    assert study_blocks.is_noise_snippet("</ModalScreen.Root>")
    assert not study_blocks.is_noise_snippet("```tsx 펜스는 노이즈로 거른다")
