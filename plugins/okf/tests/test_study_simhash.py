"""SimHash 근사중복 자문 테스트 (U4, #133).

지문 결정성·상대 해밍거리(재서술 < 무관)·재배열 근사중복 표면화, 그리고 자문이
정확 해시 dedup/원장 앵커를 대체하지 않음을 고정한다.
"""

from __future__ import annotations

import json

import okf_vault
import study
import study_inbox
import study_scope
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


# --- 비ASCII 특징 추출 -------------------------------------------------------
# 토크나이저가 ASCII 전용이면 한국어 본문은 토큰 0개 → 지문 0으로 접힌다. 그러면
# 자문이 "빈 결과"가 아니라 **무관 후보를 해밍거리 0으로 잡는 오탐기**가 된다
# (0끼리 비교하므로). 승격 절차가 이 대조를 의무 단계로 두므로(LAYERS.md §9) 고정한다.


def test_non_ascii_text_is_tokenized():
    text = "머지 전에는 반드시 스쿼시한다"
    assert study_simhash._tokens(text), "비ASCII 본문에서 토큰이 사라진다"
    assert study_simhash.fingerprint(text) != 0


def test_distinct_non_ascii_texts_are_not_all_zero():
    # 전부 0으로 접히면 무관한 둘이 해밍거리 0(= 완전 동일)로 보고된다
    a = study_simhash.fingerprint("머지 전에는 반드시 스쿼시한다")
    b = study_simhash.fingerprint("토마토는 과일 샐러드에 넣지 않는다")
    assert a != 0 and b != 0
    assert study_simhash.hamming(a, b) > 0


def test_non_ascii_shares_features_across_inflection():
    # 어절 단위 토큰은 조사·어미가 붙어 "스쿼시한다"/"스쿼시하지"를 공통점 0으로 본다.
    # 재서술 근사중복이 주 용도이므로 어미 변화를 넘는 공통 특징이 남아야 한다.
    left = set(study_simhash._tokens("머지 전에는 반드시 스쿼시한다"))
    right = set(study_simhash._tokens("머지 전에 절대 스쿼시하지 않는다"))
    assert left & right


def test_non_ascii_reworded_closer_than_unrelated():
    fp = study_simhash.fingerprint
    base = "파스는 parser.parse로 파일당 1회만 하고 소비자는 ParsedDoc을 재사용한다"
    reworded = "파싱은 parser.parse에서 파일마다 한 번만 수행하며 소비 측은 ParsedDoc을 다시 쓴다"
    unrelated = "브랜치 이름은 소문자와 숫자, 하이픈만 쓰고 태스크 코드도 소문자로 적는다"
    assert study_simhash.hamming(fp(base), fp(reworded)) < study_simhash.hamming(
        fp(base), fp(unrelated)
    )


def test_hamming_counts_differing_bits():
    assert study_simhash.hamming(0, 0) == 0
    assert study_simhash.hamming(0b1011, 0b1001) == 1
    assert study_simhash.hamming(0b1111, 0b0000) == 4


def test_near_duplicates_surfaces_same_fingerprint(tmp_path):
    a = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    b = study_inbox.append(tmp_path, "gamma beta alpha", "M.md")  # 재배열 → 지문 동일
    study_inbox.append(tmp_path, "completely different words here", "M.md")  # 무관
    near = study_inbox.near_duplicates(tmp_path, a, threshold=0)
    assert b in near  # 근사중복 자문에 표면화
    assert len(near) == 1  # 무관 후보는 제외


def test_near_duplicates_is_advisory_only(tmp_path):
    # 자문은 dedup/원장에 영향 없음 — 정확 해시 앵커 불변
    a = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    study_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    assert study_inbox.is_resolved(tmp_path, a) is False
    assert len(study_inbox.list_candidates(tmp_path)) == 2  # 근사중복이라도 별개 후보


def test_near_duplicates_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox.study_store, "sqlite3", None)
    assert study_inbox.near_duplicates(tmp_path, "whatever") == []


def test_study_near_cli(monkeypatch, tmp_path, capsys):
    # 실측: `study near` 서브커맨드가 근사중복 쌍을 JSON으로 낸다(#133 U6)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    rt = study_scope.resolve_capture(project)["runtime_root"]
    a = study_inbox.append(rt, "alpha beta gamma", "M.md")
    study_inbox.append(rt, "gamma beta alpha", "M.md")  # 재배열 → 지문 동일

    assert study.main(["near", str(project), "--threshold", "0"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert a in out and out[a]  # 근사중복 쌍이 잡힌다
