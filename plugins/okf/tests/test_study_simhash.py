"""SimHash 근사중복 자문 테스트 (U4, #133).

지문 결정성·상대 해밍거리(재서술 < 무관)·재배열 근사중복 표면화, 그리고 자문이
정확 해시 dedup/원장 앵커를 대체하지 않음을 고정한다.
"""

from __future__ import annotations

import okf_inbox
import study_simhash


def test_fingerprint_deterministic_and_hex_width():
    assert study_simhash.fingerprint("some text") == study_simhash.fingerprint("some text")
    hx = study_simhash.fingerprint_hex("some text")
    assert len(hx) == 16 and int(hx, 16) == study_simhash.fingerprint("some text")


def test_empty_text_is_zero():
    assert study_simhash.fingerprint("") == 0
    assert study_simhash.fingerprint_hex("") == "0" * 16


def test_reordered_tokens_same_fingerprint():
    # SimHash는 토큰 집합 기반 — 어순만 다르면 지문이 같다(해밍 0)
    a = study_simhash.fingerprint("alpha beta gamma")
    b = study_simhash.fingerprint("gamma beta alpha")
    assert study_simhash.hamming(a, b) == 0


def test_reworded_closer_than_unrelated():
    fp = study_simhash.fingerprint
    base = "the quick brown fox jumps over the lazy dog in the yard"
    reworded = "the quick brown fox jumps over the lazy dog in the garden"
    unrelated = "database indexes accelerate query execution over large tables"
    assert study_simhash.hamming(fp(base), fp(base)) == 0
    assert study_simhash.hamming(fp(base), fp(reworded)) < study_simhash.hamming(
        fp(base), fp(unrelated)
    )


def test_near_duplicates_surfaces_same_fingerprint(tmp_path):
    a = okf_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    b = okf_inbox.append(tmp_path, "gamma beta alpha", "M.md")  # 재배열 → 지문 동일
    okf_inbox.append(tmp_path, "completely different words here", "M.md")  # 무관
    near = okf_inbox.near_duplicates(tmp_path, a, threshold=0)
    assert b in near  # 근사중복 자문에 표면화
    assert len(near) == 1  # 무관 후보는 제외


def test_near_duplicates_is_advisory_only(tmp_path):
    # 자문은 dedup/원장에 영향 없음 — 정확 해시 앵커 불변
    a = okf_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    okf_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    assert okf_inbox.is_resolved(tmp_path, a) is False
    assert len(okf_inbox.list_candidates(tmp_path)) == 2  # 근사중복이라도 별개 후보


def test_near_duplicates_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(okf_inbox.study_store, "sqlite3", None)
    assert okf_inbox.near_duplicates(tmp_path, "whatever") == []
