"""POST /api/ask tests (Phase 6 Track B, B2 —
fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md §4).

Covers the plan's B2 completion criteria end-to-end through TestClient:
  (a) no user_id → anonymous search mode + interpretation payload.
  (b) user_id + query → query-scoped recommend; query-injected concepts'
      explanation paths are relabeled "질의에서 언급" while genuine stored
      preferences keep their real user_edge.
  (c) [C1] non-persistence: /api/ask must never leak the request-scoped query
      injection into the serving store's shared user dict — /api/recommend
      before and after an ask is identical, and the store profile carries no
      source_sections == ["query"] entry.
  (d) avoided-ingredient negation queries hard-filter carrier products in BOTH
      modes (search + recommend).
  (e) empty query-concept ∩ category-universe intersection → automatic
      relaxation (relaxed=true) with results still returned.
  (f) guards: blank query / over-length query → 400; unknown preset → 400
      (reuses the /api/recommend preset validation).
  (g) unresolved_terms from the interpretation are surfaced in the response
      (LLM path faked via monkeypatching understand_query — the dictionary
      fallback never produces unresolved terms by design).
  (h) /api/recommend regression: covered by the untouched existing suite.

The demo-state fixture pattern mirrors tests/test_serving_store_mode.py
(synthetic serving profiles on a monkeypatched module-level demo_state); the
user is scoped-only (`scoped_preference_ids`), matching real serving users so
the scoped-preference injection path — not the ignored legacy fields — is what
is exercised. GRAPHRAPPING_QUERY_LLM stays unset, so understand_query uses the
dictionary fallback unless a test injects a fake interpretation.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.rec.query_understanding import QueryInterpretation
from src.rec.search import MatchedConcept
from src.web import server
from src.web.state import DemoState


# ---------------------------------------------------------------------------
# Fixtures: synthetic serving profiles (scoped-only user, 3 products)
# ---------------------------------------------------------------------------


def _product(pid: str, category_name: str, ingredient_concept_ids: list[str]) -> dict[str, Any]:
    """A skincare product matching the "보습 크림" query on goal + keyword axes."""
    return {
        "product_id": pid,
        "brand_name": "헤라",
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:헤라"],
        "category_name": category_name,
        "category_id": None,
        "category_concept_ids": [f"concept:Category:{category_name}"],
        "ingredient_ids": [],
        "ingredient_concept_ids": ingredient_concept_ids,
        "main_benefit_ids": [],
        "main_benefit_concept_ids": ["concept:Goal:보습"],
        "top_keyword_ids": [{"id": "kw_moisturizing", "score": 0.9}],
        "top_bee_attr_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [{"id": "concern_dryness", "score": 0.9}],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_coused_product_ids": [],
        "top_comparison_product_ids": [],
        "review_count_all": 50,
    }


def _lipstick(pid: str = "P_lip") -> dict[str, Any]:
    """A makeup product with no signal overlap — outside the skincare universe."""
    return {
        "product_id": pid,
        "brand_name": "릴리",
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:릴리"],
        "category_name": "립스틱",
        "category_id": None,
        "category_concept_ids": ["concept:Category:립스틱"],
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
    }


def _scoped_user(uid: str = "U1") -> dict[str, Any]:
    """Scoped-only user (like every real serving user): collect_preference_ids
    short-circuits to scoped entries, so legacy-field injection would be a no-op
    — the tests must prove the scoped path is what /api/ask uses."""
    return {
        "user_id": uid,
        "scoped_preference_ids": [
            {"edge_type": "PREFERS_BRAND", "id": "concept:Brand:헤라", "weight": 1.0,
             "scope_group": None, "source_sections": ["chat.brand"]},
            {"edge_type": "HAS_CONCERN", "id": "concept:Concern:concern_dryness", "weight": 0.5,
             "scope_group": None, "source_sections": ["chat.face.skin_concerns"]},
        ],
    }


@pytest.fixture()
def ask_env(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, DemoState]:
    """Demo-mode TestClient over a synthetic loaded DemoState.

    Returns (client, state) so tests can inspect the store-side user dict
    directly (the C1 non-persistence assertions).
    """
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)

    state = DemoState(loaded=True)
    state.serving_products = [
        _product("P_moist", "수분크림", ["concept:Ingredient:히알루론산"]),
        _product("P_retinol", "탄력크림", ["concept:Ingredient:레티놀"]),
        _lipstick("P_lip"),
    ]
    state.serving_users = [_scoped_user("U1")]
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app), state


def _fake_interp(
    query: str,
    *,
    concepts: list[MatchedConcept] | None = None,
    avoided: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> QueryInterpretation:
    return QueryInterpretation(
        query=query,
        intent="search",
        resolved_concepts=list(concepts or []),
        avoided_ingredient_concept_ids=list(avoided or []),
        unresolved_terms=list(unresolved or []),
        llm_used=True,
    )


def _patch_understanding(monkeypatch: pytest.MonkeyPatch, interp: QueryInterpretation) -> None:
    monkeypatch.setattr(server, "understand_query", lambda _query, _products: interp)


# The interpretation an LLM would produce for "레티놀 없는 보습 크림": positive
# goal/keyword/category plus 레티놀 flipped to the avoided side (the dictionary
# fallback cannot understand the negation, so tests fake the LLM output).
def _no_retinol_interp(query: str, unresolved: list[str] | None = None) -> QueryInterpretation:
    return _fake_interp(
        query,
        concepts=[
            MatchedConcept("goal", "보습", "보습", "보습"),
            MatchedConcept("keyword", "kw_moisturizing", "보습", "보습"),
            MatchedConcept("category", "concept:Category:skincare", "크림", "스킨케어"),
        ],
        avoided=["concept:Ingredient:레티놀"],
        unresolved=unresolved,
    )


# ---------------------------------------------------------------------------
# (a) no user_id → search mode
# ---------------------------------------------------------------------------


def test_ask_without_user_runs_search_mode(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    resp = client.post("/api/ask", json={"query": "보습 크림 추천해줘"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "search"
    assert payload["relaxed"] is False
    assert payload["preset_used"] is None
    # "크림" resolves the skincare category group from the query itself.
    assert payload["category_group"] == "skincare"

    interp = payload["interpretation"]
    assert interp["llm_used"] is False  # GRAPHRAPPING_QUERY_LLM unset → dictionary fallback
    resolved_ids = {c["concept_id"] for c in interp["resolved_concepts"]}
    assert "보습" in resolved_ids

    ids = [r["product_id"] for r in payload["results"]]
    assert set(ids) == {"P_moist", "P_retinol"}  # search-native results, lipstick unmatched
    # Native search item shape (overlap_concepts alias + eligibility) preserved.
    assert payload["results"][0]["overlap_concepts"]
    assert "eligibility" in payload["results"][0]


def test_ask_query_category_group_overrides_request_hint(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    # Query resolves skincare → wins over the explicit category_group hint.
    resp = client.post("/api/ask", json={"query": "보습 크림", "category_group": "makeup"})
    assert resp.status_code == 200
    assert resp.json()["category_group"] == "skincare"

    # No category concept in the query ("촉촉한 제품" → keyword only) → hint used.
    resp = client.post("/api/ask", json={"query": "촉촉한 제품", "category_group": "makeup"})
    assert resp.status_code == 200
    assert resp.json()["category_group"] == "makeup"


# ---------------------------------------------------------------------------
# (b) user_id + query → recommend mode with "질의에서 언급" path relabel
# ---------------------------------------------------------------------------


def test_ask_with_user_recommend_mode_rewrites_query_user_edges(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림 추천해줘"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "recommend"
    assert payload["relaxed"] is False
    assert payload["category_group"] == "skincare"
    results = payload["results"]
    assert results, "query-scoped recommend must produce results for this fixture"
    assert {r["product_id"] for r in results} <= {"P_moist", "P_retinol"}  # lipstick outside universe

    paths = [p for r in results for p in r["explanation_paths"]]
    edges_by_id: dict[str, set[str]] = {}
    for p in paths:
        edges_by_id.setdefault(p["id"], set()).add(p["user_edge"])

    # Query-injected concepts (goal 보습 / keyword kw_moisturizing) are relabeled.
    assert edges_by_id.get("kw_moisturizing") == {"질의에서 언급"}
    assert edges_by_id.get("보습") == {"질의에서 언급"}
    # Genuine stored preferences keep their real serving edge_type labels.
    assert edges_by_id.get("concept:Brand:헤라") == {"PREFERS_BRAND"}
    assert edges_by_id.get("concern_dryness") == {"HAS_CONCERN"}
    # Recommend-native result shape flows through (scores + explanation intact).
    assert "final_score" in results[0] and "explanation" in results[0]


def test_ask_rewrites_query_edge_when_path_id_is_normalized_form(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F5] The user_edge relabel must compare on the normalized signal key, not
    the raw id. candidate_generator re-normalizes the goal axis, so an injected
    goal in IRI form (concept:Goal:보습) surfaces as a path id of "보습". A raw
    string membership test would miss it and leave the query goal labeled
    WANTS_GOAL; the normalized comparison relabels it to "질의에서 언급"."""
    client, _state = ask_env
    _patch_understanding(
        monkeypatch,
        _fake_interp(
            "보습 크림",
            # IRI-form goal id (mismatched vs the normalized "보습" the path carries).
            concepts=[MatchedConcept("goal", "concept:Goal:보습", "보습", "보습")],
        ),
    )
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림"})
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results

    edges_by_id: dict[str, set[str]] = {}
    for r in results:
        for p in r["explanation_paths"]:
            edges_by_id.setdefault(p["id"], set()).add(p["user_edge"])

    # The injected goal path (normalized id "보습") is relabeled despite the
    # injected concept_id being the IRI form — this is the F5 fix.
    assert edges_by_id.get("보습") == {"질의에서 언급"}
    # A genuine stored preference whose normalized key does not match any injected
    # concept keeps its real edge (no false relabel from normalization).
    assert edges_by_id.get("concept:Brand:헤라") == {"PREFERS_BRAND"}


# ---------------------------------------------------------------------------
# (c) [C1] non-persistence: injection must never touch the shared store dict
# ---------------------------------------------------------------------------


def test_ask_query_injection_does_not_persist_into_store(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, state = ask_env
    recommend_body = {"user_id": "U1", "category_group": "all", "top_k": 10}

    stored_user = next(u for u in state.serving_users if u["user_id"] == "U1")
    scoped_before = copy.deepcopy(stored_user["scoped_preference_ids"])

    baseline = client.post("/api/recommend", json=recommend_body)
    assert baseline.status_code == 200

    ask_resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림 추천해줘"})
    assert ask_resp.status_code == 200
    assert ask_resp.json()["results"]  # the injection actually ran (results exist)

    after = client.post("/api/recommend", json=recommend_body)
    assert after.status_code == 200

    # 1) /api/recommend is bit-identical before and after the ask.
    assert after.json() == baseline.json()
    # 2) The store-side user dict carries no query-injected entry at all.
    assert stored_user["scoped_preference_ids"] == scoped_before
    assert not any(
        item.get("source_sections") == ["query"]
        for item in stored_user["scoped_preference_ids"]
    )


# ---------------------------------------------------------------------------
# (d) avoided-ingredient negation → hard filter in both modes
# ---------------------------------------------------------------------------


def test_ask_search_mode_avoided_ingredient_excluded(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state = ask_env
    _patch_understanding(monkeypatch, _no_retinol_interp("레티놀 없는 보습 크림"))

    resp = client.post("/api/ask", json={"query": "레티놀 없는 보습 크림"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "search"
    ids = [r["product_id"] for r in payload["results"]]
    assert "P_retinol" not in ids  # carrier hard-filtered
    assert "P_moist" in ids
    assert payload["interpretation"]["avoided_ingredient_concept_ids"] == ["concept:Ingredient:레티놀"]


def test_ask_recommend_mode_avoided_ingredient_excluded(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state = ask_env
    # Without avoidance the retinol product IS recommendable for this user.
    baseline = client.post("/api/recommend", json={"user_id": "U1", "top_k": 10})
    assert "P_retinol" in {r["product_id"] for r in baseline.json()["results"]}

    _patch_understanding(monkeypatch, _no_retinol_interp("레티놀 없는 보습 크림"))
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "레티놀 없는 보습 크림"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "recommend"
    ids = [r["product_id"] for r in payload["results"]]
    assert "P_retinol" not in ids  # AVOIDS_INGREDIENT injection → candidate hard filter
    assert "P_moist" in ids


# ---------------------------------------------------------------------------
# (d2) [F1] avoided-ingredient negation works WITHOUT the LLM (no monkeypatch of
# understand_query): the live dictionary-fallback path must read "레티놀 없는" and
# hard-filter the retinol carrier in BOTH modes. This is the regression fix for
# the live-reproduced bug where the fallback misread 레티놀 as a positive concept
# and surfaced a retinol product as the #1 result for a "no-retinol" query.
# ---------------------------------------------------------------------------


def test_ask_search_mode_negation_via_live_fallback_excludes_carrier(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env  # GRAPHRAPPING_QUERY_LLM unset → real dictionary fallback
    resp = client.post("/api/ask", json={"query": "레티놀 없는 수분크림"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "search"
    interp = payload["interpretation"]
    assert interp["llm_used"] is False  # proves the fallback path produced this
    # The fallback itself detected the negation (avoided is non-empty)...
    assert interp["avoided_ingredient_concept_ids"] == ["concept:Ingredient:레티놀"]
    ids = [r["product_id"] for r in payload["results"]]
    # ...so the retinol carrier is absent and, crucially, not the #1 result.
    assert "P_retinol" not in ids
    assert "P_moist" in ids


def test_ask_recommend_mode_negation_via_live_fallback_excludes_carrier(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env
    # Baseline: retinol product IS recommendable for this user without avoidance.
    baseline = client.post("/api/recommend", json={"user_id": "U1", "top_k": 10})
    assert "P_retinol" in {r["product_id"] for r in baseline.json()["results"]}

    resp = client.post("/api/ask", json={"user_id": "U1", "query": "레티놀 없는 수분크림"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "recommend"
    assert payload["interpretation"]["llm_used"] is False
    assert payload["interpretation"]["avoided_ingredient_concept_ids"] == ["concept:Ingredient:레티놀"]
    ids = [r["product_id"] for r in payload["results"]]
    assert payload["results"], "query-scoped recommend must still return the non-retinol product"
    assert "P_retinol" not in ids
    assert "P_moist" in ids


# ---------------------------------------------------------------------------
# (d3) [F4] recommend-mode response carries the KPI meta fields (parity with
# /api/recommend) so the frontend dashboard shows real counts, not '-'.
# ---------------------------------------------------------------------------


def test_ask_recommend_mode_exposes_kpi_meta_fields(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림 추천해줘"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "recommend"
    assert payload["total_product_count"] == 3  # fixture has 3 serving products
    # skincare tab universe (수분크림 + 탄력크림), makeup lipstick excluded.
    assert payload["category_filtered_count"] == 2
    assert isinstance(payload["candidate_count"], int) and payload["candidate_count"] >= 1
    assert isinstance(payload["weights_used"], dict) and payload["weights_used"]


def test_ask_search_mode_has_no_recommend_kpi_fields(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    """The KPI meta is recommend-mode only; search mode keeps its native shape."""
    client, _state = ask_env
    payload = client.post("/api/ask", json={"query": "보습 크림 추천해줘"}).json()
    assert payload["resolved_mode"] == "search"
    assert "candidate_count" not in payload
    assert "weights_used" not in payload


# ---------------------------------------------------------------------------
# (e) empty intersection → automatic relaxation, results still returned
# ---------------------------------------------------------------------------


def test_ask_relaxes_universe_when_query_concepts_match_no_product(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env
    # "주름" resolves concern_wrinkles, which no fixture product carries →
    # concept ∩ universe = ∅ → relaxed candidate scope, not an empty response.
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "주름 개선 크림"})
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["resolved_mode"] == "recommend"
    assert payload["relaxed"] is True
    assert payload["results"], "relaxation must fall back to the category universe, not return nothing"
    assert {r["product_id"] for r in payload["results"]} <= {"P_moist", "P_retinol"}


# ---------------------------------------------------------------------------
# (f) guards: blank / over-length query → 400, unknown preset → 400
# ---------------------------------------------------------------------------


def test_ask_blank_query_returns_400(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    assert client.post("/api/ask", json={"query": ""}).status_code == 400
    assert client.post("/api/ask", json={"query": "   "}).status_code == 400
    assert client.post("/api/ask", json={}).status_code == 400  # query defaults to ""


def test_ask_query_over_500_chars_returns_400(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    assert client.post("/api/ask", json={"query": "가" * 501}).status_code == 400
    # Exactly at the limit is accepted (guard is strictly greater-than).
    assert client.post("/api/ask", json={"query": "가" * 500}).status_code == 200


def test_ask_unknown_preset_returns_400(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림", "preset": "nonexistent"})
    assert resp.status_code == 400
    assert "nonexistent" in resp.json()["detail"]


def test_ask_unknown_user_returns_404(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    assert client.post("/api/ask", json={"user_id": "ghost", "query": "보습 크림"}).status_code == 404


def test_ask_preset_reuses_recommend_preset_resolution(ask_env: tuple[TestClient, DemoState]) -> None:
    client, _state = ask_env
    resp = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림", "preset": "trusted"})
    assert resp.status_code == 200
    preset_used = resp.json()["preset_used"]
    assert preset_used is not None
    assert preset_used["key"] == "trusted"
    assert preset_used["mode"] == "strict"  # same resolved parameters as /api/recommend


# ---------------------------------------------------------------------------
# (g) unresolved_terms surfaced (hallucinated term injected via fake LLM)
# ---------------------------------------------------------------------------


def test_ask_surfaces_unresolved_terms(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state = ask_env
    _patch_understanding(
        monkeypatch,
        _fake_interp(
            "저분자 보습 크림",
            concepts=[MatchedConcept("goal", "보습", "보습", "보습")],
            unresolved=["저분자"],
        ),
    )
    resp = client.post("/api/ask", json={"query": "저분자 보습 크림"})
    assert resp.status_code == 200
    interp = resp.json()["interpretation"]
    assert interp["unresolved_terms"] == ["저분자"]  # surfaced, never silently dropped
    assert interp["llm_used"] is True
