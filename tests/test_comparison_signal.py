"""Phase 7 P7-1a — comparison signal wiring + mode realization (A1/A2).

Covers:
  A1  comparison overlap generation (owned × top_comparison), boost-only
      eligibility contract ("comparison alone never qualifies" in STRICT/EXPLORE,
      COMPARE admits it), comparison scoring, comparison explanation.
  A2  mode wiring into scorer/reranker: comparison feature weighted only in
      COMPARE, reranker diversity relaxed only in COMPARE, EXPLORE == default.

The default (STRICT/EXPLORE) path must stay byte-identical to pre-P7-1a; the
dense_golden ranking snapshot and evidence-family baseline enforce that
separately. Here we prove the COMPARE/comparison behavior actually fires.
"""

from __future__ import annotations

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.explainer import explain
from src.rec.recommendation_evidence_index import (
    BOOST_ONLY_TYPES,
    REVIEW_GRAPH_TYPES,
    build_candidate_eligibility,
)
from src.rec.reranker import rerank
from src.rec.scorer import ScoredProduct, Scorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _user(**overrides):
    base = {
        "user_id": "u1",
        "preferred_brand_ids": [],
        "active_category_ids": [],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "goal_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_keyword_ids": [],
        "preferred_context_ids": [],
        "owned_product_ids": [],
        "owned_family_ids": [],
        "repurchased_family_ids": [],
        "recent_purchase_brand_ids": [],
        "repurchase_brand_ids": [],
        "repurchase_category_ids": [],
    }
    base.update(overrides)
    return base


def _product(pid="P_CAND", **overrides):
    base = {
        "product_id": pid,
        "brand_id": "brand_cand",
        "category_id": "cat_a",
        "ingredient_ids": [],
        "main_benefit_ids": [],
        "brand_concept_ids": [],
        "category_concept_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [],
        "top_keyword_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_comparison_product_ids": [],
        "top_coused_product_ids": [],
        "review_count_all": 100,
        "source_review_count_6m": 0,
        "source_avg_rating_6m": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A1 — candidate overlap generation
# ---------------------------------------------------------------------------

def test_comparison_overlap_generated_when_owned_product_is_compared_with():
    # Candidate P_CAND was compared-with P_OWNED (a product the user owns).
    user = _user(owned_product_ids=["P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.9, "review_cnt": 3}])]

    # COMPARE admits comparison neighbors, so the candidate is returned.
    candidates = generate_candidates(user, products, mode=RecommendationMode.COMPARE)

    assert len(candidates) == 1
    assert "comparison:P_OWNED" in candidates[0].overlap_concepts


def test_comparison_matches_across_product_iri_prefix():
    # owned given WITH the product: IRI prefix; comparison ids are IRIs too.
    # Both must resolve to the same join key.
    user = _user(owned_product_ids=["product:P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.5, "review_cnt": 2}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.COMPARE)

    assert "comparison:P_OWNED" in candidates[0].overlap_concepts


def test_no_comparison_overlap_when_owned_not_in_comparison_list():
    user = _user(owned_product_ids=["P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OTHER", "score": 0.5, "review_cnt": 2}])]

    # No user-aligned evidence at all → filtered in every default mode.
    candidates = generate_candidates(user, products, mode=RecommendationMode.COMPARE)

    assert candidates == []


# ---------------------------------------------------------------------------
# A1 — boost-only eligibility contract (the mandatory "comparison alone" test)
# ---------------------------------------------------------------------------

def test_comparison_only_candidate_is_not_eligible_in_explore():
    user = _user(owned_product_ids=["P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.9, "review_cnt": 3}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    # Comparison is boost-only: it does not qualify a candidate by itself.
    assert candidates == []


def test_comparison_only_candidate_is_not_eligible_in_strict():
    user = _user(owned_product_ids=["P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.9, "review_cnt": 3}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.STRICT)

    assert candidates == []


def test_comparison_only_candidate_is_admitted_in_compare_mode():
    user = _user(owned_product_ids=["P_OWNED"])
    products = [_product(top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.9, "review_cnt": 3}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.COMPARE)

    assert len(candidates) == 1
    eligibility = candidates[0].eligibility
    assert eligibility.eligible is True
    assert eligibility.boost_only_paths == ["comparison:P_OWNED"]
    # Boost-only must NOT masquerade as a first-class evidence family.
    assert eligibility.evidence_families == []
    assert eligibility.review_graph_paths == []


def test_build_candidate_eligibility_boost_only_contract():
    # Default: comparison alone → not eligible, tracked as boost-only.
    default = build_candidate_eligibility(["comparison:P_OWNED"])
    assert default.eligible is False
    assert default.boost_only_paths == ["comparison:P_OWNED"]
    assert default.evidence_families == []
    assert "NO_USER_ALIGNED_EVIDENCE" in default.rejection_reasons

    # boost_only_qualifies (COMPARE) → eligible, still not a first-class family.
    admitted = build_candidate_eligibility(
        ["comparison:P_OWNED"], boost_only_qualifies=True,
    )
    assert admitted.eligible is True
    assert admitted.boost_only_paths == ["comparison:P_OWNED"]
    assert admitted.evidence_families == []

    # to_dict exposes the new bucket without disturbing existing keys.
    payload = admitted.to_dict()
    assert payload["boost_only_paths"] == ["comparison:P_OWNED"]
    assert payload["review_graph_paths"] == []


def test_comparison_is_boost_only_not_review_graph_family():
    # Regression: comparison was previously (incorrectly) in REVIEW_GRAPH_TYPES,
    # which would have let it buy eligibility as a review-graph relation.
    assert "comparison" in BOOST_ONLY_TYPES
    assert "comparison" not in REVIEW_GRAPH_TYPES


def test_comparison_does_not_alter_eligibility_when_real_evidence_present():
    # A candidate with genuine evidence (brand master truth) stays eligible via
    # that family; comparison is purely additive and reported separately.
    user = _user(
        owned_product_ids=["P_OWNED"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand", "weight": 1.0}],
    )
    products = [
        _product(
            brand_concept_ids=["concept:Brand:brand_cand"],
            top_comparison_product_ids=[{"id": "product:P_OWNED", "score": 0.9, "review_cnt": 3}],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    eligibility = candidates[0].eligibility
    assert eligibility.eligible is True
    assert eligibility.master_truth_paths  # brand
    assert eligibility.boost_only_paths == ["comparison:P_OWNED"]
    assert eligibility.evidence_families == ["PRODUCT_MASTER_TRUTH"]


# ---------------------------------------------------------------------------
# A1/A2 — comparison scoring is mode-gated
# ---------------------------------------------------------------------------

def test_comparison_scores_only_in_compare_mode():
    scorer = Scorer()
    scorer.load_config()  # loads modes.compare.comparison_neighbor
    user = _user()
    product = _product()

    # Baseline (no comparison overlap) carries only mode-invariant features
    # (e.g. novelty_bonus); comparison must add exactly nothing to it in EXPLORE.
    s_explore_base = scorer.score(user, product, [], mode=RecommendationMode.EXPLORE)
    s_explore = scorer.score(user, product, ["comparison:P_OWNED"], mode=RecommendationMode.EXPLORE)
    s_compare = scorer.score(user, product, ["comparison:P_OWNED"], mode=RecommendationMode.COMPARE)

    # EXPLORE: comparison weight is 0 → adds nothing over the no-overlap baseline.
    assert "comparison_alternative" not in s_explore.feature_contributions
    assert s_explore.raw_score == s_explore_base.raw_score
    assert s_explore.score_layers["review_graph_score"] == 0.0

    # COMPARE: comparison contributes, landing in the review_graph score layer.
    assert s_compare.feature_contributions["comparison_alternative"] > 0
    assert s_compare.raw_score > s_explore.raw_score
    assert s_compare.score_layers["review_graph_score"] > 0


def test_comparison_weight_zero_without_mode_config():
    # load_from_dict callers have no modes config → comparison never scores,
    # even in COMPARE mode.
    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 0.5})
    product = _product()

    s = scorer.score(_user(), product, ["comparison:P_OWNED"], mode=RecommendationMode.COMPARE)

    assert "comparison_alternative" not in s.feature_contributions
    assert s.raw_score == 0.0


def test_scorer_default_mode_is_explore_and_ignores_comparison():
    # The default mode argument must not score comparison (protects every
    # existing positional caller that omits mode).
    scorer = Scorer()
    scorer.load_config()
    s_baseline = scorer.score(_user(), _product(), [])
    s_default = scorer.score(_user(), _product(), ["comparison:P_OWNED"])
    assert "comparison_alternative" not in s_default.feature_contributions
    assert s_default.raw_score == s_baseline.raw_score


# ---------------------------------------------------------------------------
# A2 — reranker mode difference
# ---------------------------------------------------------------------------

def _scored(pid, score):
    return ScoredProduct(pid, score, score, score, {"keyword_match": score}, 100)


def test_compare_mode_relaxes_reranker_diversity():
    scored = [_scored("P1", 0.90), _scored("P2", 0.85), _scored("P3", 0.80)]
    # P1/P2 share a brand; EXPLORE penalizes the second same-brand pick.
    profiles = {
        "P1": {"brand_id": "B1", "category_id": "C1"},
        "P2": {"brand_id": "B1", "category_id": "C1"},
        "P3": {"brand_id": "B2", "category_id": "C2"},
    }

    explore = rerank(scored, product_profiles=profiles, diversity_weight=0.15, top_k=3,
                     mode=RecommendationMode.EXPLORE)
    compare = rerank(scored, product_profiles=profiles, diversity_weight=0.15, top_k=3,
                     mode=RecommendationMode.COMPARE)

    # EXPLORE applies a diversity penalty; COMPARE does not.
    assert any(r.diversity_bonus != 0.0 for r in explore)
    assert all(r.diversity_bonus == 0.0 for r in compare)
    # COMPARE keeps pure score order (comparable same-brand items stay adjacent).
    assert [r.product_id for r in compare] == ["P1", "P2", "P3"]


def test_reranker_default_mode_matches_explore():
    scored = [_scored("P1", 0.90), _scored("P2", 0.85), _scored("P3", 0.80)]
    profiles = {
        "P1": {"brand_id": "B1", "category_id": "C1"},
        "P2": {"brand_id": "B1", "category_id": "C1"},
        "P3": {"brand_id": "B2", "category_id": "C2"},
    }
    default = rerank(scored, product_profiles=profiles, diversity_weight=0.15, top_k=3)
    explore = rerank(scored, product_profiles=profiles, diversity_weight=0.15, top_k=3,
                     mode=RecommendationMode.EXPLORE)
    assert [r.product_id for r in default] == [r.product_id for r in explore]
    assert [r.diversity_bonus for r in default] == [r.diversity_bonus for r in explore]


# ---------------------------------------------------------------------------
# A1 — explanation
# ---------------------------------------------------------------------------

def test_comparison_explanation_mentions_owned_product():
    scored = ScoredProduct(
        product_id="P_CAND", raw_score=0.04, shrinked_score=0.03, final_score=0.03,
        feature_contributions={"comparison_alternative": 0.04},
    )
    result = explain(scored, ["comparison:58763"])
    assert result.paths
    assert result.paths[0].concept_type == "comparison"
    assert "58763" in result.summary_ko
    assert "비교" in result.summary_ko
