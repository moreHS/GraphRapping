"""Search-absorption A3 + A4 tests.

Plan: fable_doc/plans/2026-07-23_search_absorption.md §A3, §A4.

A3 — ingredient strength (required/preferred):
- the hard gate fires ONLY for provenance=="raw" AND strength=="required";
- a "preferred" family ("있으면 더 좋고") never hard-gates, is surfaced under
  ``ingredient_preferences``, and keeps ``ingredient_filter.applied=False``;
- the documented anonymous preferred-only degeneracy (overlap≥1 → carriers only).

A4 — evidence-state transparency (aggregation only; pass/exclude verdict UNCHANGED):
- the 3-state matcher (matched / unmatched_in_available_evidence / no_evidence);
- ``ingredient_filter.evidence_unknown_products`` over the gate denominator
  (avoided-removed universe), only when the filter is applied.

The strength CLASSIFICATION (slot→strength threading) is covered in
test_query_understanding.py; here we assert the server WIRING + the pure matcher.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.rec.ingredient_constraint import (
    IngredientConstraint,
    count_evidence_unknown_products,
    ingredient_evidence_state,
)
from src.rec.query_understanding import QueryInterpretation
from src.rec.search import MatchedConcept
from src.web import server
from src.web.state import DemoState

_HYA = "concept:Ingredient:소듐하이알루로네이트"
_RET = "concept:Ingredient:레티놀"


# ===========================================================================
# A4 — pure matcher: 3-state evidence + denominator aggregation
# ===========================================================================


def _p(pid: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "representative_product_name": "",
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
    }
    base.update(overrides)
    return base


def _hya_constraint(strength: str = "required") -> IngredientConstraint:
    return IngredientConstraint(
        label="히알루론",
        inci_concept_ids=[_HYA],
        name_surfaces=["히알루론", "소듐하이알루로네이트"],
        provenance="raw",
        strength=strength,
    )


def test_a4_evidence_matched_structured() -> None:
    """Structured carrier → matched."""
    con = _hya_constraint()
    assert ingredient_evidence_state(_p("A", ingredient_concept_ids=[_HYA]), con) == "matched"


def test_a4_evidence_matched_raw_only() -> None:
    """Raw ingredient_ids master string (no concept id) still matches (suffix fold)."""
    con = _hya_constraint()
    p = _p("A", ingredient_ids=["소듐하이알루로네이트"])
    assert ingredient_evidence_state(p, con) == "matched"


def test_a4_evidence_matched_name_only() -> None:
    """Name-only carrier (no structured field) → matched (the name axis)."""
    con = _hya_constraint()
    p = _p("A", representative_product_name="히알루론 수분크림")
    assert ingredient_evidence_state(p, con) == "matched"


def test_a4_evidence_unmatched_has_other_ingredient() -> None:
    """Has an ingredient list, but not X → unmatched_in_available_evidence (NOT proof
    of absence — the list is partial)."""
    con = _hya_constraint()
    p = _p("A", ingredient_concept_ids=[_RET])
    assert ingredient_evidence_state(p, con) == "unmatched_in_available_evidence"


def test_a4_evidence_none_when_empty() -> None:
    """No structured/raw ingredient field AND no name match → no_evidence (확인 불가)."""
    con = _hya_constraint()
    p = _p("A", representative_product_name="그냥 크림")
    assert ingredient_evidence_state(p, con) == "no_evidence"


def test_a4_evidence_xfree_name_guarded_is_no_evidence() -> None:
    """An X-free name ("히알루론프리") is guarded to non-match; with no structured
    ingredient field it is no_evidence (we still have no ingredient list to reason
    over — the name is not an ingredient list)."""
    con = _hya_constraint()
    p = _p("A", representative_product_name="히알루론프리 크림")
    assert ingredient_evidence_state(p, con) == "no_evidence"


def test_a4_count_multi_family_min_one_no_evidence() -> None:
    """Multi-family aggregation: a gate-eliminated product counts iff AT LEAST ONE
    required family is no_evidence for it. Denominator products that PASS every
    family are never counted."""
    hya = _hya_constraint()
    ret = IngredientConstraint("레티놀", [_RET], ["레티놀"], "raw", "required")
    products = [
        _p("PASS", ingredient_concept_ids=[_HYA, _RET]),  # passes both → not counted
        _p("NO_EV", representative_product_name="무성분 크림"),  # both no_evidence → counted
        _p("UNMATCHED", ingredient_concept_ids=[_HYA]),  # has hya, ret unmatched (has list) → NOT counted
        _p("MIXED", ingredient_concept_ids=[_RET]),  # ret matches, hya unmatched (has list) → NOT counted
    ]
    # NO_EV is the only eliminated product with a no_evidence family.
    assert count_evidence_unknown_products(products, [hya, ret]) == 1


def test_a4_count_empty_constraints_is_zero() -> None:
    assert count_evidence_unknown_products([_p("A")], []) == 0


@pytest.mark.parametrize("blank", [[""], ["  "], ["", "  "]])
def test_a4_evidence_blank_ingredient_field_is_no_evidence(blank: list[str]) -> None:
    """[F2] A placeholder/blank ingredient field ([""], ["  "]) is NOT evidence — it
    would never match, so it cannot prove absence either → no_evidence (was
    mis-classified as unmatched by list-truthiness)."""
    con = _hya_constraint()
    assert ingredient_evidence_state(_p("A", ingredient_ids=blank), con) == "no_evidence"
    assert (
        ingredient_evidence_state(_p("B", ingredient_concept_ids=blank), con)
        == "no_evidence"
    )


# ===========================================================================
# A3 + A4 — server e2e (demo mode)
# ===========================================================================


def _prod(pid: str, name: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "representative_product_name": name,
        "brand_name": "브랜드",
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:브랜드"],
        "category_name": "크림",
        "category_id": None,
        "category_concept_ids": ["concept:Category:크림"],
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [{"id": "kw_moist", "score": 0.9}],
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


def _a34_products() -> list[dict[str, Any]]:
    return [
        _prod("CARRIER", "보습 크림", ingredient_concept_ids=[_HYA]),  # structured carrier
        _prod("OTHER_ING", "촉촉 크림", ingredient_concept_ids=[_RET]),  # has list, no hya → unmatched
        _prod("NO_EV", "수분 크림"),  # no ingredient field, name no-match → no_evidence
    ]


def _user(uid: str = "U1") -> dict[str, Any]:
    return {"user_id": uid, "scoped_preference_ids": [
        {"edge_type": "PREFERS_KEYWORD", "id": "kw_moist",
         "weight": 0.8, "scope_group": None, "source_sections": ["chat.keyword"]},
    ]}


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, DemoState]:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = _a34_products()
    state.serving_users = [_user("U1")]
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app), state


def _interp(constraints: list[IngredientConstraint], **overrides: Any) -> QueryInterpretation:
    concepts = [MatchedConcept("keyword", "kw_moist", "보습", "보습")]
    # A preferred/required ingredient family also carries its concept in resolved_concepts
    # (that is what drives the search overlap + PREFERS_INGREDIENT boost).
    for c in constraints:
        concepts.append(MatchedConcept("ingredient", c.inci_concept_ids[0], c.label, c.label))
    base = dict(
        query="q", intent="search", resolved_concepts=concepts,
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
        ingredient_constraints=constraints,
    )
    base.update(overrides)
    return QueryInterpretation(**base)


# --- A3 preferred: no hard gate, surfaced as a preference -------------------


def test_a3_preferred_not_hard_filtered_login(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw+preferred family does NOT hard-gate: non-carriers survive, applied=False,
    the label is surfaced under ingredient_preferences, and evidence_unknown=0."""
    client, _ = env
    interp = _interp([_hya_constraint("preferred")], intent="recommend")
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "히알루론 있으면 좋고 보습 크림"}).json()
    meta = payload["ingredient_filter"]
    assert meta["applied"] is False
    assert meta["evidence_unknown_products"] == 0  # not applied → forced 0
    assert payload["ingredient_preferences"] == ["히알루론"]
    # Non-carriers are NOT hard-filtered out (soft preference only).
    result_ids = {r["product_id"] for r in payload["results"]}
    assert {"OTHER_ING", "NO_EV"} & result_ids


def test_a3_preferred_not_hard_filtered_anonymous(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous preferred family → applied=False + ingredient_preferences surfaced."""
    client, _ = env
    interp = _interp([_hya_constraint("preferred")])
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"query": "히알루론 있으면 좋고 보습 크림"}).json()
    assert payload["ingredient_filter"]["applied"] is False
    assert payload["ingredient_filter"]["evidence_unknown_products"] == 0
    assert payload["ingredient_preferences"] == ["히알루론"]


def test_a3_anonymous_preferred_only_degenerate_documented(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented degeneracy: a preferred-only anonymous query (the preferred
    ingredient is the only positive overlap axis) structurally returns only
    carriers, yet is surfaced as a PREFERENCE (applied=False, ingredient_preferences
    populated) — never a hard filter."""
    client, _ = env
    # Only the preferred ingredient concept resolves (no keyword) → overlap axis = hya.
    interp = QueryInterpretation(
        query="q", intent="search",
        resolved_concepts=[MatchedConcept("ingredient", _HYA, "히알루론", "히알루론")],
        avoided_ingredient_concept_ids=[], unresolved_terms=[], llm_used=True,
        ingredient_constraints=[_hya_constraint("preferred")],
    )
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"query": "히알루론 있으면 좋겠어"}).json()
    assert payload["ingredient_filter"]["applied"] is False
    assert payload["ingredient_preferences"] == ["히알루론"]
    # Structurally, only the carrier overlaps → the returned set is carriers.
    result_ids = {r["product_id"] for r in payload["results"]}
    assert result_ids <= {"CARRIER"}


# --- A3 required regression + A4 evidence_unknown ---------------------------


def test_a3_required_still_hard_filters_with_unknown_count_login(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw+required family still hard-gates (regression): only the carrier survives,
    applied=True. [A4] the no-evidence product (NO_EV) is counted in
    evidence_unknown_products; the has-a-list-but-unmatched product (OTHER_ING) is
    NOT (its absence is 'proven' by an existing list, so it is not 'unknown')."""
    client, _ = env
    interp = _interp([_hya_constraint("required")], intent="recommend")
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "히알루론 든 보습 크림"}).json()
    meta = payload["ingredient_filter"]
    assert meta["applied"] is True
    assert meta["matched_products"] == 1  # only CARRIER
    assert meta["evidence_unknown_products"] == 1  # only NO_EV
    result_ids = {r["product_id"] for r in payload["results"]}
    assert result_ids == {"CARRIER"}
    assert payload["ingredient_preferences"] == []


def test_a4_required_unknown_count_anonymous(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same required gate + evidence_unknown count on the anonymous path."""
    client, _ = env
    interp = _interp([_hya_constraint("required")])
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"query": "히알루론 든 보습 크림"}).json()
    meta = payload["ingredient_filter"]
    assert meta["applied"] is True
    assert meta["matched_products"] == 1
    assert meta["evidence_unknown_products"] == 1


def test_a4_relaxed_gate_forces_zero_unknown(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the required gate empties the universe → relaxed, evidence_unknown is
    forced to 0 (meaningful only while the filter is applied)."""
    client, state = env
    # Strip the only carrier's hyaluron so NOTHING carries the family → relax.
    state.serving_products[0]["ingredient_concept_ids"] = []
    interp = _interp([_hya_constraint("required")], intent="recommend")
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "히알루론 든 보습 크림"}).json()
    meta = payload["ingredient_filter"]
    assert meta["relaxed"] is True and meta["applied"] is False
    assert meta["evidence_unknown_products"] == 0


def test_a4_no_ingredient_filter_zero_unknown(
    env: tuple[TestClient, DemoState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ingredient family at all → applied=False, evidence_unknown=0, and the meta
    field is always present (additive contract)."""
    client, _ = env
    interp = _interp([], intent="recommend")
    monkeypatch.setattr(server, "understand_query", lambda _q, _p: interp)
    payload = client.post("/api/ask", json={"user_id": "U1", "query": "보습 크림"}).json()
    meta = payload["ingredient_filter"]
    assert meta["applied"] is False
    assert meta["evidence_unknown_products"] == 0
    assert payload["ingredient_preferences"] == []
