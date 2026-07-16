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
