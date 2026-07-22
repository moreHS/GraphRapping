"""Concept-based search tests (Phase 4.2, fable_doc/03_improvement_plan.md §4.2).

Covers:
- concept resolution success across all six axes (brand/category/ingredient/
  concern/goal/keyword) and the category-*group* axis (tab vocabulary).
- concept resolution failure — explicit non-resolution, not a silent full-text
  fallback.
- overlap-based ranking + evidence-family classification (reused from
  src/rec/recommendation_evidence_index.py, same as /api/recommend).
- the `/api/search` endpoint in both demo mode (module-level demo_state) and
  DB mode (fake store), mirroring the server-function-call test pattern used
  by tests/test_web_server_source_enrichment.py and
  tests/test_serving_store_mode.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.rec.ingredient_constraint import IngredientConstraint
from src.rec.search import resolve_query_concepts, search_products
from src.web import server
from src.web.state import DemoState


def _product(pid: str = "P1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_name": None,
        "brand_concept_ids": [],
        "category_name": None,
        "category_concept_ids": [],
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [],
        "top_concern_pos_ids": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# resolve_query_concepts — per-axis resolution
# ---------------------------------------------------------------------------


def test_resolve_concern_axis():
    concepts = resolve_query_concepts("건조해서 고민이에요", [])
    concern = [c for c in concepts if c.concept_type == "concern"]
    assert any(c.concept_id == "concern_dryness" for c in concern)


def test_resolve_goal_axis():
    concepts = resolve_query_concepts("보습 원해요", [])
    goals = {c.concept_id for c in concepts if c.concept_type == "goal"}
    assert "보습" in goals


def test_resolve_keyword_axis():
    concepts = resolve_query_concepts("촉촉한 제품 찾아요", [])
    keywords = {c.concept_id for c in concepts if c.concept_type == "keyword"}
    assert "kw_moist" in keywords


def test_resolve_category_group_axis():
    concepts = resolve_query_concepts("메이크업 추천해줘", [])
    categories = {c.concept_id for c in concepts if c.concept_type == "category"}
    assert "concept:Category:makeup" in categories


def test_resolve_brand_axis_from_catalog():
    products = [_product("P1", brand_name="이니스프리", brand_concept_ids=["concept:Brand:이니스프리"])]
    concepts = resolve_query_concepts("이니스프리 신상 나왔나요", products)
    brands = {c.concept_id for c in concepts if c.concept_type == "brand"}
    assert brands == {"concept:Brand:이니스프리"}


def test_resolve_category_axis_literal_catalog_name():
    products = [_product("P1", category_name="핸드보습", category_concept_ids=["concept:Category:핸드보습"])]
    concepts = resolve_query_concepts("핸드보습 제품 있나요", products)
    categories = {c.concept_id for c in concepts if c.concept_type == "category"}
    assert "concept:Category:핸드보습" in categories


def test_resolve_ingredient_axis_from_catalog():
    products = [
        _product(
            "P1",
            ingredient_ids=["히알루론산"],
            ingredient_concept_ids=["concept:Ingredient:히알루론산"],
        )
    ]
    concepts = resolve_query_concepts("히알루론산 들어간 제품", products)
    ingredients = {c.concept_id for c in concepts if c.concept_type == "ingredient"}
    assert ingredients == {"concept:Ingredient:히알루론산"}


def test_resolve_ingredient_axis_falls_back_to_concept_suffix_when_labels_misaligned():
    """If ingredient_ids is shorter/absent, the concept id suffix is used as
    the label instead of crashing on an index error."""
    products = [_product("P1", ingredient_ids=[], ingredient_concept_ids=["concept:Ingredient:레티놀"])]
    concepts = resolve_query_concepts("레티놀 성분 궁금해요", products)
    ingredients = {c.concept_id for c in concepts if c.concept_type == "ingredient"}
    assert ingredients == {"concept:Ingredient:레티놀"}


def test_resolve_bare_ingredient_negation_span_not_adopted():
    """[F7] A bare INCI surface sitting inside a negation span is refused positive
    adoption at RESOLUTION level (matching the alias layer), so "레티놀 없는 크림"
    resolves no positive retinol — no reliance on a downstream subtraction step."""
    products = [
        _product("P1", ingredient_ids=["레티놀"], ingredient_concept_ids=["concept:Ingredient:레티놀"])
    ]
    negated = {c.concept_id for c in resolve_query_concepts("레티놀 없는 크림", products)
               if c.concept_type == "ingredient"}
    assert negated == set()
    # Sanity: WITHOUT a negation marker the bare axis DOES adopt it (proves the
    # guard, not a missing mapping, is what suppresses the negated case).
    positive = {c.concept_id for c in resolve_query_concepts("레티놀 든 크림", products)
                if c.concept_type == "ingredient"}
    assert positive == {"concept:Ingredient:레티놀"}


def test_resolve_multiple_axes_in_one_query():
    """The fable_doc §4.2 completion example: '보습 잘 되는 스킨케어' resolves
    a goal, a keyword, and a category group simultaneously."""
    concepts = resolve_query_concepts("보습 잘 되는 스킨케어", [])
    by_type = {c.concept_type for c in concepts}
    assert "goal" in by_type
    assert "keyword" in by_type
    assert "category" in by_type


def test_resolve_query_concepts_no_match_returns_empty():
    concepts = resolve_query_concepts("asdkjfhaskdjfh12345", [_product("P1")])
    assert concepts == []


def test_resolve_query_concepts_blank_query_returns_empty():
    assert resolve_query_concepts("", [_product("P1")]) == []
    assert resolve_query_concepts("   ", [_product("P1")]) == []


def test_resolve_short_surface_tokens_are_not_noise_matched():
    """A single-character substring must not spuriously resolve (min-length
    floor mirrors the keyword min_label_len=2 already used for promotion)."""
    # "향" (scent) is a real bee_attr/keyword-adjacent token, but as a lone
    # character it must not match every query that happens to contain it.
    concepts = resolve_query_concepts("아무 상관 없는 문장입니다", [])
    assert concepts == []


# ---------------------------------------------------------------------------
# search_products — overlap ranking + evidence family
# ---------------------------------------------------------------------------


def test_search_ranks_more_overlap_higher():
    products = [
        _product(
            "P_low",
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
        ),
        _product(
            "P_high",
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
            top_keyword_ids=[{"id": "kw_moisturizing", "score": 0.8}],
        ),
        _product("P_none"),
    ]
    outcome = search_products("보습 원해요", products)

    assert outcome.resolved is True
    result_ids = [r.product_id for r in outcome.results]
    assert result_ids == ["P_high", "P_low"]  # P_none excluded (no evidence)
    assert outcome.results[0].relevance_score > outcome.results[1].relevance_score


def test_search_evidence_family_reused_from_recommendation_index():
    products = [
        _product(
            "P1",
            brand_name="이니스프리",
            brand_concept_ids=["concept:Brand:이니스프리"],
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9}],
        ),
    ]
    outcome = search_products("이니스프리 건조함", products)
    assert len(outcome.results) == 1
    families = outcome.results[0].eligibility.evidence_families
    assert "PRODUCT_MASTER_TRUTH" in families  # brand
    assert "REVIEW_GRAPH_RELATION" in families  # concern
    assert outcome.results[0].eligibility.eligible is True


def test_search_products_same_completion_phrase_returns_evidence_backed_result():
    """fable_doc §4.2 completion example, exercised end-to-end through
    search_products (test_resolve_multiple_axes_in_one_query above only checks
    concept resolution): the same '보습 잘 되는 스킨케어' phrase must also
    return an actual product match with non-empty overlap_concepts and
    evidence-backed eligibility, not just resolve concepts."""
    products = [
        _product(
            "P1",
            category_name="스킨케어",
            category_concept_ids=["concept:Category:스킨케어"],
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
            top_keyword_ids=[{"id": "kw_moisturizing", "score": 0.9}],
        ),
    ]
    outcome = search_products("보습 잘 되는 스킨케어", products)
    assert outcome.resolved is True

    payload = outcome.to_dict()
    assert payload["result_count"] == 1
    result = payload["results"][0]
    assert result["product_id"] == "P1"
    assert result["overlap_concepts"]  # non-empty: at least one axis overlapped
    eligibility = result["eligibility"]
    assert eligibility["eligible"] is True
    assert "PRODUCT_MASTER_TRUTH" in eligibility["evidence_families"]
    assert "REVIEW_GRAPH_RELATION" in eligibility["evidence_families"]


def test_search_no_resolution_short_circuits_before_scanning_products():
    products = [_product("P1", brand_name="이니스프리", brand_concept_ids=["concept:Brand:이니스프리"])]
    outcome = search_products("zzzz_no_such_concept_zzzz", products)
    assert outcome.resolved is False
    assert outcome.results == []
    assert outcome.resolved_concepts == []


def test_search_max_results_truncates():
    products = [
        _product(f"P{i}", main_benefit_ids=["보습강화"], main_benefit_concept_ids=["concept:Goal:보습"])
        for i in range(5)
    ]
    outcome = search_products("보습", products, max_results=2)
    assert len(outcome.results) == 2


def test_search_category_group_does_not_force_match_on_unrelated_product():
    """Resolving a category-group concept must not make every product match —
    only products the group actually classifies to (evidence-first)."""
    products = [_product("P_lipstick", category_name="립스틱", category_concept_ids=["concept:Category:립스틱"])]
    outcome = search_products("스킨케어 추천", products)
    assert outcome.resolved is True  # concept resolved...
    assert outcome.results == []  # ...but no product actually belongs to it


def test_search_outcome_to_dict_shape():
    products = [_product("P1", brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"])]
    outcome = search_products("설화수 제품", products)
    payload = outcome.to_dict()
    assert payload["query"] == "설화수 제품"
    assert payload["resolved"] is True
    assert payload["result_count"] == 1
    result = payload["results"][0]
    assert result["product_id"] == "P1"
    assert result["matched_concepts"] == ["brand:concept:Brand:설화수"]
    assert "eligibility" in result and "evidence_families" in result["eligibility"]


# ---------------------------------------------------------------------------
# /api/search endpoint — demo mode
# ---------------------------------------------------------------------------


def _search_product(pid: str = "P1") -> dict[str, Any]:
    return _product(
        pid,
        brand_name="헤라",
        brand_concept_ids=["concept:Brand:헤라"],
        category_name="쿠션",
        category_concept_ids=["concept:Category:쿠션"],
        top_keyword_ids=[{"id": "kw_thin_spread", "score": 0.9}],
    )


@pytest.mark.asyncio
async def test_search_get_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = [_search_product("P1")]
    monkeypatch.setattr(server, "demo_state", state)

    payload = await server.search_get(query="헤라 쿠션", top_k=10)

    # /api/search is unified onto the anonymous /api/ask shape (plan §B2 v3).
    assert payload["resolved_mode"] == "search"
    assert payload["message"] is None  # resolved → no no-concept guidance
    assert len(payload["results"]) == 1
    assert payload["results"][0]["product_id"] == "P1"
    assert payload["results"][0]["product"]["product_id"] == "P1"
    assert payload["ingredient_filter"]["applied"] is False  # no ingredient in query


@pytest.mark.asyncio
async def test_search_post_demo_mode_no_concept_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = [_search_product("P1")]
    monkeypatch.setattr(server, "demo_state", state)

    payload = await server.search(server.SearchRequest(query="zzzz_no_such_concept_zzzz", top_k=10))

    assert payload["resolved_mode"] == "search"
    assert payload["results"] == []
    assert payload["message"]  # explicit guidance, not a silent empty result


@pytest.mark.asyncio
async def test_search_demo_mode_requires_pipeline_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await server.search_get(query="헤라")
    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# /api/search endpoint — DB mode (fake store, mirrors _FakeStore in
# tests/test_web_server_source_enrichment.py)
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, products: list[dict]) -> None:
        self._products = products

    async def get_products(self) -> list[dict]:
        return self._products

    async def get_product(self, product_id: str) -> dict | None:
        return next((p for p in self._products if p["product_id"] == product_id), None)

    async def get_users(self) -> list[dict]:
        return []

    async def get_user(self, user_id: str) -> dict | None:
        return None


@pytest.mark.asyncio
async def test_search_post_db_mode_independent_of_demo_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB mode must not require a demo pipeline run (demo_state stays unloaded)."""
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    monkeypatch.setattr(server, "_serving_store", _FakeStore([_search_product("P1")]))

    payload = await server.search(server.SearchRequest(query="헤라 쿠션", top_k=5))

    assert payload["resolved_mode"] == "search"
    assert len(payload["results"]) == 1
    assert payload["results"][0]["product_id"] == "P1"
    assert "brand:concept:Brand:헤라" in payload["results"][0]["matched_concepts"]


@pytest.mark.asyncio
async def test_search_get_db_mode_evidence_family_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    monkeypatch.setattr(server, "_serving_store", _FakeStore([_search_product("P1")]))

    payload = await server.search_get(query="쿠션 제품", top_k=5)

    assert len(payload["results"]) == 1
    eligibility = payload["results"][0]["eligibility"]
    assert "PRODUCT_MASTER_TRUTH" in eligibility["evidence_families"]


@pytest.mark.asyncio
async def test_search_top_k_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    products = [_search_product(f"P{i}") for i in range(5)]
    monkeypatch.setattr(server, "_serving_store", _FakeStore(products))

    payload = await server.search(server.SearchRequest(query="헤라 쿠션", top_k=0))
    assert len(payload["results"]) >= 1  # clamped to >=1, not an empty slice

    payload_big = await server.search(server.SearchRequest(query="헤라 쿠션", top_k=10_000))
    assert len(payload_big["results"]) == 5


# ---------------------------------------------------------------------------
# Ingredient axis end-to-end (regression: concept_ids / ingredient_ids are
# independently built in build_serving_views.py and are NOT positionally
# aligned, so the concept must be labeled/matched from its own id suffix).
# ---------------------------------------------------------------------------


def _misaligned_ingredient_product(pid: str = "P1") -> dict[str, Any]:
    # ingredient_ids (raw master) and ingredient_concept_ids (filtered
    # HAS_INGREDIENT subset) differ in BOTH length and order, and the raw list's
    # index-0 name (글리세린) is not the concept below — the exact shape that made
    # positional pairing (raw_names[idx]) mislabel the concept.
    return _product(
        pid,
        ingredient_ids=["글리세린", "정제수"],
        ingredient_concept_ids=["concept:Ingredient:나이아신아마이드"],
    )


def test_search_ingredient_axis_e2e_with_misaligned_lists():
    """The real ingredient name (encoded in the concept id) resolves and flows
    through search_products ranking as an `ingredient:` overlap, even though the
    raw ingredient_ids list is a different length/order and lacks that name."""
    outcome = search_products("나이아신아마이드 세럼 찾아요", [_misaligned_ingredient_product("P1")])
    assert outcome.resolved is True
    assert len(outcome.results) == 1
    assert "ingredient:concept:Ingredient:나이아신아마이드" in outcome.results[0].matched_concepts
    # And the overlap is classified as product-master truth, same as recommend.
    assert "PRODUCT_MASTER_TRUTH" in outcome.results[0].eligibility.evidence_families


def test_search_ingredient_axis_no_false_positive_from_misaligned_raw_name():
    """Querying the misaligned raw ingredient name (글리세린, which carries no
    concept id on this product) must NOT resolve the unrelated concept it was
    positionally paired with under the bug."""
    outcome = search_products("글리세린 세럼", [_misaligned_ingredient_product("P1")])
    matched = [mc for r in outcome.results for mc in r.matched_concepts]
    assert "ingredient:concept:Ingredient:나이아신아마이드" not in matched


# ---------------------------------------------------------------------------
# Category axis dedupe (a literal category + its derived category-group are the
# same categorical dimension; count once, not twice).
# ---------------------------------------------------------------------------


def test_search_category_axis_deduped_when_literal_and_group_both_resolve():
    product = _product("P1", category_name="쿠션", category_concept_ids=["concept:Category:쿠션"])
    # Both the literal category concept AND the makeup group concept resolve from
    # the single token "쿠션" (쿠션 is a makeup tab keyword).
    resolved = resolve_query_concepts("쿠션", [product])
    category_ids = {c.concept_id for c in resolved if c.concept_type == "category"}
    assert {"concept:Category:쿠션", "concept:Category:makeup"} <= category_ids
    # ...but the product's overlap counts the categorical dimension once (literal
    # kept, derived-group suppressed), so relevance is not double-inflated.
    outcome = search_products("쿠션", [product])
    assert len(outcome.results) == 1
    categories = [c for c in outcome.results[0].matched_concepts if c.startswith("category:")]
    assert categories == ["category:concept:Category:쿠션"]
    assert outcome.results[0].relevance_score == 1.0


def test_search_category_group_still_counts_when_no_literal_category_matches():
    """Dedupe must not drop the group match when it is the ONLY categorical
    signal (product classifies to the group but carries no matching literal
    category concept)."""
    product = _product(
        "P_lip",
        category_name="립스틱",
        category_concept_ids=["concept:Category:립스틱"],
    )
    # Query resolves the makeup GROUP (via "메이크업") but no literal "립스틱".
    outcome = search_products("메이크업 추천", [product])
    assert len(outcome.results) == 1
    categories = [c for c in outcome.results[0].matched_concepts if c.startswith("category:")]
    assert categories == ["category:concept:Category:makeup"]


# ---------------------------------------------------------------------------
# Result field alias: overlap_concepts mirrors matched_concepts so the shared
# front-end evidence renderer (app.js reads overlap_concepts) consumes search
# and recommend results identically.
# ---------------------------------------------------------------------------


def test_search_result_dict_exposes_overlap_concepts_alias():
    products = [_product("P1", brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"])]
    payload = search_products("설화수 제품", products).to_dict()
    result = payload["results"][0]
    assert result["overlap_concepts"] == result["matched_concepts"]
    assert result["overlap_concepts"] == ["brand:concept:Brand:설화수"]


# ---------------------------------------------------------------------------
# Empty-query POST/GET consistency (SearchRequest.query defaults to "").
# ---------------------------------------------------------------------------


def test_search_request_query_defaults_to_empty_string():
    """A POST body with no `query` is valid (mirrors GET's optional query), so
    the endpoint returns guidance rather than raising an HTTP 422."""
    assert server.SearchRequest().query == ""
    assert server.SearchRequest(top_k=5).query == ""


@pytest.mark.asyncio
async def test_search_empty_query_post_and_get_consistent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = [_search_product("P1")]
    monkeypatch.setattr(server, "demo_state", state)

    get_payload = await server.search_get(query="")
    post_payload = await server.search(server.SearchRequest(query=""))

    for payload in (get_payload, post_payload):
        assert payload["resolved_mode"] == "search"
        assert payload["results"] == []
        assert payload["message"]  # explicit guidance, not a silent empty result
    # Same payload on both verbs (no POST-only 422 for a blank query).
    assert get_payload == post_payload


# ---------------------------------------------------------------------------
# Avoided-ingredient hard filter (Phase 6 B2: `avoided_ingredient_concept_ids`
# keyword — negation queries like "레티놀 없는 크림" exclude carrier products
# entirely; default None leaves every existing caller byte-identical).
# ---------------------------------------------------------------------------


def _avoidance_products() -> list[dict[str, Any]]:
    # Both products match the "보습" query identically (goal axis); only their
    # ingredients differ, so any result difference is the hard filter alone.
    return [
        _product(
            "P_clean",
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
            ingredient_concept_ids=["concept:Ingredient:히알루론산"],
        ),
        _product(
            "P_retinol",
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
            ingredient_concept_ids=["concept:Ingredient:레티놀"],
        ),
    ]


def test_search_avoided_ingredient_hard_filter_excludes_carrier():
    """A product whose ingredient_concept_ids intersects the avoided set is
    skipped entirely (hard filter), even though it matches the query otherwise."""
    outcome = search_products(
        "보습 크림",
        _avoidance_products(),
        avoided_ingredient_concept_ids=["concept:Ingredient:레티놀"],
    )
    ids = [r.product_id for r in outcome.results]
    assert "P_clean" in ids
    assert "P_retinol" not in ids
    # Resolution itself is untouched — only the ranking loop filters.
    assert outcome.resolved is True


def test_search_avoided_default_none_and_empty_do_not_change_results():
    """Omitted / None / [] avoided sets are byte-identical to the pre-B2
    behaviour, so existing callers are unaffected by the signature extension."""
    products = _avoidance_products()
    baseline = search_products("보습 크림", products)
    with_none = search_products("보습 크림", products, avoided_ingredient_concept_ids=None)
    with_empty = search_products("보습 크림", products, avoided_ingredient_concept_ids=[])

    assert {r.product_id for r in baseline.results} == {"P_clean", "P_retinol"}
    assert with_none.to_dict() == baseline.to_dict()
    assert with_empty.to_dict() == baseline.to_dict()


# ---------------------------------------------------------------------------
# Wanted-ingredient hard gate (Phase 6 B2: `ingredient_constraints` keyword).
# A product must satisfy every family (AND) via the shared matcher (structured ∪
# product-name). A name-only carrier earns a `product_name:<관용어>` overlap so it
# survives the "overlap ≥ 1" gate and is classified PRODUCT_MASTER_TRUTH.
# ---------------------------------------------------------------------------


_HYA_S = "concept:Ingredient:소듐하이알루로네이트"
_HYA_A = "concept:Ingredient:하이알루로닉애씨드"


def _hya_constraint() -> IngredientConstraint:
    return IngredientConstraint(
        label="히알루론",
        inci_concept_ids=[_HYA_S, _HYA_A],
        name_surfaces=["히알루론산", "히알루론", "히아루론산"],
        provenance="raw",
    )


def _hya_universe() -> list[dict[str, Any]]:
    # All match the "보습" goal query; only their ingredient/name evidence differs.
    return [
        _product("P_struct", main_benefit_concept_ids=["concept:Goal:보습"],
                 ingredient_concept_ids=[_HYA_S, _HYA_A],
                 representative_product_name="어떤 수분크림"),
        _product("P_name", main_benefit_concept_ids=["concept:Goal:보습"],
                 representative_product_name="그린티히알루론산 로션"),
        _product("P_free", main_benefit_concept_ids=["concept:Goal:보습"],
                 representative_product_name="히알루론프리 크림"),
        _product("P_none", main_benefit_concept_ids=["concept:Goal:보습"],
                 ingredient_concept_ids=["concept:Ingredient:정제수"],
                 representative_product_name="정제수 토너"),
    ]


def test_search_ingredient_constraint_hard_gate_keeps_only_carriers():
    outcome = search_products("보습 크림", _hya_universe(), ingredient_constraints=[_hya_constraint()])
    ids = {r.product_id for r in outcome.results}
    assert ids == {"P_struct", "P_name"}  # free-of + non-carrier excluded
    assert outcome.resolved is True


def test_search_ingredient_constraint_name_only_gets_product_name_axis():
    """A name-only carrier survives (overlap ≥ 1 via product_name) and is
    classified PRODUCT_MASTER_TRUTH (the product name is catalog master truth)."""
    outcome = search_products("보습 크림", _hya_universe(), ingredient_constraints=[_hya_constraint()])
    name_result = next(r for r in outcome.results if r.product_id == "P_name")
    assert "product_name:히알루론" in name_result.matched_concepts
    assert "PRODUCT_MASTER_TRUTH" in name_result.eligibility.evidence_families


def test_search_ingredient_constraint_and_across_families():
    retinol = IngredientConstraint(
        label="레티놀", inci_concept_ids=["concept:Ingredient:레티놀"],
        name_surfaces=["레티놀"], provenance="raw",
    )
    both = _product("P_both", main_benefit_concept_ids=["concept:Goal:보습"],
                    ingredient_concept_ids=[_HYA_S, "concept:Ingredient:레티놀"])
    only_hya = _product("P_hya", main_benefit_concept_ids=["concept:Goal:보습"],
                        ingredient_concept_ids=[_HYA_S])
    outcome = search_products("보습", [both, only_hya],
                              ingredient_constraints=[_hya_constraint(), retinol])
    assert {r.product_id for r in outcome.results} == {"P_both"}  # AND across families


def test_search_ingredient_constraint_default_none_byte_identical():
    products = _hya_universe()
    baseline = search_products("보습 크림", products)
    with_none = search_products("보습 크림", products, ingredient_constraints=None)
    assert with_none.to_dict() == baseline.to_dict()
