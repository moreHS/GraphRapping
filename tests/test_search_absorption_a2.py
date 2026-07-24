"""Search-absorption A2 tests: polarity generalized to the brand / category axes —
excluded brand / literal category / category group hard exclusion, threaded through
search, the recommend candidate pipeline, related products, and the pin trace.

Plan: fable_doc/plans/2026-07-23_search_absorption.md §A2.

Three layers:
- candidate_generator: excluded brand/category/group are hard filters (before any
  overlap/pin work → exclusion beats a pin); default None keeps the path identical.
- search_products: same hard filters + caller-authority drop of a re-resolved
  excluded brand/category; default None keeps the path byte-identical.
- server e2e (demo mode): "이니스프리 말고 보습크림" (brand), "선크림 빼고 세럼" (literal
  category — 세럼 kept, 선크림류 removed), "스킨케어 빼고" (group — universe reconstructed);
  exclusion beats a pin (pinned_dropped reason); the ``excluded`` meta is surfaced;
  related propagation in both modes.

The interpretation-level resolution (excluded_brand_ids / excluded_category_surfaces /
excluded_category_groups, _negated_brands / _negated_categories, LLM slots,
non-cancellation) is covered in tests/test_query_understanding.py. The no-query
byte-identity + ranking-snapshot invariants are proven by the untouched recommend
suites staying green.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.query_understanding import QueryInterpretation
from src.rec.search import MatchedConcept, search_products
from src.web import server
from src.web.state import DemoState


# ===========================================================================
# Layer 1 — candidate_generator excluded-axis hard filters
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


def _cg_product(pid: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_id": None,
        "category_id": None,
        "category_name": None,
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


_BRAND_A = [{"id": "concept:Brand:A", "weight": 1.0}]


def test_cg_excluded_brand_hard_filtered():
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        _cg_product("KEEP", brand_concept_ids=["concept:Brand:A"]),
        _cg_product("DROP", brand_concept_ids=["concept:Brand:A", "concept:Brand:X"]),
    ]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        excluded_brand_ids={"concept:Brand:X"},
    )
    assert [c.product_id for c in cands] == ["KEEP"]


def test_cg_excluded_literal_category_hard_filtered():
    """[F3] Surface-keyed: the negated surface "선크림" ⊂ the product's OWN category
    label "선크림 & 선블럭" → dropped (no reliance on concept-id links)."""
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        _cg_product("KEEP", brand_concept_ids=["concept:Brand:A"], category_name="에센스"),
        _cg_product("DROP", brand_concept_ids=["concept:Brand:A"], category_name="선크림 & 선블럭"),
    ]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        excluded_category_surfaces={"선크림"},
    )
    assert [c.product_id for c in cands] == ["KEEP"]


def test_cg_excluded_category_group_hard_filtered():
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        # makeup group (쿠션 keyword) — kept.
        _cg_product("KEEP", brand_concept_ids=["concept:Brand:A"], category_name="쿠션"),
        # skincare group (에센스 keyword) — dropped by the group exclusion.
        _cg_product("DROP", brand_concept_ids=["concept:Brand:A"], category_name="에센스"),
    ]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        excluded_category_groups={"skincare"},
    )
    assert [c.product_id for c in cands] == ["KEEP"]


def test_cg_exclusion_beats_pin():
    """명시 배제 > 핀: a pinned product hit by an excluded brand is dropped (never
    reaches the product: overlap)."""
    user = _user()
    products = [_cg_product("PIN", brand_concept_ids=["concept:Brand:X"])]
    cands = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        query_product_ids={"PIN"}, excluded_brand_ids={"concept:Brand:X"},
    )
    assert cands == []


def test_cg_a2_default_none_byte_identical():
    user = _user(preferred_brand_ids=_BRAND_A)
    products = [
        _cg_product("B1", brand_concept_ids=["concept:Brand:A"], category_name="에센스"),
        _cg_product("B2", brand_concept_ids=["concept:Brand:A"], category_name="크림"),
    ]
    base = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    same = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        excluded_brand_ids=None, excluded_category_surfaces=None, excluded_category_groups=None,
    )
    assert [(c.product_id, c.overlap_concepts) for c in base] == [
        (c.product_id, c.overlap_concepts) for c in same
    ]


# ===========================================================================
# Layer 2 — search_products excluded-axis hard filters
# ===========================================================================


def _s_product(pid: str, name: str, brand: str, bcid: str, cat: str, ccid: str) -> dict[str, Any]:
    return {
        "product_id": pid, "representative_product_name": name,
        "brand_name": brand, "brand_id": None, "brand_concept_ids": [bcid],
        "category_name": cat, "category_id": None, "category_concept_ids": [ccid],
        "ingredient_ids": [], "ingredient_concept_ids": [],
        "main_benefit_ids": [], "main_benefit_concept_ids": [],
        "top_keyword_ids": [], "top_bee_attr_ids": [], "top_context_ids": [],
        "top_concern_pos_ids": [], "top_concern_neg_ids": [], "top_tool_ids": [],
        "top_coused_product_ids": [], "top_comparison_product_ids": [],
    }


def _s_universe() -> list[dict[str, Any]]:
    return [
        _s_product("P_inni", "이니스프리 보습크림", "이니스프리", "concept:Brand:이니스프리",
                   "크림", "concept:Category:크림"),
        _s_product("P_rival", "라네즈 보습크림", "라네즈", "concept:Brand:라네즈",
                   "크림", "concept:Category:크림"),
        _s_product("P_sun", "설화수 선크림", "설화수", "concept:Brand:설화수",
                   "선크림 & 선블럭", "concept:Category:선크림 & 선블럭"),
        _s_product("P_serum", "설화수 윤조세럼", "설화수", "concept:Brand:설화수",
                   "에센스", "concept:Category:에센스"),
    ]


def test_search_excluded_brand_removed():
    outcome = search_products(
        "보습크림", _s_universe(), excluded_brand_ids={"concept:Brand:이니스프리"},
    )
    ids = {r.product_id for r in outcome.results}
    assert "P_inni" not in ids  # excluded brand gone
    assert "P_rival" in ids  # rival 크림 kept


def test_search_excluded_literal_category_removed():
    outcome = search_products(
        "세럼 크림", _s_universe(),
        excluded_category_surfaces={"선크림"},  # F3: surface ⊂ "선크림 & 선블럭" label
    )
    ids = {r.product_id for r in outcome.results}
    assert "P_sun" not in ids  # literal 선크림 category gone
    assert "P_serum" in ids  # 세럼 kept


def test_search_excluded_group_removed_not_relaxed():
    """0건 비완화 (search layer): excluding the only group present yields an empty
    result set — the exclusion is a hard filter, never relaxed to include it back."""
    outcome = search_products(
        "에센스 세럼", _s_universe(), excluded_category_groups={"skincare"},
    )
    ids = {r.product_id for r in outcome.results}
    assert ids == set()  # every candidate was skincare → all excluded, no relax


def test_search_a2_default_none_byte_identical():
    universe = _s_universe()
    base = search_products("보습크림", universe).to_dict()
    with_none = search_products(
        "보습크림", universe,
        excluded_brand_ids=None, excluded_category_surfaces=None, excluded_category_groups=None,
    ).to_dict()
    assert base == with_none


# ===========================================================================
# Layer 3 — server e2e (demo mode)
# ===========================================================================


def _prod(pid: str, name: str, brand: str, bcid: str, cat: str, ccid: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid, "representative_product_name": name,
        "brand_name": brand, "brand_id": None, "brand_concept_ids": [bcid],
        "category_name": cat, "category_id": None, "category_concept_ids": [ccid],
        "ingredient_ids": [], "ingredient_concept_ids": [],
        "main_benefit_ids": [], "main_benefit_concept_ids": [],
        "top_keyword_ids": [], "top_bee_attr_ids": [], "top_context_ids": [],
        "top_concern_pos_ids": [], "top_concern_neg_ids": [], "top_tool_ids": [],
        "top_coused_product_ids": [], "top_comparison_product_ids": [],
        "review_count_all": 50,
    }
    base.update(overrides)
    return base


def _a2_products() -> list[dict[str, Any]]:
    return [
        _prod("P_inni", "이니스프리 보습크림", "이니스프리", "concept:Brand:이니스프리",
              "크림", "concept:Category:크림"),
        _prod("P_rival", "라네즈 보습크림", "라네즈", "concept:Brand:라네즈",
              "크림", "concept:Category:크림"),
        _prod("P_sun", "설화수 선크림", "설화수", "concept:Brand:설화수",
              "선크림 & 선블럭", "concept:Category:선크림 & 선블럭"),
        _prod("P_serum", "설화수 윤조세럼", "설화수", "concept:Brand:설화수",
              "에센스", "concept:Category:에센스"),
        _prod("P_lip", "릴리 립스틱", "릴리", "concept:Brand:릴리",
              "립스틱", "concept:Category:립스틱"),
    ]


def _a2_user(uid: str = "U1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": uid,
        # A brand pref for every fixture brand so the recommend path can rank a
        # survivor in any category (evidence via PREFERS_BRAND overlap).
        "scoped_preference_ids": [
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:라네즈",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.brand"]},
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:릴리",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.brand"]},
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:설화수",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.brand"]},
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:이니스프리",
             "weight": 0.8, "scope_group": None, "source_sections": ["chat.brand"]},
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def a2_env(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)  # live dictionary fallback
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)

    state = DemoState(loaded=True)
    state.serving_products = _a2_products()
    state.serving_users = [_a2_user("U1")]
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app)


def test_e2e_brand_exclusion_anonymous(a2_env: TestClient) -> None:
    """"이니스프리 말고 보습크림": the 이니스프리 product is hard-excluded from the
    anonymous results; the keyword 보습크림 still matches the rival."""
    payload = a2_env.post("/api/ask", json={"query": "이니스프리 말고 보습크림"}).json()
    assert payload["interpretation"]["excluded_brand_ids"] == ["concept:Brand:이니스프리"]
    ids = {r["product_id"] for r in payload["results"]}
    assert "P_inni" not in ids  # excluded brand gone
    assert "P_rival" in ids  # rival 보습크림 kept
    assert payload["excluded"]["brands"] == ["이니스프리"]


def test_e2e_brand_exclusion_login(a2_env: TestClient) -> None:
    payload = a2_env.post(
        "/api/ask", json={"user_id": "U1", "query": "이니스프리 말고 보습크림"}
    ).json()
    assert payload["resolved_mode"] == "recommend"
    ids = {r["product_id"] for r in payload["results"]}
    assert "P_inni" not in ids  # excluded brand gone even with a 이니스프리 brand pref
    assert payload["excluded"]["brands"] == ["이니스프리"]


def test_e2e_literal_category_exclusion_keeps_serum(a2_env: TestClient) -> None:
    """"선크림 빼고 세럼": the 선크림 & 선블럭 literal category is removed; the 세럼
    (skincare essence) is kept — literal exclusion, not group (non-cancellation)."""
    payload = a2_env.post("/api/ask", json={"query": "선크림 빼고 세럼"}).json()
    assert payload["interpretation"]["excluded_category_surfaces"] == ["선크림"]
    assert payload["interpretation"]["excluded_category_groups"] == []
    ids = {r["product_id"] for r in payload["results"]}
    assert "P_sun" not in ids  # 선크림류 removed
    assert "P_serum" in ids  # 세럼 kept (skincare group양성 유지)
    assert payload["excluded"]["categories"] == ["선크림"]  # F3: surface verbatim


def test_e2e_group_exclusion_reconstructs_universe_login(a2_env: TestClient) -> None:
    """"스킨케어 빼고": the recommend universe becomes "all − skincare" — every skincare
    product is absent; a non-skincare survivor (makeup 립스틱) still ranks."""
    payload = a2_env.post(
        "/api/ask", json={"user_id": "U1", "query": "스킨케어 빼고"}
    ).json()
    assert payload["interpretation"]["excluded_category_groups"] == ["skincare"]
    assert payload["category_group"] == "all"  # skincare group selection invalidated
    ids = {r["product_id"] for r in payload["results"]}
    assert ids.isdisjoint({"P_inni", "P_rival", "P_sun", "P_serum"})  # skincare gone
    assert "P_lip" in ids  # non-skincare survivor ranks (릴리 brand pref)
    assert payload["excluded"]["category_groups"] == ["스킨케어"]


def test_e2e_group_exclusion_conflict_request_hint_invalidated(a2_env: TestClient) -> None:
    """The request category_group hint is invalidated when the query negates it
    (배제 우선): category_group falls back to "all"."""
    payload = a2_env.post(
        "/api/ask",
        json={"user_id": "U1", "query": "스킨케어 빼고", "category_group": "skincare"},
    ).json()
    assert payload["category_group"] == "all"


def test_e2e_exclusion_beats_pin(a2_env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """명시 배제 > A1 핀: a pinned product whose brand is excluded is dropped with a
    reason, not pinned."""
    interp = QueryInterpretation(
        query="이니스프리 보습크림 말고 이니스프리", intent="recommend",
        resolved_concepts=[
            MatchedConcept("product", "P_inni", "이니스프리 보습크림", "이니스프리 보습크림"),
        ],
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
        excluded_brand_ids=["concept:Brand:이니스프리"],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = a2_env.post(
        "/api/ask", json={"user_id": "U1", "query": "이니스프리 보습크림 말고 이니스프리"}
    ).json()
    assert "P_inni" not in {r["product_id"] for r in payload["results"]}
    assert payload["pinned_product_ids"] == []
    dropped = {d["id"]: d["reason"] for d in payload["pinned_dropped"]}
    assert dropped.get("P_inni") == "excluded_brand"


def test_e2e_exclusion_beats_pin_anonymous(a2_env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    interp = QueryInterpretation(
        query="선크림 핀 빼고", intent="search",
        resolved_concepts=[MatchedConcept("product", "P_sun", "설화수 선크림", "설화수 선크림")],
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
        excluded_category_surfaces=["선크림"],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = a2_env.post("/api/ask", json={"query": "선크림 핀 빼고"}).json()
    assert "P_sun" not in {r["product_id"] for r in payload["results"]}
    assert payload["pinned_product_ids"] == []
    dropped = {d["id"]: d["reason"] for d in payload["pinned_dropped"]}
    assert dropped.get("P_sun") == "excluded_category"


def test_e2e_excluded_meta_shape(a2_env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``excluded`` meta carries labeled brand/category/group/product arrays."""
    interp = QueryInterpretation(
        query="배제 종합", intent="search",
        resolved_concepts=[], avoided_ingredient_concept_ids=[],
        unresolved_terms=[], llm_used=True,
        excluded_product_ids=["P_serum"],
        excluded_brand_ids=["concept:Brand:이니스프리"],
        excluded_category_surfaces=["선크림"],
        excluded_category_groups=["skincare"],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = a2_env.post("/api/ask", json={"query": "배제 종합"}).json()
    excluded = payload["excluded"]
    assert excluded["brands"] == ["이니스프리"]
    assert excluded["categories"] == ["선크림"]  # F3: surface verbatim
    assert excluded["category_groups"] == ["스킨케어"]  # tab label
    assert excluded["products"] == ["설화수 윤조세럼"]  # representative name


def test_e2e_related_excludes_negated_brand(a2_env: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """related products propagate the exclusion in both modes: an 이니스프리 neighbour
    of a result is not surfaced as a related product."""
    state = DemoState(loaded=True)
    state.serving_products = _a2_products()
    state.serving_users = [_a2_user("U1")]
    # The rival's neighbour is the excluded 이니스프리 product → must be filtered from related.
    state.similar_ungated = {
        "P_rival": [{"product_id": "P_inni", "neighbor_name": "이니스프리 보습크림",
                     "score": 20.0, "shared_axes": []}],
    }
    monkeypatch.setattr(server, "demo_state", state)

    for body in ({"query": "이니스프리 말고 보습크림"},
                 {"user_id": "U1", "query": "이니스프리 말고 보습크림"}):
        payload = a2_env.post("/api/ask", json=body).json()
        related_ids = {e["product_id"] for e in payload["related_products"]}
        assert "P_inni" not in related_ids  # excluded brand not re-surfaced via related


def test_e2e_login_relax_count_excludes_axis_excluded(
    a2_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[성분 relax 유니버스 선차감] The ingredient relax count pre-subtracts
    axis-excluded products (parity with A1's excluded_product_ids): when the only
    carrier of the wanted ingredient sits in an EXCLUDED brand, matched=0 → relaxed
    (never applied=true with 0 real carriers)."""
    from src.rec.ingredient_constraint import IngredientConstraint

    hya = "concept:Ingredient:소듐하이알루로네이트"
    # Make the (설화수) essence the ONLY 히알루론 carrier, then exclude brand 설화수.
    interp = QueryInterpretation(
        query="설화수 말고 히알루론", intent="recommend",
        resolved_concepts=[], avoided_ingredient_concept_ids=[],
        unresolved_terms=[], llm_used=True,
        ingredient_constraints=[IngredientConstraint("히알루론", [hya], ["히알루론"], "raw")],
        excluded_brand_ids=["concept:Brand:설화수"],
    )

    state = DemoState(loaded=True)
    products = _a2_products()
    for p in products:
        if p["product_id"] == "P_serum":  # 설화수 essence — the sole carrier
            p["ingredient_concept_ids"] = [hya]
    state.serving_products = products
    state.serving_users = [_a2_user("U1")]
    monkeypatch.setattr(server, "demo_state", state)
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)

    payload = a2_env.post(
        "/api/ask", json={"user_id": "U1", "query": "설화수 말고 히알루론"}
    ).json()
    meta = payload["ingredient_filter"]
    assert meta["matched_products"] == 0  # sole carrier is in the excluded brand → not counted
    assert meta["relaxed"] is True and meta["applied"] is False
    # The excluded 설화수 products never appear in the results.
    ids = {r["product_id"] for r in payload["results"]}
    assert ids.isdisjoint({"P_serum", "P_sun"})  # both 설화수


def test_e2e_exclusion_only_anonymous_message(
    a2_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F4] An exclusion-only anonymous query ("스킨케어 빼고") returns 0 results but a
    non-silent message: the only positive artifact is the excluded group's own literal
    label, so honest guidance is surfaced instead of an empty payload."""
    state = DemoState(loaded=True)
    # A product literally labelled "스킨케어" (classifies to the skincare group): the
    # label forward-matches "스킨케어 빼고" so search stays "resolved", but the group is
    # excluded → 0 results → exclusion-only guidance.
    state.serving_products = [
        _prod("P_sk", "AP 스킨케어세트", "아모레퍼시픽", "concept:Brand:아모레퍼시픽",
              "스킨케어", "concept:Category:스킨케어"),
    ]
    state.serving_users = []
    monkeypatch.setattr(server, "demo_state", state)

    payload = a2_env.post("/api/ask", json={"query": "스킨케어 빼고"}).json()
    assert payload["interpretation"]["excluded_category_groups"] == ["skincare"]
    assert payload["results"] == []
    assert payload["message"] == server._SEARCH_EXCLUSION_ONLY_MESSAGE
    assert payload["excluded"]["category_groups"] == ["스킨케어"]


def test_e2e_excluded_products_label_dedupe(
    a2_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F3] Two distinct SKUs sharing one representative name → the excluded.products
    label array dedupes (order-preserving); ids stay in the interpretation."""
    interp = QueryInterpretation(
        query="라벨중복 빼고", intent="search",
        resolved_concepts=[], avoided_ingredient_concept_ids=[],
        unresolved_terms=[], llm_used=True,
        excluded_product_ids=["P_a", "P_b", "P_c"],
    )
    state = DemoState(loaded=True)
    state.serving_products = [
        _prod("P_a", "설화수 윤조에센스", "설화수", "concept:Brand:설화수", "에센스", "concept:Category:에센스"),
        _prod("P_b", "설화수 윤조에센스", "설화수", "concept:Brand:설화수", "에센스", "concept:Category:에센스"),
        _prod("P_c", "설화수 윤조에센스미스트", "설화수", "concept:Brand:설화수", "에센스", "concept:Category:에센스"),
    ]
    state.serving_users = []
    monkeypatch.setattr(server, "demo_state", state)
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)

    payload = a2_env.post("/api/ask", json={"query": "라벨중복 빼고"}).json()
    # ids preserved (3), labels deduped (2, order-preserving).
    assert payload["interpretation"]["excluded_product_ids"] == ["P_a", "P_b", "P_c"]
    assert payload["excluded"]["products"] == ["설화수 윤조에센스", "설화수 윤조에센스미스트"]


def test_e2e_brand_not_wiped_by_span_containing_brand(a2_env: TestClient) -> None:
    """[F1] "이니스프리 선크림 빼고 세럼": only the 선크림 literal category is excluded — the
    이니스프리 brand is NOT wiped even though the span "이니스프리 선크림" contains the brand
    token (strict brand equality rejects it). 이니스프리 products survive; 선크림류 removed."""
    payload = a2_env.post("/api/ask", json={"query": "이니스프리 선크림 빼고 세럼"}).json()
    assert payload["interpretation"]["excluded_brand_ids"] == []
    assert payload["interpretation"]["excluded_category_surfaces"] == ["선크림"]
    ids = {r["product_id"] for r in payload["results"]}
    assert "P_sun" not in ids  # 선크림 & 선블럭 removed
    assert "P_inni" in ids  # 이니스프리 brand NOT wiped
