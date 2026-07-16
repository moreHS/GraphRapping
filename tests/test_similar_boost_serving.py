"""Phase 8 P8-3a (G4): ungated similarity sidecar + serving wiring tests.

Plan: fable_doc/plans/2026-07-16_phase8-3_g4_similar_boost_g5_query_related.md §1.1/§1.4.
Covers the store side of the boost channel:
- ``build_and_attach_similarity(include_ungated=True)``: returns the ungated
  (category_gate=False) index computed on the same nodes/idf, while the gated
  attach and the P8-2 additive contract ("the only profile key added is
  ``similar_product_ids``") stay intact; default call still returns None.
- ``DemoServingStore.get_ungated_similar`` / ``DBServingStore.get_ungated_similar``
  accessors (copies, empty-miss, cross-category ungated vs gated).
- server e2e (demo mode): /api/recommend fires the boost for an owned anchor —
  `similar:` overlap in the payload, similar_product_affinity contribution, and
  the `similar` explanation path carrying sidecar ``shared_axes`` — and stays
  dormant (no similar path) when the sidecar is empty.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.rec.product_similarity import SimilarProductSignal
from src.web import server
from src.web.serving_store import (
    DBServingStore,
    DemoServingStore,
    build_and_attach_similarity,
)
from src.web.state import DemoState


# ---------------------------------------------------------------------------
# build_and_attach_similarity — include_ungated
# ---------------------------------------------------------------------------


def _cross_category_products() -> list[dict[str, Any]]:
    # A (skincare) and B (makeup) share a discriminative ingredient; C breaks
    # the df==N tie so the node keeps IDF > 0.
    return [
        {"product_id": "A", "category_name": "토너",
         "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "B", "category_name": "립스틱",
         "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "C", "category_name": "세럼",
         "ingredient_concept_ids": ["concept:Ingredient:other"]},
    ]


def test_hook_default_call_returns_none_and_stays_p8_2_compatible():
    products = _cross_category_products()
    assert build_and_attach_similarity(products, {}) is None
    # Gated attach still ran (the P8-2 behaviour): cross-category pair dropped.
    assert products[0]["similar_product_ids"] == []


def test_hook_include_ungated_returns_cross_category_index_without_new_profile_keys():
    products = _cross_category_products()
    before_keys = [set(p) for p in products]
    ungated = build_and_attach_similarity(products, {}, include_ungated=True)

    assert ungated is not None
    # Ungated index: the cross-category pair fires, both directions present.
    assert [s.product_id for s in ungated["A"]] == ["B"]
    assert [s.product_id for s in ungated["B"]] == ["A"]
    assert ungated["A"][0].shared_axes  # evidence-first: axes always carried
    # Gated attach unchanged: same-category-group neighbours only (none here).
    assert all(p["similar_product_ids"] == [] for p in products)
    # P8-2 additive contract holds WITH the new flag: the only key the hook
    # adds to a profile is similar_product_ids — the sidecar is returned, not
    # attached.
    for product, before in zip(products, before_keys):
        assert set(product) - before == {"similar_product_ids"}


# ---------------------------------------------------------------------------
# DemoServingStore accessor
# ---------------------------------------------------------------------------


def _signal(pid: str, score: float = 15.0) -> SimilarProductSignal:
    return SimilarProductSignal(
        product_id=pid,
        neighbor_name=f"name-{pid}",
        score=score,
        shared_axes=[{"axis": "ingredient", "node_key": "ingredient::rare",
                      "label": "레어성분", "idf": 1.1}],
    )


@pytest.mark.asyncio
async def test_demo_store_accessor_reads_state_sidecar_and_returns_copies():
    state = DemoState(loaded=True)
    state.similar_ungated = {"A": [_signal("B")]}
    store = DemoServingStore(lambda: state)

    got = await store.get_ungated_similar("A")
    assert [s.product_id for s in got] == ["B"]
    got.append("mutation")  # type: ignore[arg-type]
    assert len(await store.get_ungated_similar("A")) == 1  # copy, not the state list
    assert await store.get_ungated_similar("NOPE") == []


# ---------------------------------------------------------------------------
# DBServingStore accessor (fake pool — mirrors test_serving_store_db shapes)
# ---------------------------------------------------------------------------


class _FakeAcquireCtx:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeConn":
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


class _FakeConn:
    def __init__(self) -> None:
        self.products: list[dict[str, Any]] = []
        self.users: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []

    async def fetch(self, query: str, *_args: Any) -> list[dict[str, Any]]:
        if "serving_product_profile" in query:
            rows = self.products
        elif "serving_user_profile" in query:
            rows = self.users
        elif "wrapped_signal" in query:
            rows = self.signals
        else:
            raise AssertionError(f"unexpected fetch query: {query!r}")
        return [dict(row) for row in rows]


def _db_product_row(product_id: str, category_name: str, ingredient: str) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "representative_product_name": f"name-{product_id}",
        "category_name": category_name,
        "ingredient_concept_ids": json.dumps([f"concept:Ingredient:{ingredient}"]),
        "brand_concept_ids": json.dumps([]),
        "category_concept_ids": json.dumps([]),
        "main_benefit_concept_ids": json.dumps([]),
        "top_keyword_ids": json.dumps([]),
    }


@pytest.mark.asyncio
async def test_db_store_refresh_builds_ungated_sidecar_alongside_gated_attach():
    conn = _FakeConn()
    conn.products = [
        _db_product_row("A", "토너", "rare"),
        _db_product_row("B", "립스틱", "rare"),
        _db_product_row("C", "세럼", "other"),
    ]
    store = DBServingStore(_FakePool(conn), refresh_sec=300)

    # Ungated sidecar: the cross-category pair is available to the boost.
    got = await store.get_ungated_similar("A")
    assert [s.product_id for s in got] == ["B"]
    # Gated attach on the served profiles is unchanged (no cross-category
    # neighbour) — the two computations coexist on one refresh.
    products = {p["product_id"]: p for p in await store.get_products()}
    assert products["A"]["similar_product_ids"] == []
    # Copy semantics + miss behaviour.
    got.clear()
    assert len(await store.get_ungated_similar("A")) == 1
    assert await store.get_ungated_similar("NOPE") == []


# ---------------------------------------------------------------------------
# Server e2e (demo mode): boost fires through /api/recommend with shared_axes
# ---------------------------------------------------------------------------


def _serving_product(pid: str, category_name: str = "수분크림") -> dict[str, Any]:
    return {
        "product_id": pid,
        "brand_name": "헤라",
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:헤라"],
        "category_name": category_name,
        "category_id": None,
        "category_concept_ids": [],
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


def _client_with_state(monkeypatch: pytest.MonkeyPatch, state: DemoState) -> TestClient:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_CANDIDATE_PREFILTER", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    monkeypatch.setattr(server, "demo_state", state)

    async def _no_sidecar(product_ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _no_sidecar)
    return TestClient(server.app)


def _boost_state(*, with_sidecar: bool) -> DemoState:
    state = DemoState(loaded=True)
    state.serving_products = [
        _serving_product("P_anchor"),
        _serving_product("P_cand"),
    ]
    state.serving_users = [{
        "user_id": "U1",
        "preferred_brand_ids": [{"id": "concept:Brand:헤라"}],
        "owned_product_ids": [{"id": "product:P_anchor", "weight": 1.0}],
    }]
    if with_sidecar:
        state.similar_ungated = {"P_anchor": [_signal("P_cand", score=15.0)]}
    return state


def test_recommend_fires_similar_boost_with_shared_axes(monkeypatch: pytest.MonkeyPatch):
    client = _client_with_state(monkeypatch, _boost_state(with_sidecar=True))
    resp = client.post("/api/recommend", json={"user_id": "U1", "mode": "explore"})
    assert resp.status_code == 200
    results = {r["product_id"]: r for r in resp.json()["results"]}

    cand = results["P_cand"]
    assert "similar:P_anchor|strength=0.5" in cand["overlap_concepts"]
    assert cand["feature_contributions"]["similar_product_affinity"] == pytest.approx(0.01)
    similar_paths = [p for p in cand["explanation_paths"] if p["type"] == "similar"]
    assert len(similar_paths) == 1
    path = similar_paths[0]
    assert path["id"] == "P_anchor"
    assert path["user_edge"] == "OWNS_PRODUCT"
    assert path["product_edge"] == "SHARES_ATTRIBUTE"
    # §1.4 provenance: the sidecar shared_axes ride along the similar path.
    assert path["shared_axes"] == [{"axis": "ingredient", "node_key": "ingredient::rare",
                                    "label": "레어성분", "idf": 1.1}]
    # Boost-only: family surface unchanged.
    assert cand["eligibility"]["evidence_families"] == ["PRODUCT_MASTER_TRUTH"]
    # Non-similar paths carry no shared_axes key (additive only where fired).
    assert all("shared_axes" not in p for p in cand["explanation_paths"] if p["type"] != "similar")
    # The owned anchor itself must never be boosted.
    if "P_anchor" in results:
        assert not [
            c for c in results["P_anchor"]["overlap_concepts"] if c.startswith("similar:")
        ]


def test_recommend_stays_dormant_without_sidecar(monkeypatch: pytest.MonkeyPatch):
    client = _client_with_state(monkeypatch, _boost_state(with_sidecar=False))
    resp = client.post("/api/recommend", json={"user_id": "U1", "mode": "explore"})
    assert resp.status_code == 200
    for result in resp.json()["results"]:
        assert not [c for c in result["overlap_concepts"] if c.startswith("similar:")]
        assert "similar_product_affinity" not in result["feature_contributions"]
        assert not [p for p in result["explanation_paths"] if p["type"] == "similar"]
