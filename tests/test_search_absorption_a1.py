"""Search-absorption A1 tests: product-name axis + identifier pin + negated-product
exclusion, threaded through the personalization pipeline.

Plan: fable_doc/plans/2026-07-23_search_absorption.md §A1.

Three layers:
- candidate_generator: a query pin earns a ``product:<pid>`` master-truth overlap
  (evidence-gate survival), is held back from the ``max_candidates`` retrieval cut,
  and hard filters (avoided / excluded) still beat the pin.
- ``server._rerank_with_pins``: the pin block leads (score order), survives the
  diversity reranker's window/cut, and ``final_rank`` is contiguous with no dupes.
- server e2e (demo mode): the "설화수 윤조에센스 어때" golden pins the essence to
  top-1 in BOTH login and anonymous modes; a negated product name is excluded from
  brand/category results; the pin trace (pinned_product_ids / pinned_dropped) is
  surfaced; a product-pin ask never leaks into a no-query /api/recommend.

The no-query byte-identity + ranking-snapshot invariants are proven by the
untouched existing recommend suites staying green (no fixture here rewrites them).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import CandidateProduct, generate_candidates
from src.rec.ingredient_constraint import IngredientConstraint
from src.rec.query_understanding import QueryInterpretation
from src.rec.reranker import rerank
from src.rec.scorer import ScoredProduct
from src.rec.search import MatchedConcept
from src.web import server
from src.web.state import DemoState


# ===========================================================================
# Layer 1 — candidate_generator pins/exclusions
# ===========================================================================


def _user(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": "u1",
        "preferred_brand_ids": [],
        "active_category_ids": [],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "goal_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_keyword_ids": [],
        "preferred_context_ids": [],
        "owned_product_ids": [],
        "owned_family_ids": [],
        "repurchased_family_ids": [],
        "recent_purchase_brand_ids": [],
        "repurchase_brand_ids": [],
        "repurchase_category_ids": [],
    }
    base.update(overrides)
    return base


def _cg_product(pid: str = "P1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_id": None,
        "category_id": None,
        "ingredient_ids": [],
        "main_benefit_ids": [],
        "brand_concept_ids": [],
        "category_concept_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [],
        "top_keyword_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_comparison_product_ids": [],
        "top_coused_product_ids": [],
        "review_count_all": 0,
    }
    base.update(overrides)
    return base


_BRAND_A = [{"id": "concept:Brand:brand_a", "weight": 1.0}]


def test_cg_pin_survives_evidence_gate():
    """A pin with NO other user-aligned overlap is evidence-qualified via the
    ``product:<pid>`` master-truth overlap (dropped without the pin)."""
    user = _user()
    products = [_cg_product("PIN")]
    # Without a pin → hard-filtered (NO_USER_ALIGNED_EVIDENCE).
    assert generate_candidates(user, products, mode=RecommendationMode.EXPLORE) == []
    # With a pin → the product: overlap qualifies it.
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, query_product_ids={"PIN"}
    )
    assert [c.product_id for c in cands] == ["PIN"]
    assert "product:PIN" in cands[0].overlap_concepts
    assert cands[0].eligibility.eligible is True
    assert "PRODUCT_MASTER_TRUTH" in cands[0].eligibility.evidence_families


def test_cg_pin_survives_retrieval_cut():
    """A pin is held back from the ``max_candidates`` cut (it would otherwise be
    dropped — it has no brand overlap while the tail does)."""
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [_cg_product(f"B{i}", brand_concept_ids=["concept:Brand:brand_a"]) for i in range(5)]
    products.append(_cg_product("PIN"))  # no evidence except the pin
    base = generate_candidates(user, products, mode=RecommendationMode.EXPLORE, max_candidates=3)
    assert "PIN" not in [c.product_id for c in base] and len(base) == 3
    pinned = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, max_candidates=3, query_product_ids={"PIN"}
    )
    ids = [c.product_id for c in pinned]
    assert "PIN" in ids  # preserved beyond the cut
    assert len([p for p in ids if p != "PIN"]) == 3  # non-pin tail still capped


def test_cg_excluded_product_hard_filtered():
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        _cg_product("KEEP", brand_concept_ids=["concept:Brand:brand_a"]),
        _cg_product("DROP", brand_concept_ids=["concept:Brand:brand_a"]),
    ]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, excluded_product_ids={"DROP"}
    )
    assert [c.product_id for c in cands] == ["KEEP"]


def test_cg_avoided_ingredient_beats_pin():
    """Hard filter > pin: a pinned product carrying an avoided ingredient is dropped
    (never reaches the pin overlap)."""
    user = _user(avoided_ingredient_ids=[{"id": "concept:Ingredient:레티놀", "weight": 1.0}])
    products = [_cg_product("PIN", ingredient_concept_ids=["concept:Ingredient:레티놀"])]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, query_product_ids={"PIN"}
    )
    assert cands == []


def test_cg_exclusion_beats_pin():
    """A product both pinned and excluded is excluded (exclusion wins)."""
    user = _user()
    products = [_cg_product("PIN")]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        query_product_ids={"PIN"}, excluded_product_ids={"PIN"},
    )
    assert cands == []


def test_cg_pins_default_none_byte_identical():
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        _cg_product("B1", brand_concept_ids=["concept:Brand:brand_a"]),
        _cg_product("B2", brand_concept_ids=["concept:Brand:brand_a"]),
    ]
    base = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    same = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        query_product_ids=None, excluded_product_ids=None,
    )
    assert [(c.product_id, c.overlap_concepts) for c in base] == [
        (c.product_id, c.overlap_concepts) for c in same
    ]


# ===========================================================================
# Layer 2 — _rerank_with_pins (pin block leads + rank integrity + cut survival)
# ===========================================================================


def _sp(pid: str, score: float) -> ScoredProduct:
    return ScoredProduct(
        product_id=pid, raw_score=score, shrinked_score=score,
        final_score=score, feature_contributions={},
    )


def _scored(pairs: list[tuple[str, float]]) -> list[tuple[CandidateProduct, ScoredProduct]]:
    scored = [(CandidateProduct(product_id=pid), _sp(pid, sc)) for pid, sc in pairs]
    scored.sort(key=lambda x: x[1].final_score, reverse=True)  # pipeline pre-sorts
    return scored


def test_rerank_pin_leads_despite_low_score():
    scored = _scored([("A", 9.0), ("B", 8.0), ("PIN", 0.1)])
    out, cut = server._rerank_with_pins(
        scored, pins={"PIN"}, product_map={},
        diversity_weight=0.0, top_k=3, mode=RecommendationMode.EXPLORE,
    )
    ids = [r.product_id for r in out]
    assert ids[0] == "PIN"  # pin leads despite the lowest score
    assert set(ids) == {"PIN", "A", "B"}
    assert [r.final_rank for r in out] == [0, 1, 2]  # contiguous, no dupes
    assert cut == []


def test_rerank_multi_pin_block_in_score_order():
    scored = _scored([("P_lo", 1.0), ("P_hi", 5.0), ("X", 9.0)])
    out, cut = server._rerank_with_pins(
        scored, pins={"P_lo", "P_hi"}, product_map={},
        diversity_weight=0.0, top_k=3, mode=RecommendationMode.EXPLORE,
    )
    ids = [r.product_id for r in out]
    assert ids[:2] == ["P_hi", "P_lo"]  # pin block, internal score order
    assert ids[2] == "X"
    assert [r.final_rank for r in out] == [0, 1, 2]
    assert cut == []


def test_rerank_pins_ge_topk_cuts_lowest_and_reports():
    """[F6] Pins alone exceed top_k → top_k wins; the lowest-scored pin is cut and
    reported (reason="top_k" in the caller)."""
    scored = _scored([("P1", 5.0), ("P2", 6.0), ("X", 9.0)])
    out, cut = server._rerank_with_pins(
        scored, pins={"P1", "P2"}, product_map={},
        diversity_weight=0.0, top_k=1, mode=RecommendationMode.EXPLORE,
    )
    assert [r.product_id for r in out] == ["P2"]  # highest pin kept, X (non-pin) dropped
    assert [r.final_rank for r in out] == [0]
    assert cut == ["P1"]  # lowest-scored pin cut by top_k


def test_rerank_topk_zero_guard():
    """[F11] top_k <= 0 → no results, no negative slicing; all pins reported cut."""
    scored = _scored([("P1", 5.0), ("P2", 6.0)])
    out, cut = server._rerank_with_pins(
        scored, pins={"P1", "P2"}, product_map={},
        diversity_weight=0.0, top_k=0, mode=RecommendationMode.EXPLORE,
    )
    assert out == []
    assert set(cut) == {"P1", "P2"}


def test_rerank_dedupes_duplicate_ids():
    """[F9] Duplicate product_ids in scored collapse to first occurrence."""
    scored = _scored([("A", 9.0), ("A", 1.0), ("B", 5.0)])
    out, _cut = server._rerank_with_pins(
        scored, pins={"A"}, product_map={},
        diversity_weight=0.0, top_k=5, mode=RecommendationMode.EXPLORE,
    )
    ids = [r.product_id for r in out]
    assert ids.count("A") == 1  # deduped
    assert set(ids) == {"A", "B"}


def test_rerank_no_pins_matches_plain_rerank():
    scored = _scored([("A", 9.0), ("B", 8.0), ("C", 7.0)])
    a, cut = server._rerank_with_pins(
        scored, pins=set(), product_map={},
        diversity_weight=0.05, top_k=5, mode=RecommendationMode.EXPLORE,
    )
    b = rerank([s for _, s in scored], product_profiles={}, diversity_weight=0.05,
               top_k=5, mode=RecommendationMode.EXPLORE)
    assert [(r.product_id, r.final_rank, r.final_score) for r in a] == [
        (r.product_id, r.final_rank, r.final_score) for r in b
    ]
    assert cut == []


# ===========================================================================
# Layer 3 — server e2e (demo mode): the 윤조에센스 golden + pin trace
# ===========================================================================


def _named(pid: str, name: str, category: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "representative_product_name": name,
        "brand_name": "설화수",
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:설화수"],
        "category_name": category,
        "category_id": None,
        "category_concept_ids": [f"concept:Category:{category}"],
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [],
        "top_bee_attr_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_coused_product_ids": [],
        "top_comparison_product_ids": [],
        "review_count_all": 50,
    }
    base.update(overrides)
    return base


def _golden_products() -> list[dict[str, Any]]:
    return [
        # The essence (the pin target) — brand + category only, no concern/keyword.
        _named("50165", "설화수 윤조에센스", "에센스"),
        # Its mist variant — same, a sibling.
        _named("50166", "설화수 윤조에센스 미스트", "에센스"),
        # The competitor that currently wins on brand+concern+keyword (would be #1
        # WITHOUT the pin): matches the user's stored concern + keyword.
        _named(
            "99999", "맨본윤에센스", "에센스",
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9}],
            top_keyword_ids=[{"id": "kw_moist", "score": 0.9}],
        ),
        # An out-of-category product (makeup) — never in the skincare universe.
        {
            "product_id": "P_lip", "representative_product_name": "릴리 립스틱",
            "brand_name": "릴리", "brand_id": None,
            "brand_concept_ids": ["concept:Brand:릴리"],
            "category_name": "립스틱", "category_id": None,
            "category_concept_ids": ["concept:Category:립스틱"],
            "ingredient_ids": [], "ingredient_concept_ids": [],
            "main_benefit_ids": [], "main_benefit_concept_ids": [],
            "top_keyword_ids": [], "top_bee_attr_ids": [], "top_context_ids": [],
            "top_concern_pos_ids": [], "top_concern_neg_ids": [], "top_tool_ids": [],
            "top_coused_product_ids": [], "top_comparison_product_ids": [],
        },
    ]


def _golden_user(uid: str = "U1") -> dict[str, Any]:
    return {
        "user_id": uid,
        "scoped_preference_ids": [
            {"edge_type": "HAS_CONCERN", "id": "concept:Concern:concern_dryness",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.face.skin_concerns"]},
            {"edge_type": "PREFERS_KEYWORD", "id": "kw_moist",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.keyword"]},
        ],
    }


@pytest.fixture()
def a1_env(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, DemoState]:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)  # live dictionary fallback
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)

    state = DemoState(loaded=True)
    state.serving_products = _golden_products()
    state.serving_users = [_golden_user("U1")]
    # A related sidecar so the additive related-products surface is exercised: the
    # essence pin (50165) has a neighbour NB; the competitor 99999 has none.
    state.similar_ungated = {
        "50165": [{"product_id": "NB", "neighbor_name": "이웃 에센스", "score": 15.0,
                   "shared_axes": [{"axis": "ingredient", "node_key": "ing::x", "label": "x", "idf": 1.0}]}],
    }
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app), state


_GOLDEN_QUERY = "설화수 윤조에센스 어때"


def test_golden_anonymous_pins_essence_top1(a1_env: tuple[TestClient, DemoState]) -> None:
    client, _state = a1_env
    payload = client.post("/api/ask", json={"query": _GOLDEN_QUERY}).json()

    assert payload["resolved_mode"] == "search"
    assert payload["interpretation"]["llm_used"] is False  # live fallback
    # The product axis resolved the essence as a product concept.
    product_concepts = [
        c for c in payload["interpretation"]["resolved_concepts"] if c["concept_type"] == "product"
    ]
    assert any(c["concept_id"] == "50165" for c in product_concepts)

    results = payload["results"]
    assert results[0]["product_id"] == "50165"  # essence pinned to top-1
    assert "product:50165" in results[0]["overlap_concepts"]
    assert "PRODUCT_MASTER_TRUTH" in results[0]["eligibility"]["evidence_families"]
    assert payload["pinned_product_ids"] == ["50165"]
    assert payload["pinned_dropped"] == []
    # 맨본윤에센스 is still returned (brand match) but no longer #1.
    assert "99999" in {r["product_id"] for r in results}


def test_golden_login_pins_essence_over_stronger_competitor(
    a1_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = a1_env
    payload = client.post("/api/ask", json={"user_id": "U1", "query": _GOLDEN_QUERY}).json()

    assert payload["resolved_mode"] == "recommend"
    results = payload["results"]
    assert results[0]["product_id"] == "50165"  # pinned essence leads...
    # ...even though 99999 scores higher (brand + the user's concern + keyword).
    competitor = next(r for r in results if r["product_id"] == "99999")
    assert competitor["rank"] > results[0]["rank"]
    assert results[0]["final_score"] <= competitor["final_score"]  # pin is not a score boost
    assert payload["pinned_product_ids"] == ["50165"]
    # rank field integrity: contiguous 1..N, no dupes.
    ranks = [r["rank"] for r in results]
    assert ranks == list(range(1, len(ranks) + 1))


def test_golden_related_uses_pin_as_anchor(a1_env: tuple[TestClient, DemoState]) -> None:
    """The pinned essence anchors the related-products expansion (its neighbour NB
    surfaces); NB is not itself a 1차 result."""
    client, _state = a1_env
    payload = client.post("/api/ask", json={"user_id": "U1", "query": _GOLDEN_QUERY}).json()
    related_ids = [e["product_id"] for e in payload["related_products"]]
    assert "NB" in related_ids


def test_negated_product_excluded_both_modes(a1_env: tuple[TestClient, DemoState]) -> None:
    """"설화수 윤조에센스 빼고 추천" — the essence AND its mist (both carry '윤조에센스'
    in the name) are excluded from the results; the competitor stays."""
    client, _state = a1_env
    q = "설화수 윤조에센스 빼고 추천"

    anon = client.post("/api/ask", json={"query": q}).json()
    assert set(anon["interpretation"]["excluded_product_ids"]) == {"50165", "50166"}
    ids = {r["product_id"] for r in anon["results"]}
    assert "50165" not in ids and "50166" not in ids
    assert "99999" in ids  # 맨본윤에센스 (no '윤조에센스' in its name) survives
    assert anon["pinned_product_ids"] == []
    # F12: the excluded reason is recorded in the trace (both modes).
    anon_dropped = {d["id"]: d["reason"] for d in anon["pinned_dropped"]}
    assert anon_dropped.get("50165") == "excluded_product"
    assert anon_dropped.get("50166") == "excluded_product"

    login = client.post("/api/ask", json={"user_id": "U1", "query": q}).json()
    login_ids = {r["product_id"] for r in login["results"]}
    assert "50165" not in login_ids and "50166" not in login_ids
    login_dropped = {d["id"]: d["reason"] for d in login["pinned_dropped"]}
    assert login_dropped.get("50165") == "excluded_product"
    assert login_dropped.get("50166") == "excluded_product"


def test_negated_product_via_malgo_marker(a1_env: tuple[TestClient, DemoState]) -> None:
    """[F2a] '말고' now registers as a negation marker on the live fallback path."""
    client, _state = a1_env
    anon = client.post("/api/ask", json={"query": "설화수 윤조에센스 말고 다른거"}).json()
    assert set(anon["interpretation"]["excluded_product_ids"]) == {"50165", "50166"}


def test_anon_pin_category_gate_drops_out_of_group(
    a1_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F4] The category gate is a hard filter over pins: with an explicit skincare
    group, a makeup product pin is dropped (reason="category"), not searched."""
    client, _state = a1_env
    interp = QueryInterpretation(
        query="스킨케어 립스틱", intent="search",
        resolved_concepts=[
            MatchedConcept("category", "concept:Category:skincare", "스킨케어", "스킨케어"),
            MatchedConcept("product", "P_lip", "릴리 립스틱", "릴리 립스틱"),
        ],
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"query": "스킨케어 립스틱"}).json()
    assert payload["category_group"] == "skincare"
    assert payload["pinned_product_ids"] == []  # makeup pin dropped by the category gate
    dropped = {d["id"]: d["reason"] for d in payload["pinned_dropped"]}
    assert dropped.get("P_lip") == "category"


def test_login_relax_count_excludes_negated_product(
    a1_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F5] The ingredient relax count excludes negated products: when the only
    carrier is a negated product, matched=0 → relaxed (never applied=true with 0
    carriers). [F12] the excluded reason is also traced."""
    client, state = a1_env
    hya = "concept:Ingredient:소듐하이알루로네이트"
    state.serving_products[0]["ingredient_concept_ids"] = [hya]  # 50165 is the only carrier
    interp = QueryInterpretation(
        query="설화수 윤조에센스 빼고 히알루론", intent="recommend",
        resolved_concepts=[MatchedConcept("brand", "concept:Brand:설화수", "설화수", "설화수")],
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
        ingredient_constraints=[IngredientConstraint("히알루론", [hya], ["히알루론"], "raw")],
        excluded_product_ids=["50165"],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post(
        "/api/ask", json={"user_id": "U1", "query": "설화수 윤조에센스 빼고 히알루론"}
    ).json()
    meta = payload["ingredient_filter"]
    assert meta["matched_products"] == 0  # the only carrier is excluded → not counted
    assert meta["relaxed"] is True and meta["applied"] is False
    dropped = {d["id"]: d["reason"] for d in payload["pinned_dropped"]}
    assert dropped.get("50165") == "excluded_product"


def test_pin_dropped_by_avoided_ingredient_is_traced(
    a1_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard filter > pin, surfaced in the trace: the named product carries an
    avoided ingredient → dropped (not pinned) with a reason, not in the results."""
    client, state = a1_env
    # Give the essence an avoided ingredient and have the query avoid it.
    state.serving_products[0]["ingredient_concept_ids"] = ["concept:Ingredient:레티놀"]

    interp = QueryInterpretation(
        query="설화수 윤조에센스 레티놀 없는거",
        intent="recommend",
        resolved_concepts=[
            MatchedConcept("product", "50165", "설화수 윤조에센스", "설화수 윤조에센스"),
            MatchedConcept("brand", "concept:Brand:설화수", "설화수", "설화수"),
        ],
        avoided_ingredient_concept_ids=["concept:Ingredient:레티놀"],
        unresolved_terms=[], llm_used=True,
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)

    payload = client.post(
        "/api/ask", json={"user_id": "U1", "query": "설화수 윤조에센스 레티놀 없는거"}
    ).json()
    assert "50165" not in {r["product_id"] for r in payload["results"]}
    assert payload["pinned_product_ids"] == []
    dropped = {d["id"]: d["reason"] for d in payload["pinned_dropped"]}
    assert dropped.get("50165") == "avoided_ingredient"


def test_product_pin_ask_does_not_perturb_no_query_recommend(
    a1_env: tuple[TestClient, DemoState],
) -> None:
    """[C1 byte-identity] A product-pin ask (request-scoped pins + universe union)
    must not mutate shared state: a no-query /api/recommend is bit-identical before
    and after."""
    client, _state = a1_env
    body = {"user_id": "U1", "category_group": "all", "top_k": 10}
    baseline = client.post("/api/recommend", json=body)
    assert baseline.status_code == 200

    ask = client.post("/api/ask", json={"user_id": "U1", "query": _GOLDEN_QUERY}).json()
    assert ask["pinned_product_ids"] == ["50165"]  # the pin actually ran

    after = client.post("/api/recommend", json=body)
    assert after.status_code == 200
    assert after.content == baseline.content  # raw-bytes identity
