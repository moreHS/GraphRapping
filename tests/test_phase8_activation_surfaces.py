"""Phase 8 P8-2: activation hook + G2 graph edges + G3 similar-products API.

Covers the *surfaces* built on top of the P8-1 similarity module:
- ``build_and_attach_similarity`` (the shared DB/demo activation hook): the
  item-to-item category gate is ON, neighbours are symmetric, evidence is
  labelled, and the write is additive (no existing profile field is touched, so
  a recommendation ranking cannot move).
- the demo adapter chain (``product_signals`` -> keyword triples -> attach).
- G2 ``_build_corpus_graph``: SHARES_ATTRIBUTE edges carry ``shared_axes``, are
  capped per anchor, and are emitted once per pair.
- G3 ``GET /api/products/{id}/similar``: 200 with evidence, empty-array 200, 404.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.rec.product_similarity import keyword_signals_from_product_signals
from src.web import server
from src.web.serving_store import build_and_attach_similarity
from src.web.state import DemoState


# ---------------------------------------------------------------------------
# build_and_attach_similarity — the shared activation hook
# ---------------------------------------------------------------------------


def test_hook_category_gate_on_filters_cross_category_and_is_symmetric():
    # rare is shared by A,B (skincare) and C (makeup); D (skincare) breaks the
    # df==N tie so rare is discriminative (df=3 of N=4 -> IDF>0). With the
    # item-to-item gate ON, the cross-category pair A-C / B-C is dropped even
    # though it shares a discriminative node; A-B survive.
    products = [
        {"product_id": "A", "category_name": "토너", "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "B", "category_name": "에센스", "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "C", "category_name": "립스틱", "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "D", "category_name": "세럼", "ingredient_concept_ids": ["concept:Ingredient:other"]},
    ]
    build_and_attach_similarity(products, {})  # category_gate=True by default
    by_id = {p["product_id"]: p for p in products}

    assert [s["product_id"] for s in by_id["A"]["similar_product_ids"]] == ["B"]
    assert [s["product_id"] for s in by_id["B"]["similar_product_ids"]] == ["A"]
    # C (makeup) shares rare only across the gate -> no in-group neighbour.
    assert by_id["C"]["similar_product_ids"] == []
    assert by_id["D"]["similar_product_ids"] == []


def test_hook_is_additive_only_similar_product_ids_added():
    # A ranking-relevant field must survive untouched; the sole new key is the
    # ephemeral similar_product_ids (the safety contract that keeps rankings
    # invariant when the hook activates).
    products = [
        {"product_id": "A", "category_name": "토너", "top_keyword_ids": [{"id": "kw", "score": 3}],
         "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "B", "category_name": "에센스", "ingredient_concept_ids": ["concept:Ingredient:rare"]},
        {"product_id": "C", "category_name": "세럼", "ingredient_concept_ids": ["concept:Ingredient:other"]},
    ]
    before = [dict(p) for p in products]
    build_and_attach_similarity(products, {})
    for after, original in zip(products, before):
        assert set(after) - set(original) == {"similar_product_ids"}
        for key, value in original.items():
            assert after[key] == value  # every pre-existing field unchanged


def test_hook_demo_adapter_chain_labels_keyword_axis():
    # Demo path: product_signals -> keyword triples -> attach. A,B share the
    # canonical keyword (df=2 of N=3 -> IDF>0); the shared_axes keyword label is
    # resolved from keyword_surface_map.yaml (not the raw id).
    products = [
        {"product_id": "A", "category_name": "토너"},
        {"product_id": "B", "category_name": "에센스"},
        {"product_id": "C", "category_name": "세럼"},
    ]
    product_signals = {
        "A": [{"keyword_id": "concept:Keyword:kw_moisturizing", "bee_attr_id": "concept:BEEAttr:be", "polarity": "NEU"}],
        "B": [{"keyword_id": "concept:Keyword:kw_moisturizing", "bee_attr_id": "concept:BEEAttr:be", "polarity": "NEU"}],
    }
    build_and_attach_similarity(products, keyword_signals_from_product_signals(product_signals))
    a_sims = products[0]["similar_product_ids"]
    assert [s["product_id"] for s in a_sims] == ["B"]
    kw_axis = a_sims[0]["shared_axes"][0]
    assert kw_axis["axis"] == "keyword"
    assert kw_axis["label"] == "보습좋음"
    assert products[2]["similar_product_ids"] == []


# ---------------------------------------------------------------------------
# A2 — brand-only shared neighbours dropped from the gated (G2/G3) surface
# (DECISIONS/2026-07-18_phase8_brand_only_neighbor_policy.md). The ungated G4/G5
# sidecar keeps them (boost is score-only and unfiltered).
# ---------------------------------------------------------------------------


def _brand_policy_products() -> list[dict]:
    # All same category group (토너 -> skincare) so the item-to-item gate passes;
    # category_concept_ids empty so category is NOT a shared node. The only shared
    # nodes are brand + ingredient.
    #   P1,P2: share brand 헤라 + ingredient shared_ing  (brand+ingredient -> KEEP)
    #   P1,P3 / P2,P3: share brand 헤라 ALONE             (brand-only      -> DROP)
    #   P3,P4: share ingredient other_ing ALONE          (ingredient-only -> KEEP)
    #   P4: brand 설화수 breaks df==N so brand 헤라 stays discriminative (IDF > 0).
    def _p(pid: str, brand: str, ing: str) -> dict:
        return {
            "product_id": pid, "category_name": "토너", "category_concept_ids": [],
            "brand_concept_ids": [f"concept:Brand:{brand}"],
            "ingredient_concept_ids": [f"concept:Ingredient:{ing}"],
        }
    return [
        _p("P1", "헤라", "shared_ing"),
        _p("P2", "헤라", "shared_ing"),
        _p("P3", "헤라", "other_ing"),
        _p("P4", "설화수", "other_ing"),
    ]


def test_brand_only_neighbour_dropped_from_gated_surface_but_kept_ungated():
    products = _brand_policy_products()
    ungated = build_and_attach_similarity(products, {}, include_ungated=True)
    by_id = {p["product_id"]: p for p in products}

    def neigh(pid: str) -> set:
        return {s["product_id"] for s in by_id[pid]["similar_product_ids"]}

    # Gated surface: the brand-only pair (P1/P2 <-> P3) is gone, two-directionally;
    # the brand+ingredient pair (P1<->P2) and the ingredient-only pair (P3<->P4) stay.
    assert neigh("P1") == {"P2"}
    assert neigh("P2") == {"P1"}
    assert neigh("P3") == {"P4"}  # P1/P2 dropped (brand-only), P4 kept (ingredient)
    assert neigh("P4") == {"P3"}

    # The dropped edge really was brand-only: it survives UNFILTERED in the
    # ungated sidecar, sharing exactly the brand axis — that is why the gated
    # surface drops it while the boost channel keeps it.
    assert ungated is not None
    ungated_p1 = {s.product_id: s for s in ungated["P1"]}
    assert "P3" in ungated_p1
    assert {ax["axis"] for ax in ungated_p1["P3"].shared_axes} == {"brand"}
    # The kept gated edge P1<->P2 shares more than brand (brand + ingredient).
    assert {ax["axis"] for ax in ungated_p1["P2"].shared_axes} == {"brand", "ingredient"}


def test_brand_only_policy_leaves_ungated_g3_g4_neighbour_counts_intact():
    # The ungated sidecar (feeds G4 boost / G5 related) is NOT filtered: every
    # neighbour — including brand-only — is retained.
    products = _brand_policy_products()
    ungated = build_and_attach_similarity(products, {}, include_ungated=True)
    assert ungated is not None
    # P3 keeps all three ungated neighbours (P1, P2 brand-only + P4 ingredient),
    # whereas its gated surface kept only P4.
    assert {s.product_id for s in ungated["P3"]} == {"P1", "P2", "P4"}


# ---------------------------------------------------------------------------
# G2 — _build_corpus_graph similarity edges
# ---------------------------------------------------------------------------


def _sim(pid: str, score: float) -> dict:
    return {
        "product_id": pid,
        "neighbor_name": f"name-{pid}",
        "score": score,
        "shared_axes": [{"axis": "keyword", "node_key": f"keyword::be:{pid}:", "label": "보습좋음", "idf": 2.1}],
    }


def test_g2_corpus_graph_emits_capped_shares_attribute_edges_with_evidence():
    # 5 score-sorted neighbours; the graph caps to the top 3 (the widget keeps all).
    profile = {
        "product_id": "P1",
        "similar_product_ids": [_sim("N1", 5.0), _sim("N2", 4.0), _sim("N3", 3.0), _sim("N4", 2.0), _sim("N5", 1.0)],
    }
    nodes_map: dict = {"P1": {"id": "P1", "label": "P1", "type": "product", "main": True}}
    edges: list = []
    server._build_corpus_graph(profile, "P1", nodes_map, edges)

    sim_edges = [e for e in edges if e["label"] == "SHARES_ATTRIBUTE"]
    assert len(sim_edges) == server._SIMILAR_GRAPH_CAP == 3
    # Top-3 by score, each carrying its shared_axes evidence + score.
    assert [e["target"] for e in sim_edges] == ["N1", "N2", "N3"]
    for edge in sim_edges:
        assert edge["source"] == "P1"
        assert edge["shared_axes"] and edge["shared_axes"][0]["label"] == "보습좋음"
        assert isinstance(edge["score"], float)
    # Neighbour product nodes were added with their embedded names.
    assert nodes_map["N1"]["type"] == "product"
    assert nodes_map["N1"]["label"] == "name-N1"


def test_g2_no_similar_ids_emits_no_similarity_edges():
    profile = {"product_id": "P1"}  # activation hook never ran -> field absent
    nodes_map: dict = {"P1": {"id": "P1", "label": "P1", "type": "product", "main": True}}
    edges: list = []
    server._build_corpus_graph(profile, "P1", nodes_map, edges)
    assert not [e for e in edges if e["label"] == "SHARES_ATTRIBUTE"]


def test_g2_self_reference_skipped_and_pair_emitted_once():
    profile = {"product_id": "P1", "similar_product_ids": [_sim("P1", 9.0), _sim("N2", 4.0)]}
    nodes_map: dict = {"P1": {"id": "P1", "label": "P1", "type": "product", "main": True}}
    edges: list = []
    server._build_corpus_graph(profile, "P1", nodes_map, edges)
    sim_edges = [e for e in edges if e["label"] == "SHARES_ATTRIBUTE"]
    # self-edge dropped; the one real neighbour appears exactly once (undirected).
    assert [e["target"] for e in sim_edges] == ["N2"]


# ---------------------------------------------------------------------------
# G3 — GET /api/products/{id}/similar (TestClient e2e)
# ---------------------------------------------------------------------------


def _demo_client(monkeypatch, products: list[dict]) -> TestClient:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = products
    monkeypatch.setattr(server, "demo_state", state)
    return TestClient(server.app)


def test_g3_returns_neighbours_with_evidence(monkeypatch):
    client = _demo_client(monkeypatch, [
        {"product_id": "P1", "similar_product_ids": [_sim("P2", 3.5)]},
        {"product_id": "P2", "similar_product_ids": []},
    ])
    resp = client.get("/api/products/P1/similar")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product_id"] == "P1"
    assert body["total"] == 1
    item = body["items"][0]
    assert item["product_id"] == "P2"
    assert item["shared_axes"][0]["label"] == "보습좋음"


def test_g3_known_product_no_neighbours_is_empty_200(monkeypatch):
    client = _demo_client(monkeypatch, [{"product_id": "P2", "similar_product_ids": []}])
    resp = client.get("/api/products/P2/similar")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_g3_unknown_product_is_404(monkeypatch):
    client = _demo_client(monkeypatch, [{"product_id": "P1", "similar_product_ids": []}])
    resp = client.get("/api/products/NOPE/similar")
    assert resp.status_code == 404
