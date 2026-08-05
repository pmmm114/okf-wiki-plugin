"""근사중복 어휘 겹침 자문 테스트 (#392 — SimHash 대체).

토크나이저 계약·순위(재서술 < 무관)·대칭·판정 불가 제외를 고정하고, 자문이 정확 해시
dedup/원장 앵커를 대체하지 않음을 잠근다. 이전 SimHash 구현의 계약 중 살아남은 것은
그대로 이관했고, 지문 저장에 딸린 것(hex 폭·해밍 비트수)은 사라졌다.
"""

from __future__ import annotations

import json
import sqlite3

import okf_vault
import pytest
import study
import study_inbox
import study_overlap
import study_scope
import study_store


def _index(texts):
    return study_overlap.build_index(texts)


# --- 토크나이저 (SimHash에서 이관) -------------------------------------------


def test_tokens_split_unsegmented_scripts_into_bigrams():
    # 공백이 어절을 가르지 않는 문자군은 bigram — 조사·어미가 붙어도 특징을 공유한다
    left = set(study_overlap.tokens("머지 전에는 반드시 스쿼시한다"))
    right = set(study_overlap.tokens("머지 전에 절대 스쿼시하지 않는다"))
    assert left & right, "굴절 차이로 공통 특징이 0이 됐다"


def test_tokens_keep_frequency():
    # BM25는 TF를 쓴다 — 집합이 아니라 목록이어야 한다
    assert study_overlap.tokens("alpha alpha beta").count("alpha") == 2


def test_tokens_empty_for_untokenized_scripts():
    # 토크나이저가 다루지 않는 문자군(키릴)은 토큰 0개 = 판정 불가
    assert study_overlap.tokens("Привет мир вот текст") == []


def test_index_excludes_tokenless_candidates():
    """판정 불가는 **부재**로 다룬다 — 값으로 접으면 무관한 것들이 한데 묶인다(#306)."""
    index = _index({"a": "alpha beta", "b": "Привет мир", "c": "alpha gamma"})
    assert set(index["tf"]) == {"a", "c"}
    assert study_overlap.nearest(index, "b") == []
    assert all(hit["id"] != "b" for hit in study_overlap.nearest(index, "a"))


# --- 순위 계약 ----------------------------------------------------------------


def test_reordered_tokens_are_closest():
    # bag-of-words라 어순만 다른 것은 최상위 — 재서술 자문의 기본 성질
    index = _index(
        {"a": "alpha beta gamma", "b": "gamma beta alpha", "c": "completely different words"}
    )
    assert study_overlap.nearest(index, "a")[0]["id"] == "b"


def test_reworded_ranks_above_unrelated():
    index = _index(
        {
            "base": "머지 전에는 반드시 스쿼시한다",
            "reworded": "머지 전에 반드시 스쿼시를 한다",
            "unrelated": "토마토는 과일 샐러드에 넣지 않는다",
        }
    )
    ranked = [hit["id"] for hit in study_overlap.nearest(index, "base")]
    assert ranked[0] == "reworded", ranked


def test_field_is_overlap_not_similarity():
    """이름이 계약이다 — 이 값은 어휘 겹침이지 의미 유사도가 아니다(#387).

    ``similarity``로 부르면 읽는 쪽이 "의미가 같다"로 받아들여 판정 근거로 삼는다.
    실측에서 의미가 정반대인 쌍이 같은 의미인 쌍보다 5~7배 높은 점수를 받았다.
    """
    hit = study_overlap.nearest(_index({"a": "alpha beta", "b": "alpha gamma"}), "a")[0]
    assert set(hit) == {"id", "overlap"}


def test_ranking_is_symmetric():
    """대칭 평균이라 A의 목록에 B가 있으면 B의 목록에도 A가 있다.

    비대칭 BM25(질의→문서)는 실측 상호성이 65.3%였다 — 일괄 뷰에서 한쪽만 뜨는 쌍이
    3분의 1이면 목록으로 읽기 어렵다.
    """
    texts = {
        "long": "alpha beta gamma delta epsilon zeta eta theta iota kappa alpha beta",
        "short": "alpha beta",
        "other": "completely unrelated words here",
    }
    index = _index(texts)
    top = {k: [h["id"] for h in study_overlap.nearest(index, k, top_k=1)] for k in texts}
    assert "short" in top["long"] and "long" in top["short"], top


def test_ties_break_by_id_deterministically():
    # 동률에서도 순서가 갈리지 않는다 — id 오름차순(형식 가정 없음)
    index = _index({"q": "alpha beta", "zzz": "alpha beta", "aaa": "alpha beta"})
    assert [h["id"] for h in study_overlap.nearest(index, "q")] == ["aaa", "zzz"]


# --- inbox 배선 ---------------------------------------------------------------


def test_near_duplicates_surfaces_reordered_candidate(tmp_path):
    a = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    b = study_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    study_inbox.append(tmp_path, "completely different words here", "M.md")
    near = study_inbox.near_duplicates(tmp_path, a, top_k=5)
    assert [h["id"] for h in near][0] == b
    assert near[0]["overlap"] > 0


def test_zero_overlap_candidates_drop_out_of_the_list(tmp_path):
    """공통 토큰이 하나도 없는 후보는 목록에 **오르지 않는다**(#392 계약 변경).

    SimHash는 모든 쌍에 거리가 있어 무관해도 목록을 채웠다. 겹침은 0이면 보여 줄 것이
    없다 — 목록이 짧다는 것 자체가 "겹치는 게 없다"는 정보이고, 무관한 후보로 K를
    채우면 사람이 읽어야 할 줄만 늘어난다.

    실무에서는 이 상황이 사실상 오지 않는다(실측 후보 2198건 전부가 상위 5를 채웠다) —
    한국어 bigram은 조사·어미가 흔해 어떤 두 문장이든 무언가는 겹치기 때문이다.
    """
    a = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    b = study_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    study_inbox.append(tmp_path, "completely different words here", "M.md")

    near = study_inbox.near_duplicates(tmp_path, a, top_k=5)
    assert [hit["id"] for hit in near] == [b], "겹침 0인 후보가 목록을 채웠다"


def test_overlapping_candidate_always_surfaces(tmp_path):
    """겹침이 있으면 **반드시** 나온다 — #306이 고친 증상의 회귀 방지.

    임계 시절에는 재서술 쌍조차 걸러져 자문이 사실상 항상 비었고, 그 빈 결과가
    '무검사'가 아니라 '근사중복 없음'으로 읽혔다. 겹침 기반에는 임계가 없다.
    """
    ident = study_inbox.append(tmp_path, "머지 전에는 반드시 스쿼시한다", "M.md")
    twin = study_inbox.append(tmp_path, "머지 전에 절대 스쿼시하지 않는다", "M.md")

    near = study_inbox.near_duplicates(tmp_path, ident, top_k=5)
    assert [hit["id"] for hit in near] == [twin]


def test_near_duplicates_is_advisory_only(tmp_path):
    # 자문은 dedup/원장에 영향 없음 — 정확 해시 앵커 불변
    a = study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    study_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    assert study_inbox.is_resolved(tmp_path, a) is False
    assert len(study_inbox.list_candidates(tmp_path)) == 2  # 근사중복이라도 별개 후보


def test_no_fingerprint_column_is_written(tmp_path):
    """지문을 저장하지 않는다 — BM25는 코퍼스 통계라 미리 계산할 수 없다(#392).

    저장이 사라지면 저장분과 원문 재계산이 어긋나는 소급 결함도 사라진다(실측 105건).
    """
    study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    with sqlite3.connect(tmp_path / "study.db") as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(candidate)")}
    assert "simhash" not in cols, cols


def test_near_duplicates_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox.study_store, "sqlite3", None)
    assert study_inbox.near_duplicates(tmp_path, "whatever") == []


def test_near_duplicate_pairs_empty_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(study_inbox.study_store, "sqlite3", None)
    assert study_inbox.near_duplicate_pairs(tmp_path, ["whatever"]) == {}


# --- 일괄 뷰의 코퍼스 확보 (#382 계약 유지) ------------------------------------
#
# 일괄 뷰는 코퍼스를 **1회 확보**하고 메모리에서 비교한다. 계약은 둘이다: 출력이 단건
# 경로와 **같을 것**, 그리고 로드가 **1회일 것**. 두 경로는 헬퍼를 공유하지 않는다 —
# 단건은 쌍별 역방향(`_score`), 일괄은 전치 재사용이라 실제로 다른 코드가 돈다.


def _count_corpus_loads(monkeypatch) -> list:
    calls: list = []
    original = study_store.list_snippets

    def counted(runtime):
        calls.append(runtime)
        return original(runtime)

    monkeypatch.setattr(study_store, "list_snippets", counted)
    return calls


def test_near_duplicate_pairs_equals_per_candidate_single_query(tmp_path):
    """일괄 뷰 출력 == 후보마다 단건 자문을 부른 출력(순서·값·상위 K 포함).

    대조만으로는 부족하다 — 두 경로가 같은 색인을 쓰므로 색인이 틀리면 양쪽이 똑같이
    틀린다. 판정 불가 제외와 상위 K 절단은 참조 대조가 아니라 **값으로 직접** 단언한다.
    """
    study_inbox.append(tmp_path, "alpha beta gamma", "M.md")
    study_inbox.append(tmp_path, "gamma beta alpha", "M.md")
    study_inbox.append(tmp_path, "completely different words here", "M.md")
    study_inbox.append(tmp_path, "머지 전에는 반드시 스쿼시한다", "M.md")
    study_inbox.append(tmp_path, "브랜치 이름은 소문자와 하이픈만 쓴다", "M.md")
    cyrillic = study_inbox.append(tmp_path, "Привет мир вот текст", "M.md")  # 토큰 0개
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
    assert all(len(near) <= 3 for near in pairs.values())  # 후보 5건 > K=3 → 절단이 발동한다


def test_near_duplicate_pairs_loads_corpus_once(monkeypatch, tmp_path):
    for snippet in ("alpha beta gamma", "gamma beta alpha", "delta epsilon zeta"):
        study_inbox.append(tmp_path, snippet, "M.md")
    idents = [c["id"] for c in study_inbox.list_candidates(tmp_path)]
    calls = _count_corpus_loads(monkeypatch)

    study_inbox.near_duplicate_pairs(tmp_path, idents, top_k=3)

    assert len(calls) == 1, (
        f"후보 {len(idents)}건에 코퍼스 로드 {len(calls)}회 — 후보마다 재로드한다"
    )


# --- CLI ----------------------------------------------------------------------


def _near_cli_project(monkeypatch, tmp_path):
    """`study near`가 도는 최소 프로젝트 — ``(project, 첫 후보 id)``."""
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
    study_inbox.append(rt, "gamma beta alpha", "M.md")
    study_inbox.append(rt, "completely different words here", "M.md")
    return project, first


def test_study_near_cli(monkeypatch, tmp_path, capsys):
    project, a = _near_cli_project(monkeypatch, tmp_path)

    assert study.main(["near", str(project), "--top-k", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert a in out and out[a]
    assert "overlap" in out[a][0]


def test_study_near_cli_loads_corpus_once(monkeypatch, tmp_path, capsys):
    """CLI 경로도 1회 — `cmd_near`가 단건 루프로 되돌아가면 여기서 붉어진다."""
    project, _first = _near_cli_project(monkeypatch, tmp_path)
    calls = _count_corpus_loads(monkeypatch)

    assert study.main(["near", str(project), "--top-k", "5"]) == 0

    capsys.readouterr()
    assert len(calls) == 1, f"후보 3건에 코퍼스 로드 {len(calls)}회"


# --- 실측 재서술 쌍 (#306 · #387) ----------------------------------------------
#
# 한국어는 bigram 토큰이라 어순·조사만 바뀌어도 SimHash 지문이 크게 흩어졌다. 실측 거리
# 11~32로 임계 3에 드는 것이 하나도 없었고, `study near`가 사실상 항상 빈 결과를 냈다.
# 상위 K는 그 성질 위에 임계를 얹지 않는다. BM25로 바꾼 뒤에도 임계는 여전히 불가다 —
# 같은 쌍의 최솟값이 무관 쌍의 최댓값보다 낮다(#387 교차검증).

RESTATEMENT_PAIRS = [
    ("훅은 절대 승격·디스패치하지 않는다", "훅이 승격이나 디스패치를 하는 일은 없다"),
    ("판정 상수는 rules/v0_1.json이 단일원천이다", "판정 상수의 단일 원천은 rules/v0_1.json이다"),
    ("파스는 파일당 한 번만 수행한다", "파일마다 파싱은 1회로 제한된다"),
    ("엔진은 Claude를 알지 못한다", "엔진 쪽에서 Claude를 참조하면 안 된다"),
]


@pytest.mark.parametrize(("a", "b"), RESTATEMENT_PAIRS, ids=range(len(RESTATEMENT_PAIRS)))
def test_restatement_pairs_surface_in_top_k(a, b, tmp_path):
    """재서술 쌍이 **상위 K 자문에 값 표기와 함께** 나온다.

    임계 3이었다면 전부 탈락했다(실측 11~32). 상위 K는 순위라 겹침이 적어도 목록에
    오르고, 그 값을 사람·모델이 본다 — 자문의 본래 계약이다.
    """
    ident = study_inbox.append(tmp_path, a, "M.md")
    study_inbox.append(tmp_path, b, "M.md")
    study_inbox.append(tmp_path, "전혀 무관한 다른 이야기입니다", "M.md")

    near = study_inbox.near_duplicates(tmp_path, ident, top_k=5)
    assert near, "자문이 통째로 비었다 — 임계 시절의 증상"
    assert all("overlap" in hit for hit in near), near


@pytest.mark.parametrize(("a", "b"), RESTATEMENT_PAIRS, ids=range(len(RESTATEMENT_PAIRS)))
def test_restatement_pair_ranks_first(a, b, tmp_path):
    """재서술본이 무관 후보를 **제치고 1위**다 — 지표 교체의 실익이 걸린 계약.

    SimHash는 여기서 무너졌다(#387 실측: 재서술 8문장 풀에서 최근접 1위 3/8, R@1 0.164).
    """
    ident = study_inbox.append(tmp_path, a, "M.md")
    twin = study_inbox.append(tmp_path, b, "M.md")
    study_inbox.append(tmp_path, "전혀 무관한 다른 이야기입니다", "M.md")
    study_inbox.append(tmp_path, "점심에는 김치찌개를 먹었다", "M.md")

    near = study_inbox.near_duplicates(tmp_path, ident, top_k=5)
    assert near[0]["id"] == twin, [(h["id"], h["overlap"]) for h in near]


def test_threshold_cannot_separate_restatements_from_unrelated():
    """어떤 절대 임계도 진짜와 가짜를 가르지 못한다 — 임계 재도입 금지의 근거(#387).

    같은 쌍의 최솟값이 무관 쌍의 최댓값보다 낮다. 지표를 바꿔도 이 성질은 남는다.
    """
    texts = {
        f"{i}{side}": text
        for i, pair in enumerate(RESTATEMENT_PAIRS)
        for side, text in zip("ab", pair)
    }
    index = _index(texts)
    same, cross = [], []
    for i in range(len(RESTATEMENT_PAIRS)):
        for hit in study_overlap.nearest(index, f"{i}a", top_k=len(texts)):
            (same if hit["id"] == f"{i}b" else cross).append(hit["overlap"])
    assert min(same) < max(cross), (min(same), max(cross))
