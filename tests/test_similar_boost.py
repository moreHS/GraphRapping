"""Phase 8 P8-3a (G4): `similar` boost-only channel — contract tests.

Plan: fable_doc/plans/2026-07-16_phase8-3_g4_similar_boost_g5_query_related.md §1.
Covers the recommendation-side contract:
- evidence index: `similar` is boost-only (BOOST_ONLY_TYPES), never admissible,
  never counted as an evidence family, sole-evidence candidates fail in EVERY
  mode (including COMPARE's boost_only_qualifies opt-in).
- boost assembly (`build_similar_boost_index`): strength saturation at 30,
  anchor-self / already-owned-neighbour exclusion, dict-entry parsing,
  malformed-score defense.
- candidate generation: dormant when `similar_boost=None` (byte-identical),
  off-category anchors fire through generate_candidates_prefiltered, and
  `similar` is EXCLUDED from the retrieval overlap_score aggregate (>50-eligible
  cut invariance).
- scorer: top-level similar_product_weight (0.02, all modes, Σstrength clamp),
  manual-slider (load_from_dict) semantics = no boost, review_graph layer.
- explainer: OWNS_PRODUCT/SHARES_ATTRIBUTE edges, per-anchor PROPORTIONAL
  contribution (no duplicated totals), Korean summary.
- audit path: build_audit_report fires the boost for the real owned edge
  (user_dry_30f -> 58763) with families unchanged.
- provenance: a fired keyword shared-axis node key traces back to the demo
  signal rows (wrapped_signal equivalent) of BOTH products.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import (
    _SIMILAR_STRENGTH_SATURATION,
    build_similar_boost_index,
    extract_owned_product_ids,
    generate_candidates,
    generate_candidates_prefiltered,
)
from src.rec.explainer import explain
from src.rec.product_similarity import (
    SimilarProductSignal,
    keyword_node_key,
    keyword_signals_from_product_signals,
)
from src.rec.recommendation_evidence_index import (
    BOOST_ONLY_ADMISSIBLE_TYPES,
    BOOST_ONLY_TYPES,
    build_candidate_eligibility,
    classify_overlap,
)
from src.rec.scorer import ScoredProduct, Scorer, _score_layers


ALL_MODES = (RecommendationMode.STRICT, RecommendationMode.EXPLORE, RecommendationMode.COMPARE)


# ---------------------------------------------------------------------------
# Evidence index — boost-only invariants
# ---------------------------------------------------------------------------


def test_similar_is_boost_only_and_never_admissible():
    assert "similar" in BOOST_ONLY_TYPES
    # The admissible set is unchanged: comparison only (COMPARE opt-in).
    assert BOOST_ONLY_ADMISSIBLE_TYPES == frozenset({"comparison"})
    assert classify_overlap("similar:58763|strength=0.5") == "boost_only"


def test_similar_alone_never_buys_eligibility_even_with_opt_in():
    overlaps = ["similar:58763|strength=0.9"]
    for qualifies in (False, True):  # True = the COMPARE-mode opt-in path
        eligibility = build_candidate_eligibility(overlaps, boost_only_qualifies=qualifies)
        assert eligibility.eligible is False
        assert eligibility.boost_only_paths == overlaps
        assert eligibility.evidence_families == []  # never counted as a family
        assert "NO_USER_ALIGNED_EVIDENCE" in eligibility.rejection_reasons


def test_similar_rides_on_first_class_evidence_without_becoming_a_family():
    eligibility = build_candidate_eligibility(
        ["brand:concept:Brand:헤라", "similar:58763|strength=0.5"]
    )
    assert eligibility.eligible is True
    assert eligibility.evidence_families == ["PRODUCT_MASTER_TRUTH"]
    assert eligibility.boost_only_paths == ["similar:58763|strength=0.5"]


# ---------------------------------------------------------------------------
# Boost assembly — build_similar_boost_index / extract_owned_product_ids
# ---------------------------------------------------------------------------


def _sig(pid: str, score: float) -> SimilarProductSignal:
    return SimilarProductSignal(
        product_id=pid,
        neighbor_name=f"name-{pid}",
        score=score,
        shared_axes=[{"axis": "ingredient", "node_key": f"ingredient::{pid}", "label": pid, "idf": 1.0}],
    )


def test_extract_owned_product_ids_parses_dict_entries_and_strips_iri_prefix():
    user = {"owned_product_ids": [{"id": "product:58763", "weight": 1.0}, "P002", {"id": "P003"}]}
    assert extract_owned_product_ids(user) == {"58763", "P002", "P003"}


def test_boost_index_strength_saturates_at_30():
    assert _SIMILAR_STRENGTH_SATURATION == 30.0
    index = build_similar_boost_index({"A"}, {"A": [_sig("B", 15.0), _sig("C", 60.0)]})
    assert index["B"] == [("A", 0.5)]
    assert index["C"] == [("A", 1.0)]  # clamped, never > 1


def test_boost_index_excludes_anchor_self_and_owned_neighbours():
    # B is itself owned -> a candidate the user owns must never be boosted;
    # a (degenerate) self edge is dropped too.
    index = build_similar_boost_index(
        {"A", "B"}, {"A": [_sig("A", 30.0), _sig("B", 30.0), _sig("D", 30.0)]}
    )
    assert set(index) == {"D"}


def test_boost_index_accepts_dict_entries_and_skips_malformed_scores():
    signals = [
        {"product_id": "B", "score": 15.0},
        {"product_id": "C", "score": "not-a-number"},
        {"product_id": "E", "score": float("nan")},
        {"product_id": "F", "score": -3.0},
        {"product_id": "", "score": 5.0},
    ]
    index = build_similar_boost_index({"A"}, {"A": signals})
    assert set(index) == {"B"}
    assert index["B"] == [("A", 0.5)]


def test_boost_index_multi_anchor_lists_are_anchor_sorted():
    index = build_similar_boost_index(
        {"Z", "A"}, {"Z": [_sig("B", 30.0)], "A": [_sig("B", 15.0)]}
    )
    assert index["B"] == [("A", 0.5), ("Z", 1.0)]  # deterministic: sorted anchors


# ---------------------------------------------------------------------------
# Candidate generation — dormant default, firing, retrieval-cut exclusion
# ---------------------------------------------------------------------------


def _candidate_product(pid: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_id": None,
        "brand_concept_ids": ["concept:Brand:헤라"],
        "category_name": "수분크림",
        "category_concept_ids": [],
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [],
        "top_bee_attr_ids": [],
        "review_count_all": 20,
    }
    base.update(extra)
    return base


def _brand_user(**extra: Any) -> dict[str, Any]:
    user: dict[str, Any] = {"user_id": "U1", "preferred_brand_ids": [{"id": "concept:Brand:헤라"}]}
    user.update(extra)
    return user


def test_similar_only_candidate_fails_eligibility_in_every_mode():
    # The user's ONLY link to the product is the boost channel -> hard-filtered
    # as NO_USER_ALIGNED_EVIDENCE in every mode (sole-evidence contract).
    user = {"user_id": "U1", "owned_product_ids": [{"id": "product:ANCHOR"}]}
    product = _candidate_product("CAND", brand_concept_ids=[])
    for mode in ALL_MODES:
        candidates = generate_candidates(
            user, [product], mode=mode, similar_boost={"CAND": [("ANCHOR", 0.9)]}
        )
        assert candidates == []  # hard-filtered away everywhere


def test_none_boost_is_dormant_and_boost_changes_only_target_overlaps():
    user = _brand_user(owned_product_ids=[{"id": "product:ANCHOR"}])
    products = [_candidate_product("CAND"), _candidate_product("OTHER")]
    boost = {"CAND": [("ANCHOR", 0.5)]}

    base = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    boosted = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, similar_boost=boost
    )

    base_by_id = {c.product_id: c for c in base}
    boosted_by_id = {c.product_id: c for c in boosted}
    # Non-boosted candidate: byte-identical overlaps and score.
    assert boosted_by_id["OTHER"].overlap_concepts == base_by_id["OTHER"].overlap_concepts
    assert boosted_by_id["OTHER"].overlap_score == base_by_id["OTHER"].overlap_score
    # Boosted candidate: only the similar entry is added; the retrieval
    # aggregate (overlap_score) is UNCHANGED (similar excluded from it).
    assert boosted_by_id["CAND"].overlap_concepts == (
        base_by_id["CAND"].overlap_concepts + ["similar:ANCHOR|strength=0.5"]
    )
    assert boosted_by_id["CAND"].overlap_score == base_by_id["CAND"].overlap_score
    # Eligibility families are identical (boost-only never counts).
    assert (
        boosted_by_id["CAND"].eligibility.evidence_families
        == base_by_id["CAND"].eligibility.evidence_families
    )


def test_already_owned_candidate_gets_no_similar_overlap():
    user = _brand_user(owned_product_ids=[{"id": "product:CAND"}])
    products = [_candidate_product("CAND")]
    # Defense-in-depth: even if a (mis-assembled) boost index targets an owned
    # candidate, the generator refuses to emit the overlap.
    candidates = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE,
        similar_boost={"CAND": [("SOMETHING", 0.9)]},
    )
    owned = next(c for c in candidates if c.product_id == "CAND")
    assert owned.already_owned is True
    assert not [c for c in owned.overlap_concepts if c.startswith("similar:")]


def test_off_category_anchor_fires_through_prefiltered_entry_point():
    # The anchor product is NOT in the prefiltered (category-tab) id set — only
    # the candidate is. The boost index is corpus-wide, so the overlap fires.
    user = _brand_user(owned_product_ids=[{"id": "product:ANCHOR"}])
    products_by_id = {
        "CAND": _candidate_product("CAND", category_name="립스틱"),
        "ANCHOR": _candidate_product("ANCHOR", category_name="수분크림"),
    }
    candidates = generate_candidates_prefiltered(
        user_profile=user,
        prefiltered_product_ids=["CAND"],  # anchor excluded by the tab
        product_profiles_by_id=products_by_id,
        mode=RecommendationMode.EXPLORE,
        similar_boost={"CAND": [("ANCHOR", 0.7)]},
    )
    assert [c.product_id for c in candidates] == ["CAND"]
    assert "similar:ANCHOR|strength=0.7" in candidates[0].overlap_concepts


def test_similar_never_reorders_the_retrieval_50_cut():
    # 55 eligible candidates with identical first-class overlap (score 1 each).
    # The stable sort keeps input order -> the first 50 survive the cut. The
    # last 5 get TWO similar entries each: if similar counted toward
    # overlap_score they would jump the cut (3 > 1) — they must not.
    user = _brand_user(owned_product_ids=[{"id": "product:ANCHOR"}])
    products = [_candidate_product(f"P{i:02d}") for i in range(55)]
    boost = {f"P{i:02d}": [("ANCHOR", 0.9), ("ANCHOR2", 0.8)] for i in range(50, 55)}

    base = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    boosted = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, similar_boost=boost
    )

    assert [c.product_id for c in base] == [f"P{i:02d}" for i in range(50)]
    assert [c.product_id for c in boosted] == [c.product_id for c in base]  # cut unchanged


# ---------------------------------------------------------------------------
# Scorer — top-level weight, clamp, modes, manual sliders, layer
# ---------------------------------------------------------------------------


def _scored(user: dict[str, Any], product: dict[str, Any], overlaps: list[str],
            mode: RecommendationMode = RecommendationMode.EXPLORE,
            scorer: Scorer | None = None) -> ScoredProduct:
    if scorer is None:
        scorer = Scorer()
        scorer.load_config()
    return scorer.score(user, product, overlaps, mode=mode)


def test_scorer_similar_contribution_in_every_mode_with_yaml_weight():
    product = _candidate_product("CAND")
    for mode in ALL_MODES:
        scored = _scored({}, product, ["similar:ANCHOR|strength=0.5"], mode=mode)
        assert scored.feature_contributions["similar_product_affinity"] == pytest.approx(0.01)  # 0.02 * 0.5


def test_scorer_multi_anchor_strength_sum_clamps_at_one():
    product = _candidate_product("CAND")
    scored = _scored({}, product, [
        "similar:A|strength=0.7", "similar:B|strength=0.6",  # Σ = 1.3 -> clamp 1.0
    ])
    assert scored.feature_contributions["similar_product_affinity"] == pytest.approx(0.02)


def test_scorer_manual_sliders_load_from_dict_yields_zero_similar_contribution():
    # load_from_dict (manual weight sliders) does NOT load top-level backend
    # boosts — the D1/D2 semantics, pinned here for `similar` too.
    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 0.16})
    scored = _scored({}, _candidate_product("CAND"), ["similar:A|strength=0.9"], scorer=scorer)
    assert "similar_product_affinity" not in scored.feature_contributions


def test_similar_contribution_lands_in_review_graph_layer():
    layers = _score_layers({"similar_product_affinity": 0.014})
    assert layers["review_graph_score"] == pytest.approx(0.014)


# ---------------------------------------------------------------------------
# Explainer — edges, per-anchor proportional contribution, summary
# ---------------------------------------------------------------------------


def test_explainer_similar_paths_share_contribution_per_anchor():
    # ONE scored feature, TWO anchors: each path carries its proportional
    # share (0.6 : 0.2), never the duplicated total.
    scored = ScoredProduct(
        product_id="CAND", raw_score=0.016, shrinked_score=0.016, final_score=0.016,
        feature_contributions={"similar_product_affinity": 0.016},
    )
    exp = explain(scored, ["similar:A1|strength=0.6", "similar:A2|strength=0.2"])
    paths = {p.concept_id: p for p in exp.paths}
    assert set(paths) == {"A1", "A2"}
    assert paths["A1"].contribution == pytest.approx(0.016 * 0.6 / 0.8)
    assert paths["A2"].contribution == pytest.approx(0.016 * 0.2 / 0.8)
    assert paths["A1"].contribution + paths["A2"].contribution == pytest.approx(0.016)
    for p in paths.values():
        assert p.user_edge == "OWNS_PRODUCT"
        assert p.product_edge == "SHARES_ATTRIBUTE"
    assert "보유하신 'A1' 제품과 속성을 공유하는 상품" in exp.summary_ko


def test_explainer_single_anchor_path_carries_full_contribution():
    scored = ScoredProduct(
        product_id="CAND", raw_score=0.01, shrinked_score=0.01, final_score=0.01,
        feature_contributions={"similar_product_affinity": 0.01},
    )
    exp = explain(scored, ["similar:58763|strength=0.5"])
    assert len(exp.paths) == 1
    assert exp.paths[0].contribution == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Audit path — the real owned edge fires end-to-end (dense fixture)
# ---------------------------------------------------------------------------


def test_audit_path_fires_similar_boost_for_real_owned_edge():
    from scripts.audit_recommendation_evidence import build_audit_report

    report = build_audit_report(
        fixture="dense_golden", kg_mode="on", user_id="user_dry_30f",
        category_group="makeup",
    )
    scenario = report["scenarios"][0]
    fired = [
        row for row in scenario["top_products"]
        if any(c.startswith("similar:58763|strength=") for c in row["overlap_concepts"])
    ]
    assert fired, "expected the owned anchor 58763 to boost a makeup candidate"
    for row in fired:
        assert row["feature_contributions"]["similar_product_affinity"] > 0
        # Boost-only: no new family name appears anywhere.
        assert set(row["evidence_families"]) <= {
            "PRODUCT_MASTER_TRUTH", "REVIEW_GRAPH_RELATION",
            "REVIEW_GRAPH_WEAK_RELATION", "PURCHASE_BEHAVIOR",
        }
        assert row["eligibility"]["boost_only_paths"]


# ---------------------------------------------------------------------------
# Provenance — keyword shared-axis node traces back to the signal rows
# ---------------------------------------------------------------------------


def test_fired_keyword_shared_axis_traces_back_to_demo_signal_rows():
    """§13.3(5): the keyword composite node on a fired similar edge must be
    reconstructible from BOTH products' raw signal rows (the demo-side
    wrapped_signal equivalent) — untraceable evidence is not admissible."""
    from src.web.serving_store import build_and_attach_similarity

    products = [
        {"product_id": "A", "category_name": "토너"},
        {"product_id": "B", "category_name": "립스틱"},  # cross-category on purpose
        {"product_id": "C", "category_name": "세럼"},
    ]
    product_signals = {
        "A": [{"keyword_id": "concept:Keyword:kw_moisturizing",
               "bee_attr_id": "concept:BEEAttr:moisture", "polarity": "NEU"}],
        "B": [{"keyword_id": "concept:Keyword:kw_moisturizing",
               "bee_attr_id": "concept:BEEAttr:moisture", "polarity": "NEU"}],
    }
    ungated = build_and_attach_similarity(
        products, keyword_signals_from_product_signals(product_signals),
        include_ungated=True,
    )
    assert ungated is not None
    # Ungated: the cross-category pair fires (the gated attach would drop it).
    neighbours = {s.product_id: s for s in ungated["A"]}
    assert "B" in neighbours
    keyword_axes = [ax for ax in neighbours["B"].shared_axes if ax["axis"] == "keyword"]
    assert keyword_axes, "keyword shared axis expected on the fired edge"
    node_key = keyword_axes[0]["node_key"]
    # Trace back: recomputing the composite key from each product's raw signal
    # rows must reproduce the fired node key on BOTH sides of the edge.
    for pid in ("A", "B"):
        assert any(
            keyword_node_key(row["bee_attr_id"], row["keyword_id"], row["polarity"]) == node_key
            for row in product_signals[pid]
        ), f"node {node_key} not traceable to product {pid} signal rows"
