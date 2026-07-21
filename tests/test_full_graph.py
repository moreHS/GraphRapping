"""F5 full graph (users + products + concepts) — server contract tests.

Covers the plan §F5 invariants:
- canonical concept node identity: a product edge and a user edge to the same
  concept land on ONE node (the "two islands" fix, codex #5);
- edge-family toggles + orphan-concept drop;
- min_strength applies ONLY to the score-bearing SHARES_ATTRIBUTE family (#7);
- deterministic max_nodes truncation (users+products first, then concepts by
  degree) with no dangling edges + truncation meta (#7);
- pseudonymous user nodes never leak profile fields;
- SHARES_ATTRIBUTE unordered-pair dedup keeping the max score;
- OWNS only to in-catalog products;
- the per-product / per-user graph endpoints are untouched (regression).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.web import server
from src.web.state import DemoState


def _products() -> list[dict]:
    return [
        {
            "product_id": "P1", "representative_product_name": "토너", "brand_name": "BrandA",
            "brand_concept_ids": ["concept:Brand:BrandA"],
            "category_concept_ids": ["concept:Category:토너"],
            "ingredient_concept_ids": ["concept:Ingredient:나이아신아마이드"],
            "main_benefit_concept_ids": ["concept:Goal:미백"],
            "top_bee_attr_ids": [{"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 2.0}],
            "top_keyword_ids": [{"id": "concept:Keyword:kw_mild", "score": 1.0}],
            "top_concern_pos_ids": [{"id": "concept:Concern:건조함", "score": 1.0}],
            "similar_product_ids": [{"product_id": "P2", "score": 5.0}],
        },
        {
            "product_id": "P2", "representative_product_name": "에센스", "brand_name": "BrandA",
            "brand_concept_ids": ["concept:Brand:BrandA"],
            # symmetric back-edge (max-score dedup) + one neighbour outside the catalog
            "similar_product_ids": [{"product_id": "P1", "score": 4.0}, {"product_id": "OUT", "score": 9.0}],
        },
        {
            "product_id": "P3", "representative_product_name": "크림", "brand_name": "BrandB",
            "brand_concept_ids": ["concept:Brand:BrandB"],
        },
    ]


def _users() -> list[dict]:
    return [
        {
            "user_id": "real_aaaa1111",
            # profile fields that MUST NOT leak onto the graph node payload
            "age_band": "30s", "gender": "F", "skin_type": "건성", "skin_tone": "봄웜",
            "scoped_preference_ids": [
                {"id": "concept:Brand:BrandA", "edge_type": "PREFERS_BRAND", "scope_group": None},
                {"id": "concept:Concern:건조함", "edge_type": "HAS_CONCERN", "scope_group": "face"},
                {"id": "product:P1", "edge_type": "OWNS_PRODUCT", "scope_group": None},
            ],
            "owned_product_ids": [{"id": "product:P1"}, {"id": "product:OUT"}],
        },
        {
            "user_id": "real_bbbb2222",
            "scoped_preference_ids": [
                {"id": "concept:Brand:BrandB", "edge_type": "REPURCHASES_BRAND", "scope_group": None},
            ],
            "owned_product_ids": [],
        },
    ]


def _build(**kw) -> dict:
    kw.setdefault("edge_types", set(server._FULL_EDGE_FAMILIES))
    kw.setdefault("min_strength", 0.0)
    kw.setdefault("max_nodes", server._FULL_GRAPH_MAX_NODES)
    return server._build_full_graph(_products(), _users(), **kw)


def _demo_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.serving_products = _products()
    state.serving_users = _users()
    monkeypatch.setattr(server, "demo_state", state)
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# node identity — the "two islands" proof
# ---------------------------------------------------------------------------

def test_full_graph_canonical_concept_unifies_product_and_user_no_two_islands():
    out = _build()
    ids = [n["id"] for n in out["nodes"]]
    for concept in ("concept:Brand:BrandA", "concept:Concern:건조함"):
        # exactly ONE canonical node for the concept
        assert ids.count(concept) == 1, f"{concept} must be a single canonical node"
        product_sources = {
            e["source"] for e in out["edges"]
            if e["target"] == concept and e["family"] == "product_concept"
        }
        user_sources = {
            e["source"] for e in out["edges"]
            if e["target"] == concept and e["family"] == "user_concept"
        }
        # BOTH a product edge and a user edge attach to the SAME node id
        assert product_sources, f"{concept} has no product edge"
        assert user_sources, f"{concept} has no user edge"
    # BrandA specifically: product P1 and user real_aaaa1111 share the node
    brand = "concept:Brand:BrandA"
    assert "P1" in {e["source"] for e in out["edges"] if e["target"] == brand and e["family"] == "product_concept"}
    assert "real_aaaa1111" in {e["source"] for e in out["edges"] if e["target"] == brand and e["family"] == "user_concept"}


def test_full_graph_node_types_and_edge_families_present():
    out = _build()
    node_types = {n["type"] for n in out["nodes"]}
    assert {"user", "product", "brand", "category", "ingredient", "goal",
            "bee_attr", "keyword", "concern"} <= node_types
    families = {e["family"] for e in out["edges"]}
    assert families == set(server._FULL_EDGE_FAMILIES)
    # concept node id IS the concept IRI (canonical join key)
    byid = {n["id"]: n for n in out["nodes"]}
    assert byid["concept:Brand:BrandA"]["type"] == "brand"
    assert byid["concept:Brand:BrandA"]["label"] == "BrandA"


# ---------------------------------------------------------------------------
# product node label — brand is NOT prefixed (dedup); brand rides the payload
# ---------------------------------------------------------------------------

def test_full_product_label_omits_brand_prefix():
    # Brand is carried on the node payload + a separate brand node, so the label
    # is the representative name alone (no "{brand} {name}" duplication).
    assert server._full_product_label(
        {"product_id": "P1", "representative_product_name": "토너", "brand_name": "BrandA"}
    ) == "토너"
    # A name that already begins with the brand is returned unchanged (no double).
    assert server._full_product_label(
        {"product_id": "P9", "representative_product_name": "BrandA 토너", "brand_name": "BrandA"}
    ) == "BrandA 토너"
    # No representative name -> product_id fallback.
    assert server._full_product_label({"product_id": "P0", "brand_name": "BrandA"}) == "P0"


def test_full_graph_product_nodes_carry_brand_without_prefixing_label():
    out = _build()
    byid = {n["id"]: n for n in out["nodes"]}
    p1 = byid["P1"]
    assert p1["label"] == "토너"       # brand NOT prefixed onto the visible label
    assert p1["brand"] == "BrandA"      # brand rides the payload (for the hover tooltip)
    assert byid["P3"]["brand"] == "BrandB"


def test_full_graph_product_node_omits_brand_key_when_absent():
    out = server._build_full_graph(
        [{"product_id": "PX", "representative_product_name": "무브랜드"}],
        [],
        edge_types=set(server._FULL_EDGE_FAMILIES),
        min_strength=0.0,
        max_nodes=server._FULL_GRAPH_MAX_NODES,
    )
    px = {n["id"]: n for n in out["nodes"]}["PX"]
    assert "brand" not in px
    assert px["label"] == "무브랜드"


# ---------------------------------------------------------------------------
# privacy — pseudonymous user nodes only
# ---------------------------------------------------------------------------

def test_full_graph_user_nodes_expose_only_pseudonym_no_profile_fields():
    out = _build()
    user_nodes = [n for n in out["nodes"] if n["type"] == "user"]
    assert {n["id"] for n in user_nodes} == {"real_aaaa1111", "real_bbbb2222"}
    forbidden = {"age_band", "gender", "skin_type", "skin_tone", "scoped_preference_ids",
                 "owned_product_ids", "preferred_brand_ids"}
    for n in user_nodes:
        assert set(n.keys()) == {"id", "label", "type"}, f"user node leaked keys: {n.keys()}"
        assert n["label"] == n["id"]  # label is the pseudonym, nothing else
        assert not (set(n.keys()) & forbidden)


# ---------------------------------------------------------------------------
# edge families
# ---------------------------------------------------------------------------

def test_full_graph_owns_only_connects_in_catalog_products():
    out = _build()
    owns = [e for e in out["edges"] if e["family"] == "owns"]
    # real_aaaa1111 owns P1 (in catalog) and OUT (not) -> only P1 edge
    assert owns == [{"source": "real_aaaa1111", "target": "P1", "label": "OWNS", "family": "owns"}]


def test_full_graph_shares_attribute_unordered_dedup_keeps_max_score():
    out = _build()
    sa = [e for e in out["edges"] if e["family"] == "shares_attribute"]
    # P1<->P2 emitted once (unordered), max(5.0, 4.0)=5.0; P2<->OUT dropped (OUT off-catalog)
    assert len(sa) == 1
    assert {sa[0]["source"], sa[0]["target"]} == {"P1", "P2"}
    assert sa[0]["score"] == 5.0


def test_full_graph_user_concept_edge_carries_scope_and_type():
    out = _build()
    concern = [e for e in out["edges"]
               if e["family"] == "user_concept" and e["target"] == "concept:Concern:건조함"]
    assert concern and concern[0]["label"] == "HAS_CONCERN" and concern[0]["scope"] == "face"


# ---------------------------------------------------------------------------
# edge_types toggle
# ---------------------------------------------------------------------------

def test_full_graph_edge_types_toggle_filters_families_and_drops_orphan_concepts():
    out = _build(edge_types={"owns"})
    assert {e["family"] for e in out["edges"]} == {"owns"}
    # no concept nodes survive when only the owns family is requested
    assert [n for n in out["nodes"] if n["type"] not in ("user", "product")] == []
    # all users + products still present (anchors)
    assert {n["id"] for n in out["nodes"] if n["type"] == "product"} == {"P1", "P2", "P3"}
    assert {n["id"] for n in out["nodes"] if n["type"] == "user"} == {"real_aaaa1111", "real_bbbb2222"}


def test_full_graph_min_strength_applies_only_to_shares_attribute():
    out = _build(min_strength=4.5)
    sa = [e for e in out["edges"] if e["family"] == "shares_attribute"]
    # P1<->P2 max score 5.0 survives the 4.5 floor
    assert len(sa) == 1 and sa[0]["score"] == 5.0
    # product_concept edges (which also carry item scores) are NOT filtered
    pc = [e for e in out["edges"] if e["family"] == "product_concept"]
    assert any(e["label"] == "HAS_KEYWORD" for e in pc)  # kw_mild had score 1.0 < 4.5, still present

    # raise the floor above every similarity score -> shares_attribute empties,
    # other families untouched
    out2 = _build(min_strength=99.0)
    assert [e for e in out2["edges"] if e["family"] == "shares_attribute"] == []
    assert [e for e in out2["edges"] if e["family"] == "product_concept"]


# ---------------------------------------------------------------------------
# max_nodes truncation
# ---------------------------------------------------------------------------

def test_full_graph_max_nodes_truncation_is_deterministic_no_dangling():
    out = _build(max_nodes=5)
    meta = out["meta"]
    assert meta["truncated"] is True
    assert meta["total_nodes"] > 5
    assert meta["shown_nodes"] == 5 == len(out["nodes"])
    kept = {n["id"] for n in out["nodes"]}
    # users (2) + products (3) = 5 anchors kept first; concepts cut
    assert kept == {"real_aaaa1111", "real_bbbb2222", "P1", "P2", "P3"}
    # no dangling edges
    for e in out["edges"]:
        assert e["source"] in kept and e["target"] in kept
    # deterministic: identical inputs -> identical node ordering
    assert [n["id"] for n in _build(max_nodes=5)["nodes"]] == [n["id"] for n in out["nodes"]]


def test_full_graph_no_truncation_reports_totals():
    out = _build()
    meta = out["meta"]
    assert meta["truncated"] is False
    assert meta["total_nodes"] == meta["shown_nodes"] == len(out["nodes"])
    assert meta["total_edges"] == meta["shown_edges"] == len(out["edges"])
    # concepts appear only via edges -> every concept node has >=1 incident edge
    concept_ids = {n["id"] for n in out["nodes"] if n["type"] not in ("user", "product")}
    touched = {e["source"] for e in out["edges"]} | {e["target"] for e in out["edges"]}
    assert concept_ids <= touched


# ---------------------------------------------------------------------------
# endpoint e2e (TestClient, demo mode)
# ---------------------------------------------------------------------------

def test_full_graph_endpoint_ok(monkeypatch):
    client = _demo_client(monkeypatch)
    resp = client.get("/api/graphs/full")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"nodes", "edges", "meta"}
    assert body["meta"]["total_nodes"] == len(body["nodes"])
    assert {e["family"] for e in body["edges"]} == set(server._FULL_EDGE_FAMILIES)


def test_full_graph_endpoint_edge_types_param(monkeypatch):
    client = _demo_client(monkeypatch)
    body = client.get("/api/graphs/full?edge_types=product_concept,shares_attribute").json()
    assert {e["family"] for e in body["edges"]} == {"product_concept", "shares_attribute"}


def test_full_graph_endpoint_invalid_edge_types_400(monkeypatch):
    client = _demo_client(monkeypatch)
    assert client.get("/api/graphs/full?edge_types=bogus").status_code == 400


def test_full_graph_endpoint_bad_max_nodes_400(monkeypatch):
    client = _demo_client(monkeypatch)
    assert client.get("/api/graphs/full?max_nodes=0").status_code == 400


def test_full_graph_endpoint_user_privacy(monkeypatch):
    client = _demo_client(monkeypatch)
    body = client.get("/api/graphs/full").json()
    for n in body["nodes"]:
        if n["type"] == "user":
            assert set(n.keys()) == {"id", "label", "type"}


# ---------------------------------------------------------------------------
# regression — per-product/per-user graph endpoints untouched
# ---------------------------------------------------------------------------

def test_product_and_user_graph_endpoints_still_work(monkeypatch):
    client = _demo_client(monkeypatch)
    prod = client.get("/api/graphs/product/P1")
    assert prod.status_code == 200 and prod.json()["view_mode"] == "corpus"
    user = client.get("/api/graphs/user/real_aaaa1111")
    assert user.status_code == 200
    # legacy user graph keeps its scoped `id|scope:*` node convention (unchanged)
    assert "nodes" in user.json() and "edges" in user.json()
