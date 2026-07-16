"""Tests for Phase 8 G1 product-product shared-node similarity."""

from __future__ import annotations

import math

from src.rec.product_similarity import (
    SimilarProductSignal,
    attach_similarity_signals,
    build_idf,
    build_product_nodes,
    build_similarity_signals,
    keyword_node_key,
    keyword_signals_from_product_signals,
    symmetrize,
)


# --------------------------------------------------------------------------
# Node keys: axis namespace, composite keyword key, canonical alias, polarity
# --------------------------------------------------------------------------

def test_profile_axes_use_bare_concept_id_and_namespaces():
    nodes = build_product_nodes(
        [
            {
                "product_id": "A",
                "ingredient_concept_ids": ["concept:Ingredient:글리세린"],
                "category_concept_ids": ["concept:Category:토너"],
                "brand_concept_ids": ["concept:Brand:이니스프리"],
                "main_benefit_concept_ids": ["concept:Goal:보습"],
            }
        ],
        {},
    )
    assert nodes["A"] == {
        "ingredient::글리세린",
        "category::토너",
        "brand::이니스프리",
        "goal::보습",
    }


def test_axis_namespace_prevents_cross_axis_collision():
    # Same raw id "x" in an ingredient concept and a keyword must not merge.
    nodes = build_product_nodes(
        [{"product_id": "A", "ingredient_concept_ids": ["concept:Ingredient:x"]}],
        {"A": [("concept:BEEAttr:b", "concept:Keyword:x", "POS")]},
        alias_map={},
    )
    assert "ingredient::x" in nodes["A"]
    assert "keyword::b:x:POS" in nodes["A"]
    assert len(nodes["A"]) == 2


def test_keyword_composite_key_same_keyword_different_bee_attr_are_distinct():
    # "가볍다" case: one canonical keyword under two BEE attrs => two nodes.
    nodes = build_product_nodes(
        [{"product_id": "A"}, {"product_id": "B"}],
        {
            "A": [("concept:BEEAttr:formulation", "concept:Keyword:kw_light", "POS")],
            "B": [("concept:BEEAttr:spreadability", "concept:Keyword:kw_light", "POS")],
        },
        alias_map={},
    )
    assert nodes["A"] == {"keyword::formulation:kw_light:POS"}
    assert nodes["B"] == {"keyword::spreadability:kw_light:POS"}
    assert nodes["A"].isdisjoint(nodes["B"])


def test_keyword_composite_key_polarity_separates_nodes():
    nodes = build_product_nodes(
        [{"product_id": "A"}, {"product_id": "B"}],
        {
            "A": [("concept:BEEAttr:f", "concept:Keyword:kw_light", "POS")],
            "B": [("concept:BEEAttr:f", "concept:Keyword:kw_light", "NEG")],
        },
        alias_map={},
    )
    assert nodes["A"] == {"keyword::f:kw_light:POS"}
    assert nodes["B"] == {"keyword::f:kw_light:NEG"}
    assert nodes["A"].isdisjoint(nodes["B"])


def test_canonical_alias_folds_before_scoping():
    # IRI suffix -> canonical fold (kw_moist -> kw_moisturizing) -> scope by bee/pol,
    # so an alias id and its canonical id become the SAME shared node.
    nodes = build_product_nodes(
        [{"product_id": "A"}, {"product_id": "B"}],
        {
            "A": [("concept:BEEAttr:be", "concept:Keyword:kw_moist", "POS")],
            "B": [("concept:BEEAttr:be", "concept:Keyword:kw_moisturizing", "POS")],
        },
        alias_map={"kw_moist": "kw_moisturizing"},
    )
    assert nodes["A"] == {"keyword::be:kw_moisturizing:POS"}
    assert nodes["B"] == {"keyword::be:kw_moisturizing:POS"}
    assert nodes["A"] == nodes["B"]


def test_keyword_node_key_helper_handles_bare_and_iri():
    assert (
        keyword_node_key("bee_attr_f", "kw_x", "POS", alias_map={})
        == "keyword::bee_attr_f:kw_x:POS"
    )
    assert (
        keyword_node_key("concept:BEEAttr:bee_attr_f", "concept:Keyword:kw_x", "NEU", alias_map={})
        == "keyword::bee_attr_f:kw_x:NEU"
    )


def test_bee_attr_is_never_a_standalone_scored_node():
    nodes = build_product_nodes(
        [{"product_id": "A"}],
        {"A": [("concept:BEEAttr:beX", "concept:Keyword:kw", "NEU")]},
        alias_map={},
    )
    # Only the keyword composite axis exists; bee_attr appears solely inside it.
    assert all(node.startswith("keyword::") for node in nodes["A"])
    assert not any(node.startswith("bee") for node in nodes["A"])


# --------------------------------------------------------------------------
# IDF
# --------------------------------------------------------------------------

def test_build_idf_is_log_n_over_df_and_damps_universal_nodes():
    nodes = {
        "A": {"brand::big", "ingredient::rare"},
        "B": {"brand::big"},
        "C": {"brand::big"},
    }
    idf = build_idf(nodes)
    # brand::big is in all 3 products -> df == N -> IDF 0 (hub fully damped).
    assert idf["brand::big"] == 0.0
    # ingredient::rare in 1 of 3 -> IDF = log(3/1).
    assert idf["ingredient::rare"] == math.log(3.0)


def test_build_idf_empty_corpus():
    assert build_idf({}) == {}


# --------------------------------------------------------------------------
# Similarity: score = sum of shared IDF, top_n, min_score
# --------------------------------------------------------------------------

def _score_fixture_profiles():
    # 5 products. i_a/i_b are common (df=4 -> low IDF); i_rare shared only by
    # P and R (df=2 -> high IDF). P-Q share two low nodes; P-R share one high.
    return [
        {"product_id": "P", "ingredient_concept_ids": ["i_a", "i_b", "i_rare"]},
        {"product_id": "Q", "ingredient_concept_ids": ["i_a", "i_b"]},
        {"product_id": "R", "ingredient_concept_ids": ["i_rare"]},
        {"product_id": "F1", "ingredient_concept_ids": ["i_a", "i_b"]},
        {"product_id": "F2", "ingredient_concept_ids": ["i_a", "i_b"]},
    ]


def test_score_is_sum_of_shared_idf_not_count():
    profiles = _score_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    signals = build_similarity_signals(nodes, profiles, idf=idf, top_n=10)

    p_neighbors = {s.product_id: s for s in signals["P"]}
    high = idf["ingredient::i_rare"]         # log(5/2)
    low_sum = idf["ingredient::i_a"] + idf["ingredient::i_b"]  # 2 * log(5/4)
    assert high > low_sum  # one rare node beats two common nodes
    assert round(p_neighbors["R"].score, 6) == round(high, 6)
    assert round(p_neighbors["Q"].score, 6) == round(low_sum, 6)
    # R (1 shared node) outranks Q (2 shared nodes) because IDF mass is higher.
    assert signals["P"][0].product_id == "R"


def test_top_n_truncates_neighbours():
    profiles = _score_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    signals = build_similarity_signals(nodes, profiles, idf=idf, top_n=1)
    assert len(signals["P"]) == 1
    assert signals["P"][0].product_id == "R"


def test_min_score_filters_weak_pairs():
    profiles = _score_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    low_sum = idf["ingredient::i_a"] + idf["ingredient::i_b"]
    high = idf["ingredient::i_rare"]
    threshold = (low_sum + high) / 2  # between the two pair scores
    signals = build_similarity_signals(
        nodes, profiles, idf=idf, top_n=10, min_score=threshold
    )
    assert [s.product_id for s in signals["P"]] == ["R"]


def test_shared_axes_carry_node_evidence():
    profiles = _score_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    signals = build_similarity_signals(nodes, profiles, idf=idf, top_n=10)
    r_signal = next(s for s in signals["P"] if s.product_id == "R")
    assert r_signal.shared_axes == [
        {
            "axis": "ingredient",
            "node_key": "ingredient::i_rare",
            "label": "i_rare",
            "idf": round(idf["ingredient::i_rare"], 4),
        }
    ]


def test_universal_node_produces_no_signal_evidence_first():
    # Two products sharing only a universal node (df == N -> IDF 0). No
    # discriminative shared evidence -> no signal emitted (evidence-first).
    profiles = [
        {"product_id": "A", "brand_concept_ids": ["b"]},
        {"product_id": "B", "brand_concept_ids": ["b"]},
    ]
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    assert idf["brand::b"] == 0.0
    assert build_similarity_signals(nodes, profiles, idf=idf, top_n=5) == {}


# --------------------------------------------------------------------------
# category_gate
# --------------------------------------------------------------------------

def _gate_fixture_profiles():
    # A,C,D skincare; B makeup. i1 shared by A,B,C (df=3<N=4 -> IDF>0).
    return [
        {"product_id": "A", "category_name": "토너", "ingredient_concept_ids": ["i1"]},
        {"product_id": "B", "category_name": "립스틱", "ingredient_concept_ids": ["i1"]},
        {"product_id": "C", "category_name": "에센스", "ingredient_concept_ids": ["i1"]},
        {"product_id": "D", "category_name": "세럼", "ingredient_concept_ids": ["i2"]},
    ]


def test_category_gate_off_keeps_cross_category_pairs():
    profiles = _gate_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    signals = build_similarity_signals(
        nodes, profiles, idf=idf, category_gate=False, top_n=10
    )
    # A,B,C all share i1 across categories.
    assert {s.product_id for s in signals["A"]} == {"B", "C"}
    assert {s.product_id for s in signals["B"]} == {"A", "C"}


def test_category_gate_on_keeps_only_same_group_pairs():
    profiles = _gate_fixture_profiles()
    nodes = build_product_nodes(profiles, {})
    idf = build_idf(nodes)
    signals = build_similarity_signals(
        nodes, profiles, idf=idf, category_gate=True, top_n=10
    )
    # A,C are skincare and stay neighbours; B (makeup) is filtered out entirely.
    assert {s.product_id for s in signals["A"]} == {"C"}
    assert {s.product_id for s in signals["C"]} == {"A"}
    assert "B" not in signals


# --------------------------------------------------------------------------
# attach (ephemeral, one-shot) + symmetrize
# --------------------------------------------------------------------------

def _axis(node_key="ingredient::i", idf=1.0):
    return {"axis": node_key.split("::")[0], "node_key": node_key, "label": "i", "idf": idf}


def test_attach_sets_field_and_absent_without_attach():
    profiles = [{"product_id": "A"}, {"product_id": "B"}]
    # No attach yet -> field is absent (dormant).
    assert "similar_product_ids" not in profiles[0]
    signals = {
        "A": [SimilarProductSignal("B", "B name", 1.5, [_axis()])],
    }
    attach_similarity_signals(profiles, signals)
    assert profiles[0]["similar_product_ids"][0]["product_id"] == "B"
    assert profiles[0]["similar_product_ids"][0]["neighbor_name"] == "B name"
    # A neighbourless product gets an explicit empty list, not a missing field.
    assert profiles[1]["similar_product_ids"] == []


def test_symmetrize_unions_asymmetric_neighbours():
    # A kept B, but B's top_n did not keep A.
    signals = {
        "A": [SimilarProductSignal("B", "B name", 2.0, [_axis()])],
    }
    sym = symmetrize(signals)
    assert "B" in sym
    assert sym["B"][0].product_id == "A"
    assert sym["B"][0].score == 2.0
    assert sym["B"][0].shared_axes == signals["A"][0].shared_axes


def test_symmetrize_no_duplicate_when_both_directions_present():
    signals = {
        "A": [SimilarProductSignal("B", "B name", 2.0, [_axis()])],
        "B": [SimilarProductSignal("A", "A name", 2.0, [_axis()])],
    }
    sym = symmetrize(signals)
    assert len(sym["A"]) == 1
    assert len(sym["B"]) == 1


# --------------------------------------------------------------------------
# demo source adapter
# --------------------------------------------------------------------------

def test_keyword_signals_from_product_signals_keeps_only_keyword_signals():
    product_signals = {
        "P1": [
            {"keyword_id": "concept:Keyword:kw_a", "bee_attr_id": "concept:BEEAttr:be", "polarity": "POS"},
            {"keyword_id": None, "bee_attr_id": "concept:BEEAttr:be", "polarity": "POS"},  # dropped
            {"dst_id": "concept:Ingredient:x"},  # non-keyword signal, dropped
        ],
        "P2": [
            {"dst_id": "concept:Brand:b"},  # no keyword -> P2 absent
        ],
    }
    out = keyword_signals_from_product_signals(product_signals)
    assert out == {
        "P1": [("concept:BEEAttr:be", "concept:Keyword:kw_a", "POS")],
    }
