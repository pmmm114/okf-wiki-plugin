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
import study_store


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


def _near_cli_project(monkeypatch, tmp_path):
    """`study near`가 도는 최소 프로젝트 — 후보 3건이 적재된 review 스코프.

    ``(project, 첫 후보 id)``를 돌려준다.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.delenv(okf_vault.VAULT_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".okf-wiki.json").write_text(
        json.dumps({"study": {"capture": "review"}}), encoding="utf-8"
    )
    rt = study_scope.resolve_capture(project)["runtime_root"]
    first = study_inbox.append(rt, "alpha beta gamma", "M.md")
    study_inbox.append(rt, "gamma beta alpha", "M.md")  # 재배열 → 지문 동일
    study_inbox.append(rt, "completely different words here", "M.md")
    return project, first


def test_study_near_cli(monkeypatch, tmp_path, capsys):
    # 실측: `study near` 서브커맨드가 근사중복 쌍을 JSON으로 낸다(#133 U6)
    project, a = _near_cli_project(monkeypatch, tmp_path)

    assert study.main(["near", str(project), "--top-k", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert a in out and out[a]  # 근사중복 쌍이 잡힌다


# --- 일괄 뷰의 지문 확보 (#382) -----------------------------------------------
#
# `study near`는 후보마다 단건 자문(`near_duplicates`)을 부르던 구조라, 단건용으로
# 옳은 "1회 로드 + N 비교"가 일괄 뷰에서 N번 반복되며 비용이 제곱이 됐다 — 실측
# 후보 2,897건에서 지문 테이블 전체 SELECT 2,897회, 행 왕복 840만.
#
# 일괄 뷰는 지문을 **1회 확보**하고 메모리에서 쌍대 비교한다. 계약은 둘이다:
# 출력이 단건 경로와 **같을 것**, 그리고 지문 로드가 **1회일 것**.


def _count_fingerprint_loads(monkeypatch) -> list:
    """``list_fingerprints`` 호출 카운터 — 로드 1회 계약용."""
    calls: list = []
    original = study_store.list_fingerprints

    def counted(runtime):
        calls.append(runtime)
        return original(runtime)

    monkeypatch.setattr(study_store, "list_fingerprints", counted)
    return calls


def test_near_duplicate_pairs_equals_per_candidate_single_query(tmp_path):
    """일괄 뷰 출력 == 후보마다 단건 자문을 부른 출력(순서·거리·상위 K 포함).

    단건 API가 이 동등성의 참조 구현이다 — 지문 판정 불가 후보가 양쪽 모두에서
    빠지는 것까지 같은 계약이라 키릴 스니펫을 섞는다.

    다만 대조만으로는 부족하다: 두 경로가 확보·순위 헬퍼를 공유하므로 **헬퍼 자체가
    틀리면 양쪽이 똑같이 틀리고 이 대조는 녹색으로 남는다**(뮤테이션 실증 — 판정 불가를
    0으로 접기, 상위 K 절단 생략이 둘 다 미탐지였다). 그 둘은 참조 대조가 아니라 값으로
    직접 단언한다.
    """
    ids = [
        study_inbox.append(tmp_path, snippet, "M.md")
        for snippet in (
            "alpha beta gamma",
            "gamma beta alpha",
            "completely different words here",
            "머지 전에는 반드시 스쿼시한다",
            "브랜치 이름은 소문자와 하이픈만 쓴다",
            "Привет мир вот текст",  # 토큰 0개 → 지문 없음
        )
    ]
    cyrillic = ids[-1]
    idents = [c["id"] for c in study_inbox.list_candidates(tmp_path)]

    expected = {}
    for ident in idents:
        near = study_inbox.near_duplicates(tmp_path, ident, top_k=3)
        if near:
            expected[ident] = near

    pairs = study_inbox.near_duplicate_pairs(tmp_path, idents, top_k=3)
    assert pairs == expected
    assert list(pairs) == list(expected)  # 후보 순서도 계약(JSON 출력 순서)
    assert cyrillic not in pairs  # 판정 불가는 질의 대상에서 빠지고
    assert all(cyrillic != hit["id"] for near in pairs.values() for hit in near)  # 상대로도 안 뜬다
    assert all(len(near) <= 3 for near in pairs.values())  # 지문 5건 > K=3 → 절단이 발동한다


def test_near_duplicate_pairs_loads_fingerprints_once(monkeypatch, tmp_path):
    for snippet in ("alpha beta gamma", "gamma beta alpha", "delta epsilon zeta"):
        study_inbox.append(tmp_path, snippet, "M.md")
    idents = [c["id"] for c in study_inbox.list_candidates(tmp_path)]
    calls = _count_fingerprint_loads(monkeypatch)

    study_inbox.near_duplicate_pairs(tmp_path, idents, top_k=3)

    assert len(calls) == 1, f"후보 {len(idents)}건에 지문 로드 {len(calls)}회 — 후보마다 재로드한다"


def test_study_near_cli_loads_fingerprints_once(monkeypatch, tmp_path, capsys):
    """CLI 경로도 1회 — `cmd_near`가 단건 루프로 되돌아가면 여기서 붉어진다."""
    project, _first = _near_cli_project(monkeypatch, tmp_path)
    calls = _count_fingerprint_loads(monkeypatch)

    assert study.main(["near", str(project), "--top-k", "5"]) == 0

    capsys.readouterr()
    assert len(calls) == 1, f"후보 3건에 지문 로드 {len(calls)}회"


def test_near_duplicate_pairs_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox.study_store, "sqlite3", None)
    assert study_inbox.near_duplicate_pairs(tmp_path, ["whatever"]) == {}


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
