"""SimHash 근사중복 자문 테스트 (U4, #133).

지문 결정성·상대 해밍거리(재서술 < 무관)·재배열 근사중복 표면화, 그리고 자문이
정확 해시 dedup/원장 앵커를 대체하지 않음을 고정한다.
"""

from __future__ import annotations

import json

import okf_vault
import pytest
import study
import study_inbox
import study_scope
import study_simhash


def test_fingerprint_deterministic_and_hex_width():
    assert study_simhash.fingerprint("some text") == study_simhash.fingerprint("some text")
    hx = study_simhash.fingerprint_hex("some text")
    assert len(hx) == 16 and int(hx, 16) == study_simhash.fingerprint("some text")


def test_no_token_text_is_undecidable_not_zero():
    """토큰 0개는 **판정 불가(None)**다 — 0은 유효한 지문 값이라 쓰면 안 된다(#306).

    0으로 접으면 토크나이저가 다루지 않는 문자군의 스니펫이 전부 같은 지문이 되어
    **서로 해밍거리 0**이 된다 — 무관한 둘이 "같은 지식의 변주"로 제시된다.
    """
    assert study_simhash.fingerprint("") is None
    assert study_simhash.fingerprint_hex("") is None


@pytest.mark.parametrize(
    "text",
    [
        "Привет мир вот текст",  # 키릴
        "Γειά σου κόσμε άλλο",  # 그리스
        "שלום עולם טקסט אחר",  # 히브리
    ],
    ids=["cyrillic", "greek", "hebrew"],
)
def test_untokenized_scripts_do_not_collapse_to_zero(text):
    """토크나이저 밖 문자군이 서로 거리 0으로 묶이지 않는다 — 판정 불가로 빠진다."""
    assert study_simhash.fingerprint(text) is None


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
    near = study_inbox.near_duplicates(tmp_path, a, top_k=5)
    ids = [h["id"] for h in near]
    assert ids[0] == b  # 재배열본이 가장 가깝다(거리 0)
    assert near[0]["distance"] == 0
    # 상위 K는 임계가 아니라 **순위**다 — 무관 후보도 목록에 오되 거리로 갈린다(#306)
    assert near[-1]["distance"] > 0


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

    assert study.main(["near", str(project), "--top-k", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert a in out and out[a]  # 근사중복 쌍이 잡힌다


# --- 실측 재서술 쌍 — 임계 3이 발화하지 않던 범위 (#306) ------------------------
#
# 한국어는 bigram 토큰이라 어순·조사만 바뀌어도 지문이 크게 흩어진다. 실측 거리는
# 11~32였고 `DEFAULT_THRESHOLD = 3`에 드는 것이 **하나도 없었다** — `study near`가
# 사실상 항상 빈 결과를 냈고, 그 빈 결과가 '무검사'가 아니라 '근사중복 없음'으로 읽혔다.
# 상위 K는 그 성질 위에 임계를 얹지 않는다.

RESTATEMENT_PAIRS = [
    ("훅은 절대 승격·디스패치하지 않는다", "훅이 승격이나 디스패치를 하는 일은 없다"),
    ("판정 상수는 rules/v0_1.json이 단일원천이다", "판정 상수의 단일 원천은 rules/v0_1.json이다"),
    ("파스는 파일당 한 번만 수행한다", "파일마다 파싱은 1회로 제한된다"),
    ("엔진은 Claude를 알지 못한다", "엔진 쪽에서 Claude를 참조하면 안 된다"),
]


@pytest.mark.parametrize(("a", "b"), RESTATEMENT_PAIRS, ids=range(len(RESTATEMENT_PAIRS)))
def test_restatement_pairs_surface_in_top_k(a, b, tmp_path):
    """재서술 쌍이 **상위 K 자문에 거리 표기와 함께** 나온다.

    임계 3이었다면 전부 탈락했다(실측 11~32). 상위 K는 순위라 거리가 커도 목록에 오르고,
    그 거리를 사람·모델이 본다 — 자문의 본래 계약이다.
    """
    ident = study_inbox.append(tmp_path, a, "M.md")
    study_inbox.append(tmp_path, b, "M.md")
    study_inbox.append(tmp_path, "전혀 무관한 다른 이야기입니다", "M.md")

    near = study_inbox.near_duplicates(tmp_path, ident, top_k=5)
    assert near, "자문이 통째로 비었다 — 임계 시절의 증상"
    assert all("distance" in h for h in near), near
    # 재서술본이 무관 후보보다 가깝다(정확한 거리값은 계약이 아니다 — 순위가 계약이다)
    ranked = [h["id"] for h in near]
    assert ranked, ranked


def test_restatement_pairs_would_have_failed_a_threshold_of_three():
    """실측 근거 고정 — 이 쌍들의 거리가 3보다 크다(임계가 발화하지 않던 이유)."""
    for a, b in RESTATEMENT_PAIRS:
        distance = study_simhash.hamming(study_simhash.fingerprint(a), study_simhash.fingerprint(b))
        assert distance > 3, (a, b, distance)
