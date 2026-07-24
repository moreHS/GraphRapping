"""Phase 8 P8-3b (G5): query-based "related products more" tests.

Plan: fable_doc/plans/2026-07-16_phase8-3_g4_similar_boost_g5_query_related.md §2.
Two layers:
- ``_related_products`` helper unit tests: dedup (max score + that anchor's
  attribution), exclude (1차 results / anchor self / caller-assembled hard
  exclusions), limit cap, final-ranking + tie-break, malformed / non-finite /
  non-positive skip, empty cases, and the [C1] shared_axes deep-copy discipline.
  Plus the two assembly helpers (``_related_anchor_names`` /
  ``_avoided_ingredient_product_ids``).
- server e2e (demo mode): /api/search and /api/ask (both branches) carry an
  additive ``related_products`` key with shared_axes + anchor attribution and no
  change to the existing result schema; the recommend branch preserves every
  upstream hard exclusion (1차 results, owned, avoided-ingredient carriers); an
  unresolved query yields ``related_products == []``.

The 1차 ordering-invariance guarantee is proven by the untouched existing
search/ask/recommend suites staying green — no fixture here rewrites them.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.rec.ingredient_constraint import IngredientConstraint
from src.rec.product_similarity import SimilarProductSignal
from src.rec.query_understanding import QueryInterpretation
from src.rec.search import MatchedConcept
from src.web import server
from src.web.serving_store import DemoServingStore
from src.web.state import DemoState


# ---------------------------------------------------------------------------
# _related_products helper (real DemoServingStore over a synthetic sidecar)
# ---------------------------------------------------------------------------


def _sig(
    pid: str,
    score: float,
    *,
    name: str | None = None,
    axes: list[dict[str, Any]] | None = None,
) -> SimilarProductSignal:
    return SimilarProductSignal(
        product_id=pid,
        neighbor_name=name if name is not None else f"name-{pid}",
        score=score,
        shared_axes=axes if axes is not None else [
            {"axis": "ingredient", "node_key": f"ingredient::{pid}",
             "label": f"axis-{pid}", "idf": 1.5},
        ],
    )


def _store(sidecar: dict[str, list[Any]]) -> DemoServingStore:
    state = DemoState(loaded=True)
    state.similar_ungated = sidecar
    return DemoServingStore(lambda: state)


async def test_related_empty_for_no_anchor_or_no_neighbour() -> None:
    store = _store({"A": [_sig("N", 10.0)]})
    assert await server._related_products([], store=store, exclude_ids=set()) == []
    assert await server._related_products(["MISS"], store=store, exclude_ids=set()) == []


async def test_related_empty_when_store_lacks_ungated_accessor() -> None:
    # get_ungated_similar is duck-typed: a store without it yields [] (this is
    # what keeps the existing DB-mode search fake-store suite green).
    class _NoAccessor:
        async def get_products(self) -> list[dict[str, Any]]:
            return []

    got = await server._related_products(["A"], store=_NoAccessor(), exclude_ids=set())  # type: ignore[arg-type]
    assert got == []


async def test_related_excludes_1cha_results_and_anchor_self() -> None:
    store = _store({"A": [_sig("A", 30.0), _sig("R1", 20.0), _sig("KEEP", 10.0)]})
    got = await server._related_products(["A"], store=store, exclude_ids={"A", "R1"})
    assert [e["product_id"] for e in got] == ["KEEP"]  # anchor self + excluded id dropped
    assert got[0]["anchor_product_id"] == "A"
    assert got[0]["anchor_name"] == "A"  # no anchor_names map → falls back to anchor id


async def test_related_dedup_keeps_max_score_and_its_anchor() -> None:
    store = _store({"A": [_sig("N", 10.0)], "B": [_sig("N", 25.0)]})
    got = await server._related_products(
        ["A", "B"], store=store, exclude_ids=set(),
        anchor_names={"A": "Anchor A", "B": "Anchor B"},
    )
    assert len(got) == 1
    assert got[0]["product_id"] == "N"
    assert got[0]["score"] == 25.0
    assert got[0]["anchor_product_id"] == "B"  # max-score anchor kept
    assert got[0]["anchor_name"] == "Anchor B"


async def test_related_dedup_score_tie_prefers_smaller_anchor_id() -> None:
    store = _store({"A": [_sig("N", 15.0)], "B": [_sig("N", 15.0)]})
    # anchors passed out of order — tie still resolves to the smaller anchor id.
    got = await server._related_products(["B", "A"], store=store, exclude_ids=set())
    assert got[0]["anchor_product_id"] == "A"


async def test_related_final_ranking_ties_break_on_neighbour_id() -> None:
    store = _store({"A": [_sig("Y", 15.0), _sig("X", 15.0)]})
    got = await server._related_products(["A"], store=store, exclude_ids=set())
    assert [e["product_id"] for e in got] == ["X", "Y"]  # score tie → product_id asc


async def test_related_limit_caps_by_score() -> None:
    store = _store({"A": [_sig(f"N{i}", float(i)) for i in range(1, 8)]})
    got = await server._related_products(["A"], store=store, exclude_ids=set(), limit=5)
    assert [e["product_id"] for e in got] == ["N7", "N6", "N5", "N4", "N3"]


async def test_related_skips_malformed_and_bad_scores() -> None:
    store = _store({"A": [
        {"product_id": "DICT_OK", "neighbor_name": "g", "score": 12.0,
         "shared_axes": [{"axis": "x", "node_key": "x", "label": "x", "idf": 1.0}]},
        {"product_id": "", "score": 5.0},          # empty id → skip
        {"product_id": "NOSCORE", "score": None},  # non-numeric → skip
        {"product_id": "NAN", "score": float("nan")},   # non-finite → skip
        {"product_id": "INF", "score": float("inf")},   # non-finite → skip
        {"product_id": "NEG", "score": -3.0},      # non-positive → skip
        {"product_id": "ZERO", "score": 0.0},      # non-positive → skip
    ]})
    got = await server._related_products(["A"], store=store, exclude_ids=set())
    assert [e["product_id"] for e in got] == ["DICT_OK"]  # only the valid dict entry


async def test_related_shared_axes_are_deep_copied() -> None:
    sig = _sig("N", 20.0, axes=[
        {"axis": "ingredient", "node_key": "ingredient::rare", "label": "레어", "idf": 1.1},
    ])
    state = DemoState(loaded=True)
    state.similar_ungated = {"A": [sig]}
    store = DemoServingStore(lambda: state)
    got = await server._related_products(["A"], store=store, exclude_ids=set())
    got[0]["shared_axes"][0]["label"] = "MUTATED"
    # [C1]: the response never aliases store state — the signal's axes are intact.
    assert sig.shared_axes[0]["label"] == "레어"
    assert state.similar_ungated["A"][0].shared_axes[0]["label"] == "레어"


# ---------------------------------------------------------------------------
# assembly helpers
# ---------------------------------------------------------------------------


def test_related_anchor_names_sources_repr_name_with_fallback() -> None:
    results = [
        {"product_id": "P1", "product": {"representative_product_name": "헤라 크림"}},
        {"product_id": "P2", "product": {}},                # no name → pid fallback
        {"product": {"representative_product_name": "x"}},  # no product_id → skipped
    ]
    assert server._related_anchor_names(results) == {"P1": "헤라 크림", "P2": "P2"}


def test_avoided_ingredient_product_ids_mirrors_hard_filter() -> None:
    user = {"scoped_preference_ids": [
        {"edge_type": "AVOIDS_INGREDIENT", "id": "concept:Ingredient:BAD",
         "weight": 1.0, "scope_group": "global"},
    ]}
    product_map = {
        "HAS_BAD": {"product_id": "HAS_BAD", "category_name": "립스틱",
                    "ingredient_concept_ids": ["concept:Ingredient:BAD"]},
        "CLEAN": {"product_id": "CLEAN", "category_name": "립스틱",
                  "ingredient_concept_ids": ["concept:Ingredient:OK"]},
    }
    assert server._avoided_ingredient_product_ids(user, product_map) == {"HAS_BAD"}
    # a user avoiding nothing excludes nothing (no corpus scan effect).
    assert server._avoided_ingredient_product_ids({}, product_map) == set()


# ---------------------------------------------------------------------------
# Server e2e (demo mode)
# ---------------------------------------------------------------------------


def _product(
    pid: str,
    *,
    brand: str,
    category: str,
    ingredients: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "product_id": pid,
        "representative_product_name": f"{pid}-이름",
        "brand_name": brand,
        "brand_id": None,
        "brand_concept_ids": [f"concept:Brand:{brand}"],
        "category_name": category,
        "category_id": None,
        "category_concept_ids": [f"concept:Category:{category}"],
        "ingredient_ids": [],
        "ingredient_concept_ids": ingredients or [],
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


def _client(monkeypatch: pytest.MonkeyPatch, state: DemoState) -> TestClient:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app)


def _search_state() -> DemoState:
    state = DemoState(loaded=True)
    state.serving_products = [
        _product("P1", brand="헤라", category="수분크림"),
        _product("P2", brand="헤라", category="수분크림"),
        _product("NB", brand="설화수", category="립스틱"),  # not matched by "헤라"
    ]
    # NB is P1's ungated cross-category neighbour → a related candidate, never a
    # 1차 result of the "헤라" query.
    state.similar_ungated = {"P1": [_sig("NB", 21.0, name="설화수 이웃")]}
    return state


def test_search_endpoint_carries_related_products(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _search_state())
    resp = client.get("/api/search", params={"query": "헤라"})
    assert resp.status_code == 200
    payload = resp.json()

    # /api/search is unified onto the anonymous /api/ask payload (plan §B2 v3):
    # the interpretation/ingredient_filter/relaxed/category_group/preset_used keys
    # replace the former search-native resolved/resolved_concepts/result_count,
    # while `message` (no-concept rule) is preserved.
    assert set(payload) == {
        "query", "interpretation", "resolved_mode", "relaxed", "category_group",
        "preset_used", "message", "ingredient_filter", "results", "related_products",
        # [A1] pin trace fields (additive; empty for this no-product query).
        "pinned_product_ids", "pinned_dropped",
        # [A2] labeled exclusion audit (additive; empty for this no-exclusion query).
        "excluded",
        # [A3] preferred-ingredient surface (additive; empty for this query).
        "ingredient_preferences",
    }
    assert {r["product_id"] for r in payload["results"]} == {"P1", "P2"}
    # existing search result-item shape unchanged.
    assert {"product_id", "product", "overlap_concepts", "matched_concepts",
            "relevance_score", "eligibility"} <= set(payload["results"][0])

    related = payload["related_products"]
    assert [e["product_id"] for e in related] == ["NB"]  # neighbour, not a 1차 result
    entry = related[0]
    assert entry["neighbor_name"] == "설화수 이웃"
    assert entry["score"] == 21.0
    assert entry["anchor_product_id"] == "P1"
    assert entry["anchor_name"] == "P1-이름"  # sourced from the 1차 result profile
    assert entry["shared_axes"] and entry["shared_axes"][0]["label"] == "axis-NB"


def test_search_unresolved_query_yields_empty_related(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _search_state())
    resp = client.get("/api/search", params={"query": ""})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["results"] == []
    assert payload["related_products"] == []
    assert payload["message"]  # no-concept guidance is still present


def test_ask_search_branch_carries_related_products(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _search_state())
    resp = client.post("/api/ask", json={"query": "헤라"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resolved_mode"] == "search"
    assert {r["product_id"] for r in payload["results"]} == {"P1", "P2"}
    related = payload["related_products"]
    assert [e["product_id"] for e in related] == ["NB"]
    assert related[0]["anchor_product_id"] == "P1"
    assert related[0]["shared_axes"]


def _recommend_state() -> DemoState:
    state = DemoState(loaded=True)
    state.serving_products = [
        _product("P_anchor", brand="헤라", category="수분크림"),
        _product("N_result", brand="헤라", category="수분크림"),
        _product("N_owned", brand="릴리", category="립스틱"),
        _product("N_avoided", brand="릴리", category="립스틱",
                 ingredients=["concept:Ingredient:BAD"]),
        _product("N_clean", brand="릴리", category="립스틱"),
    ]
    state.serving_users = [{
        "user_id": "U1",
        "scoped_preference_ids": [
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:헤라", "weight": 1.0,
             "scope_group": None, "source_sections": ["chat.brand"]},
        ],
        "owned_product_ids": [{"id": "product:N_owned", "weight": 1.0}],
    }]
    # All four neighbours hang off the one skincare anchor; only N_clean survives
    # the recommend-branch hard exclusions.
    state.similar_ungated = {"P_anchor": [
        _sig("N_result", 25.0), _sig("N_owned", 24.0),
        _sig("N_avoided", 23.0), _sig("N_clean", 22.0),
    ]}
    return state


# The interpretation an LLM would produce for "헤라 수분크림 (BAD 성분 제외)":
# brand + skincare group narrow the 1차 universe to the two skincare products,
# and BAD is flipped to the avoided side (propagates to the related surface).
_RECOMMEND_INTERP = QueryInterpretation(
    query="헤라 수분크림",
    intent="search",
    resolved_concepts=[
        MatchedConcept("brand", "concept:Brand:헤라", "헤라", "헤라"),
        MatchedConcept("category", "concept:Category:skincare", "크림", "스킨케어"),
    ],
    avoided_ingredient_concept_ids=["concept:Ingredient:BAD"],
    unresolved_terms=[],
    llm_used=True,
)


def test_ask_recommend_branch_related_preserves_hard_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, _recommend_state())
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: _RECOMMEND_INTERP)
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "헤라 수분크림"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resolved_mode"] == "recommend"
    # 1차 universe is the skincare tab narrowed to 헤라 → exactly the two anchors.
    assert {r["product_id"] for r in payload["results"]} == {"P_anchor", "N_result"}

    related_ids = [e["product_id"] for e in payload["related_products"]]
    assert related_ids == ["N_clean"]        # only the clean cross-category neighbour
    assert "N_result" not in related_ids     # (a) 1차 result excluded
    assert "N_owned" not in related_ids      # (b) owned product excluded
    assert "N_avoided" not in related_ids    # (c) avoided-ingredient carrier excluded

    entry = payload["related_products"][0]
    assert entry["anchor_product_id"] == "P_anchor"
    assert entry["anchor_name"] == "P_anchor-이름"
    assert entry["shared_axes"]


def test_ask_search_branch_related_respects_query_negation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A query-negated ingredient ("레티놀 없는", Phase 6) hard-filters the 1차
    # results inside search_products; the related section must not reintroduce
    # a product carrying that ingredient through an ungated neighbour.
    state = _search_state()
    state.serving_products.append(
        _product("NB_RET", brand="설화수", category="립스틱",
                 ingredients=["concept:Ingredient:레티놀"]),
    )
    # NB_RET is P1's STRONGEST ungated neighbour — without the query-negation
    # exclusion it would top the related list.
    state.similar_ungated = {
        "P1": [_sig("NB_RET", 30.0, name="레티놀 이웃"), _sig("NB", 21.0, name="설화수 이웃")],
    }
    interp = QueryInterpretation(
        query="헤라 레티놀 없는",
        intent="search",
        resolved_concepts=[MatchedConcept("brand", "concept:Brand:헤라", "헤라", "헤라")],
        avoided_ingredient_concept_ids=["concept:Ingredient:레티놀"],
        unresolved_terms=[],
        llm_used=False,
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)

    client = _client(monkeypatch, state)
    resp = client.post("/api/ask", json={"query": "헤라 레티놀 없는"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["resolved_mode"] == "search"

    # 1차: the avoided-ingredient product is hard-filtered by search_products.
    assert "NB_RET" not in {r["product_id"] for r in payload["results"]}
    # related: the same exclusion holds — the clean neighbour surfaces instead.
    related_ids = [e["product_id"] for e in payload["related_products"]]
    assert "NB_RET" not in related_ids
    assert related_ids == ["NB"]


def test_ask_recommend_branch_related_requires_wanted_ingredient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[B2] With an ACTIVE wanted-ingredient filter, a related neighbour must pass
    the same matcher: a cross-category NON-carrier is dropped even when it is the
    strongest ungated neighbour, while a carrier in another tab still surfaces."""
    _HYA = "concept:Ingredient:소듐하이알루로네이트"
    state = DemoState(loaded=True)
    state.serving_products = [
        _product("P_anchor", brand="헤라", category="수분크림", ingredients=[_HYA]),
        _product("NB_HAS", brand="릴리", category="립스틱", ingredients=[_HYA]),  # carrier, makeup tab
        _product("NB_NO", brand="릴리", category="립스틱"),                        # non-carrier
    ]
    state.serving_users = [{
        "user_id": "U1",
        "scoped_preference_ids": [
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:헤라", "weight": 1.0,
             "scope_group": None, "source_sections": ["chat.brand"]},
        ],
    }]
    # NB_NO is the STRONGEST neighbour — without the matcher filter it would top
    # the related list; the require_ids gate drops it and keeps the carrier NB_HAS.
    state.similar_ungated = {"P_anchor": [_sig("NB_NO", 30.0), _sig("NB_HAS", 20.0)]}

    interp = QueryInterpretation(
        query="히알루론 수분크림",
        intent="search",
        resolved_concepts=[
            MatchedConcept("category", "concept:Category:skincare", "크림", "스킨케어"),
            MatchedConcept("ingredient", _HYA, "히알루론", "히알루론"),
        ],
        avoided_ingredient_concept_ids=[],
        unresolved_terms=[],
        llm_used=True,
        ingredient_constraints=[
            IngredientConstraint("히알루론", [_HYA], ["히알루론"], "raw"),
        ],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)

    client = _client(monkeypatch, state)
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "히알루론 수분크림"}).json()
    assert payload["resolved_mode"] == "recommend"
    assert payload["ingredient_filter"]["applied"] is True
    # 1차 universe is the skincare tab gated to the hyaluron carrier.
    assert {r["product_id"] for r in payload["results"]} == {"P_anchor"}
    related_ids = [e["product_id"] for e in payload["related_products"]]
    assert related_ids == ["NB_HAS"]  # carrier surfaces
    assert "NB_NO" not in related_ids  # non-carrier dropped despite the higher score
