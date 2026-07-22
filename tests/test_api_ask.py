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

from src.rec.ingredient_constraint import IngredientConstraint
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
    profile_refs: list[str] | None = None,
    ingredient_constraints: list[IngredientConstraint] | None = None,
) -> QueryInterpretation:
    return QueryInterpretation(
        query=query,
        intent="search",
        resolved_concepts=list(concepts or []),
        avoided_ingredient_concept_ids=list(avoided or []),
        unresolved_terms=list(unresolved or []),
        llm_used=True,
        profile_refs=list(profile_refs or []),
        ingredient_constraints=list(ingredient_constraints or []),
    )


def _repurchase_user(uid: str = "U_re") -> dict[str, Any]:
    """Scoped user with a REPURCHASES_CATEGORY pref (수분크림) that is NOT already a
    PREFERS_CATEGORY — so the ``repurchase`` profile-ref maps REPURCHASES_CATEGORY →
    PREFERS_CATEGORY and genuinely (idempotently) injects a new scoring signal
    (``injected: true``)."""
    return {
        "user_id": uid,
        "scoped_preference_ids": [
            {"edge_type": "HAS_CONCERN", "id": "concept:Concern:concern_dryness", "weight": 0.5,
             "scope_group": None, "source_sections": ["chat.face.skin_concerns"]},
            {"edge_type": "REPURCHASES_CATEGORY", "id": "concept:Category:수분크림", "weight": 1.0,
             "scope_group": None, "source_sections": ["purchase.repurchase"]},
        ],
    }


def _owner_user(uid: str = "U_own") -> dict[str, Any]:
    """Scoped user who OWNS P_moist (owned_product_ids — the shape
    extract_owned_product_ids reads) — used to prove ``owned`` is display-only."""
    return {
        "user_id": uid,
        "scoped_preference_ids": [
            {"edge_type": "HAS_CONCERN", "id": "concept:Concern:concern_dryness", "weight": 0.5,
             "scope_group": None, "source_sections": ["chat.face.skin_concerns"]},
        ],
        "owned_product_ids": [{"id": "product:P_moist"}],
    }


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

    # 1) /api/recommend is bit-identical before and after the ask (raw bytes, F8).
    assert after.content == baseline.content
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


# ---------------------------------------------------------------------------
# (g2) [F2] unreflected terms surfaced by the LIVE dictionary fallback (no LLM,
# no monkeypatch): tokens the dictionary reflected nowhere reach the response
# so the frontend can render them, instead of two queries collapsing silently.
# ---------------------------------------------------------------------------


def test_ask_surfaces_unreflected_terms_via_live_fallback(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, _state = ask_env  # GRAPHRAPPING_QUERY_LLM unset → real dictionary fallback
    resp = client.post("/api/ask", json={"query": "피부에 맞는 스킨케어"})
    assert resp.status_code == 200
    interp = resp.json()["interpretation"]

    assert interp["llm_used"] is False  # proves the live fallback produced this
    resolved_ids = {c["concept_id"] for c in interp["resolved_concepts"]}
    assert "concept:Category:skincare" in resolved_ids  # category still reflected
    # The unreflected tokens are surfaced (contract the frontend renders as chips
    # + a warning banner) rather than dropped silently.
    assert interp["unresolved_terms"] == ["피부에", "맞는"]
    assert interp["warnings"] and "반영되지 않았습니다" in interp["warnings"][0]


# ---------------------------------------------------------------------------
# (h) [F4-c''] profile-reference selection → deterministic injection / display
# ---------------------------------------------------------------------------


def test_ask_profile_ref_same_edge_class_is_scoring_no_op(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-edge class (concerns → HAS_CONCERN) reflects an ALREADY-active stored
    preference, so it is a scoring no-op: results are identical to the same query
    with no profile ref, and the applied entry reports injected=False."""
    client, _state = ask_env
    base_concepts = [MatchedConcept("goal", "보습", "보습", "보습")]

    _patch_understanding(monkeypatch, _fake_interp("보습 크림", concepts=base_concepts))
    baseline = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림"}).json()

    _patch_understanding(
        monkeypatch, _fake_interp("보습 크림", concepts=base_concepts, profile_refs=["concerns"]))
    variant = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림"}).json()

    # No-op: the concern is already a stored HAS_CONCERN pref → scores unchanged.
    assert variant["results"] == baseline["results"]
    refs = variant["applied_profile_refs"]
    assert [r["class"] for r in refs] == ["concerns"]
    assert refs[0]["injected"] is False
    assert refs[0]["concepts"]  # the user's concern concept is surfaced for display
    # Baseline (no profile_refs) still carries the field, empty.
    assert baseline["applied_profile_refs"] == []


def test_ask_profile_ref_repurchase_injects_new_pref(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """repurchase maps REPURCHASES_CATEGORY → PREFERS_CATEGORY, a scoring edge the
    user does not already carry → a genuine (idempotent) injection (injected=True)."""
    client, state = ask_env
    state.serving_users.append(_repurchase_user("U_re"))

    _patch_understanding(monkeypatch, _fake_interp("크림", profile_refs=["repurchase"]))
    payload = client.post("/api/ask", json={"user_id": "U_re", "query": "크림"}).json()

    refs = payload["applied_profile_refs"]
    assert [r["class"] for r in refs] == ["repurchase"]
    assert refs[0]["injected"] is True
    assert "수분크림" in refs[0]["concepts"]
    assert payload["results"], "recommend must still return results after the boost"


def test_ask_profile_ref_owned_is_display_only(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """owned is never injected (G4 already boosts owned on the shared path): results
    are identical to the no-profile-ref baseline, and the class shows injected=False."""
    client, state = ask_env
    state.serving_users.append(_owner_user("U_own"))

    _patch_understanding(monkeypatch, _fake_interp("크림"))
    baseline = client.post("/api/ask", json={"user_id": "U_own", "query": "크림"}).json()

    _patch_understanding(monkeypatch, _fake_interp("크림", profile_refs=["owned"]))
    variant = client.post("/api/ask", json={"user_id": "U_own", "query": "크림"}).json()

    assert variant["results"] == baseline["results"]  # owned injects nothing
    refs = variant["applied_profile_refs"]
    assert [r["class"] for r in refs] == ["owned"]
    assert refs[0]["injected"] is False
    assert refs[0]["concepts"] == ["P_moist"]  # product: prefix stripped, segment shown


def test_ask_search_mode_omits_applied_profile_refs_and_preserves_results(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[anonymous identity] Even when the LLM selects profile-ref classes, the
    anonymous search response OMITS applied_profile_refs entirely and its result
    ids/scores are unaffected by the selection (the join never runs without a user)."""
    client, _state = ask_env
    base_concepts = [MatchedConcept("goal", "보습", "보습", "보습")]

    _patch_understanding(monkeypatch, _fake_interp("보습 크림", concepts=base_concepts))
    baseline = client.post("/api/ask", json={"query": "보습 크림"}).json()

    _patch_understanding(
        monkeypatch,
        _fake_interp("보습 크림", concepts=base_concepts, profile_refs=["concerns", "goals"]))
    variant = client.post("/api/ask", json={"query": "보습 크림"}).json()

    assert "applied_profile_refs" not in baseline
    assert "applied_profile_refs" not in variant
    # Result identity (ids AND full dicts): selection has zero effect on anonymous.
    assert variant["results"] == baseline["results"]
    # Class names still surface in the interpretation contract (join is user-only).
    assert variant["interpretation"]["profile_refs"] == ["concerns", "goals"]


def test_ask_profile_ref_injection_does_not_persist_into_store(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[C1] The repurchase → PREFERS_CATEGORY injection lands only on the request's
    deep copy: the shared store user is untouched and /api/recommend is identical
    before and after."""
    client, state = ask_env
    state.serving_users.append(_repurchase_user("U_re"))
    stored = next(u for u in state.serving_users if u["user_id"] == "U_re")
    scoped_before = copy.deepcopy(stored["scoped_preference_ids"])
    recommend_body = {"user_id": "U_re", "category_group": "all", "top_k": 10}

    before = client.post("/api/recommend", json=recommend_body)
    assert before.status_code == 200

    _patch_understanding(monkeypatch, _fake_interp("크림", profile_refs=["repurchase"]))
    ask_resp = client.post("/api/ask", json={"user_id": "U_re", "query": "크림"}).json()
    assert ask_resp["applied_profile_refs"][0]["injected"] is True  # injection ran

    after = client.post("/api/recommend", json=recommend_body)
    assert after.json() == before.json()  # recommend unaffected by the ask
    assert stored["scoped_preference_ids"] == scoped_before  # store untouched
    assert not any(
        item.get("source_sections") == ["profile_ref"]
        for item in stored["scoped_preference_ids"]
    )


def test_ask_applied_profile_refs_payload_shape(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """applied_profile_refs is a list of {class:str, concepts:list[str],
    injected:bool} — the exact shape the frontend summary line + chips render."""
    client, _state = ask_env
    _patch_understanding(monkeypatch, _fake_interp("보습 크림", profile_refs=["concerns"]))
    refs = client.post(
        "/api/ask", json={"user_id": "U1", "query": "보습 크림"}
    ).json()["applied_profile_refs"]
    assert isinstance(refs, list) and refs
    for entry in refs:
        assert set(entry) == {"class", "concepts", "injected"}
        assert isinstance(entry["class"], str)
        assert isinstance(entry["concepts"], list)
        assert isinstance(entry["injected"], bool)


# ---------------------------------------------------------------------------
# (i) [B2] Wanted-ingredient hard filter + relaxation + /api/search unification.
# The marquee bug: "히알루론 든거 뭐 좋은거 없나" returned products with no hyaluron
# evidence. The fixture products carry the REAL catalog INCI the alias map points
# at (소듐하이알루로네이트), so the LIVE dictionary fallback (GRAPHRAPPING_QUERY_LLM
# unset) builds a raw constraint end-to-end — no monkeypatch of understand_query.
# ---------------------------------------------------------------------------

_HYA_S = "concept:Ingredient:소듐하이알루로네이트"
_MARQUEE = "히알루론 든거 뭐 좋은거 없나"


def _hya_name_product(pid: str = "P_name") -> dict[str, Any]:
    """A carrier by NAME only (no structured hyaluron), but brand 헤라 so it is a
    recommend candidate — used to prove the product_name overlap axis is attached."""
    product = _product(pid, "수분크림", [])
    product["representative_product_name"] = "그린티히알루론산 로션"
    return product


def _set_hya_universe(state: DemoState) -> None:
    state.serving_products = [
        _product("P_struct", "수분크림", [_HYA_S]),  # structured carrier
        _hya_name_product("P_name"),                 # name-only carrier
        _product("P_plain", "수분크림", []),          # no hyaluron
    ]


def test_ask_search_mode_ingredient_hard_gate_live(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, state = ask_env  # GRAPHRAPPING_QUERY_LLM unset → real dictionary fallback
    _set_hya_universe(state)

    payload = client.post("/api/ask", json={"query": _MARQUEE}).json()
    assert payload["resolved_mode"] == "search"
    assert payload["interpretation"]["llm_used"] is False  # live fallback built the constraint

    ids = {r["product_id"] for r in payload["results"]}
    assert ids == {"P_struct", "P_name"}  # both carriers; non-carrier hard-gated out

    meta = payload["ingredient_filter"]
    assert meta["applied"] is True
    assert meta["labels"] == ["히알루론"]
    assert meta["matched_products"] == 2
    assert meta["relaxed"] is False
    # The name-only carrier earns the product_name overlap axis (PRODUCT_MASTER_TRUTH).
    name_res = next(r for r in payload["results"] if r["product_id"] == "P_name")
    assert "product_name:히알루론" in name_res["overlap_concepts"]
    assert "PRODUCT_MASTER_TRUTH" in name_res["eligibility"]["evidence_families"]


def test_ask_recommend_mode_ingredient_hard_gate_live(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    client, state = ask_env
    _set_hya_universe(state)

    payload = client.post("/api/ask", json={"user_id": "U1", "query": _MARQUEE}).json()
    assert payload["resolved_mode"] == "recommend"
    assert payload["ingredient_filter"]["applied"] is True
    assert payload["ingredient_filter"]["labels"] == ["히알루론"]
    assert payload["ingredient_filter"]["relaxed"] is False

    ids = {r["product_id"] for r in payload["results"]}
    assert ids  # results exist
    assert "P_plain" not in ids  # non-carrier hard-gated out of the candidate universe
    assert ids <= {"P_struct", "P_name"}  # every result is a hyaluron carrier


def test_ask_ingredient_llm_only_not_hard_filtered(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM-only family (adopted via ingredients_wanted, no raw surface) is
    provenance="llm" → NOT hard-filtered. The non-carrier P_plain (matching on
    goal/keyword) stays in the results, and ingredient_filter.applied is False."""
    client, state = ask_env
    _set_hya_universe(state)
    _patch_understanding(
        monkeypatch,
        _fake_interp(
            "보습 크림 추천",
            concepts=[
                MatchedConcept("goal", "보습", "보습", "보습"),
                MatchedConcept("keyword", "kw_moisturizing", "보습", "보습"),
            ],
            ingredient_constraints=[
                IngredientConstraint("히알루론", [_HYA_S], ["히알루론"], "llm"),
            ],
        ),
    )
    payload = client.post("/api/ask", json={"query": "보습 크림 추천"}).json()
    assert payload["ingredient_filter"]["applied"] is False  # llm provenance → no gate
    ids = {r["product_id"] for r in payload["results"]}
    assert "P_plain" in ids  # not dropped by an ingredient gate (matches goal/keyword)


def test_ask_recommend_mode_ingredient_relax_when_no_carrier(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 carriers in the category universe → relax the INGREDIENT condition only
    (keep category/other), returning broadened results with relaxed=True + reason."""
    client, state = ask_env
    # No product carries hyaluron (structured or by name).
    state.serving_products = [_product("P_plain", "수분크림", []), _lipstick("P_lip")]
    _patch_understanding(
        monkeypatch,
        _fake_interp(
            "히알루론 크림",
            concepts=[
                MatchedConcept("goal", "보습", "보습", "보습"),
                MatchedConcept("category", "concept:Category:skincare", "크림", "스킨케어"),
            ],
            ingredient_constraints=[
                IngredientConstraint("히알루론", [_HYA_S], ["히알루론"], "raw"),
            ],
        ),
    )
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "히알루론 크림"}).json()
    assert payload["resolved_mode"] == "recommend"
    assert payload["relaxed"] is True  # top-level relaxed reflects the ingredient relax
    meta = payload["ingredient_filter"]
    assert meta["applied"] is False and meta["relaxed"] is True
    assert meta["labels"] == ["히알루론"] and meta["matched_products"] == 0
    assert meta["reason"]  # user-facing "성분 조건을 완화" notice
    assert payload["results"], "relax must broaden to the category universe, not return nothing"


def test_ask_ingredient_query_does_not_perturb_no_query_recommend(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    """[C1 byte-identity] Running an ingredient ask (which builds request-scoped
    constraints + name-label maps) must not mutate shared state: a no-query
    /api/recommend is bit-identical before and after."""
    client, state = ask_env
    _set_hya_universe(state)
    body = {"user_id": "U1", "category_group": "all", "top_k": 10}

    baseline = client.post("/api/recommend", json=body)
    assert baseline.status_code == 200

    ask_resp = client.post("/api/ask", json={"user_id": "U1", "query": _MARQUEE}).json()
    assert ask_resp["ingredient_filter"]["applied"] is True  # the gate actually ran

    after = client.post("/api/recommend", json=body)
    assert after.status_code == 200
    # Raw-bytes byte-identity (F8): stricter than a parsed-JSON compare.
    assert after.content == baseline.content  # no request-scoped state leaked


def test_search_endpoint_equivalent_to_anonymous_ask(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    """[unification] /api/search returns the identical anonymous /api/ask payload
    for the same (query, top_k) — the two entry points share one flow (plan §B2 v3)."""
    client, _state = ask_env
    search_payload = client.get("/api/search", params={"query": "보습 크림", "top_k": 10}).json()
    ask_payload = client.post("/api/ask", json={"query": "보습 크림", "top_k": 10}).json()
    assert search_payload == ask_payload


def test_ask_anonymous_payload_carries_ingredient_filter_and_message(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    """The anonymous payload now carries ingredient_filter + the message rule
    (unified with /api/search); a no-ingredient query reports applied=False."""
    client, _state = ask_env
    payload = client.post("/api/ask", json={"query": "보습 크림 추천해줘"}).json()
    assert payload["message"] is None  # resolved → no no-concept guidance
    assert payload["ingredient_filter"] == {
        "applied": False, "labels": [], "matched_products": 0,
        "relaxed": False, "reason": None,
    }


def test_ask_search_mode_ingredient_gate_honours_category_universe(
    ask_env: tuple[TestClient, DemoState],
) -> None:
    """[F4] With a category in the query, the anonymous ingredient gate is scoped to
    that category group (login parity) — a makeup hyaluron carrier must not surface
    for "히알루론 수분크림"."""
    client, state = ask_env
    state.serving_products = [
        _product("SK", "수분크림", [_HYA_S]),   # skincare hyaluron carrier
        _product("LIP", "립스틱", [_HYA_S]),     # makeup hyaluron carrier
    ]
    payload = client.post("/api/ask", json={"query": "히알루론 수분크림"}).json()
    assert payload["category_group"] == "skincare"
    ids = {r["product_id"] for r in payload["results"]}
    assert ids == {"SK"}  # the makeup carrier is excluded by the category universe
    assert payload["ingredient_filter"]["matched_products"] == 1


def test_ask_search_mode_relax_when_all_carriers_are_avoided(
    ask_env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[F5] Carriers exist but ALL also carry an avoided ingredient → matched is
    counted AFTER avoided exclusion, so the filter reports applied=false + relaxed=true
    (never applied=true with 0 results — the pre-fix inconsistency)."""
    client, state = ask_env
    retinol = "concept:Ingredient:레티놀"
    # The lone hyaluron carrier ALSO carries retinol (the avoided ingredient).
    state.serving_products = [_product("P_bad", "수분크림", [_HYA_S, retinol])]
    _patch_understanding(
        monkeypatch,
        _fake_interp(
            "히알루론 있고 레티놀 없는",
            concepts=[MatchedConcept("goal", "보습", "보습", "보습")],
            avoided=[retinol],
            ingredient_constraints=[IngredientConstraint("히알루론", [_HYA_S], ["히알루론"], "raw")],
        ),
    )
    meta = client.post("/api/ask", json={"query": "히알루론 있고 레티놀 없는"}).json()["ingredient_filter"]
    assert meta["matched_products"] == 0  # the only carrier is avoided-excluded
    assert meta["relaxed"] is True and meta["applied"] is False
    assert meta["reason"]
