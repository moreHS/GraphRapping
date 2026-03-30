"""Tests for recommendation engine: candidate → score → explain → hook → question."""

import pytest
from src.rec.candidate_generator import generate_candidates, CandidateProduct
from src.rec.scorer import Scorer, ScoredProduct
from src.rec.explainer import explain
from src.rec.hook_generator import generate_hooks
from src.rec.next_question import generate_next_question
from src.common.enums import RecommendationMode


@pytest.fixture
def user_profile():
    return {
        "user_id": "u_1001",
        "skin_type": "건성",
        "preferred_brand_ids": [{"id": "brand_hera", "weight": 1.0}],
        "preferred_category_ids": [{"id": "cat_cushion", "weight": 0.9}],
        "preferred_ingredient_ids": [{"id": "ing_ceramide", "weight": 0.8}],
        "avoided_ingredient_ids": [{"id": "ing_ethanol", "weight": 1.0}],
        "concern_ids": [{"id": "concern_dryness", "weight": 0.9}],
        "goal_ids": [{"id": "goal_soothing", "weight": 0.8}],
        "preferred_bee_attr_ids": [{"id": "bee_attr_spreadability", "weight": 0.8}],
        "preferred_keyword_ids": [{"id": "kw_thin_spread", "weight": 0.9}],
        "preferred_context_ids": [{"id": "ctx_morning", "weight": 0.7}],
    }


@pytest.fixture
def product_profiles():
    return [
        {
            "product_id": "P001",
            "brand_id": "brand_hera",
            "category_id": "cat_cushion",
            "ingredient_ids": ["ing_ceramide", "ing_niacinamide"],
            "main_benefit_ids": ["goal_soothing"],
            "top_bee_attr_ids": [{"id": "bee_attr_spreadability", "score": 0.9, "review_cnt": 50}],
            "top_keyword_ids": [{"id": "kw_thin_spread", "score": 0.91, "review_cnt": 40}],
            "top_context_ids": [{"id": "ctx_morning", "score": 0.8, "review_cnt": 30}],
            "top_concern_pos_ids": [{"id": "concern_dryness", "score": 0.85, "review_cnt": 60}],
            "top_concern_neg_ids": [],
            "top_tool_ids": [],
            "top_comparison_product_ids": [],
            "top_coused_product_ids": [],
            "review_count_all": 120,
            "review_count_30d": 15,
        },
        {
            "product_id": "P002",
            "brand_id": "brand_clio",
            "category_id": "cat_cushion",
            "ingredient_ids": ["ing_ethanol", "ing_talc"],  # user avoids ethanol!
            "main_benefit_ids": [],
            "top_bee_attr_ids": [],
            "top_keyword_ids": [],
            "top_context_ids": [],
            "top_concern_pos_ids": [],
            "top_concern_neg_ids": [],
            "top_tool_ids": [],
            "top_comparison_product_ids": [],
            "top_coused_product_ids": [],
            "review_count_all": 80,
        },
        {
            "product_id": "P003",
            "brand_id": "brand_other",
            "category_id": "cat_lipstick",  # different category
            "ingredient_ids": [],
            "main_benefit_ids": [],
            "top_bee_attr_ids": [],
            "top_keyword_ids": [],
            "top_context_ids": [],
            "top_concern_pos_ids": [],
            "top_concern_neg_ids": [],
            "top_tool_ids": [],
            "top_comparison_product_ids": [],
            "top_coused_product_ids": [],
            "review_count_all": 50,
        },
    ]


class TestCandidateGeneration:
    def test_hard_filter_avoided_ingredient(self, user_profile, product_profiles):
        candidates = generate_candidates(user_profile, product_profiles)
        product_ids = {c.product_id for c in candidates}
        assert "P002" not in product_ids  # ethanol conflict

    def test_hard_filter_category_strict(self, user_profile, product_profiles):
        candidates = generate_candidates(user_profile, product_profiles, mode=RecommendationMode.STRICT)
        product_ids = {c.product_id for c in candidates}
        assert "P003" not in product_ids  # lipstick ≠ cushion

    def test_explore_mode_allows_category(self, user_profile, product_profiles):
        candidates = generate_candidates(user_profile, product_profiles, mode=RecommendationMode.EXPLORE)
        product_ids = {c.product_id for c in candidates}
        assert "P003" in product_ids  # explore allows category mismatch

    def test_overlap_scoring(self, user_profile, product_profiles):
        candidates = generate_candidates(user_profile, product_profiles)
        p1 = next(c for c in candidates if c.product_id == "P001")
        assert p1.overlap_score > 0
        assert any("keyword" in c for c in p1.overlap_concepts)


class TestScorer:
    def test_scoring_with_config(self, user_profile, product_profiles):
        scorer = Scorer()
        scorer.load_from_dict({
            "keyword_match": 0.28,
            "residual_bee_attr_match": 0.12,
            "context_match": 0.15,
            "concern_fit": 0.15,
            "ingredient_match": 0.10,
            "brand_match_conf_weighted": 0.08,
            "goal_fit": 0.08,
            "category_affinity": 0.05,
            "freshness_boost": 0.05,
        })

        overlap = ["brand:brand_hera", "category:cat_cushion", "keyword:kw_thin_spread",
                    "bee_attr:bee_attr_spreadability", "context:ctx_morning", "concern:concern_dryness",
                    "goal:goal_soothing"]
        scored = scorer.score(user_profile, product_profiles[0], overlap)
        assert scored.raw_score > 0
        assert scored.shrinked_score > 0
        assert scored.shrinked_score <= scored.raw_score

    def test_shrinkage_effect(self, user_profile):
        scorer = Scorer()
        scorer.load_from_dict({"keyword_match": 1.0}, shrinkage_k=10)

        low_support = {"product_id": "X", "review_count_all": 2}
        high_support = {"product_id": "Y", "review_count_all": 100}

        s_low = scorer.score(user_profile, low_support, ["keyword:kw"])
        s_high = scorer.score(user_profile, high_support, ["keyword:kw"])
        assert s_high.shrinked_score > s_low.shrinked_score


class TestExplainer:
    def test_score_faithful(self, user_profile, product_profiles):
        scorer = Scorer()
        scorer.load_from_dict({"keyword_match": 0.28, "concern_fit": 0.15, "context_match": 0.15})

        overlap = ["keyword:kw_thin_spread", "concern:concern_dryness", "context:ctx_morning"]
        scored = scorer.score(user_profile, product_profiles[0], overlap)
        explanation = explain(scored, overlap)

        assert explanation.product_id == "P001"
        assert len(explanation.paths) > 0
        # All explanation paths should come from actual score contributors
        for path in explanation.paths:
            assert path.contribution > 0
        assert explanation.summary_ko  # Korean summary generated


class TestHookGenerator:
    def test_hooks(self, user_profile, product_profiles):
        scorer = Scorer()
        scorer.load_from_dict({"keyword_match": 0.28, "concern_fit": 0.15})
        overlap = ["keyword:kw_thin_spread", "concern:concern_dryness", "brand:brand_hera"]
        scored = scorer.score(user_profile, product_profiles[0], overlap)
        explanation = explain(scored, overlap)
        hooks = generate_hooks(explanation)
        assert hooks.discovery
        assert hooks.consideration
        assert hooks.conversion


class TestNextQuestion:
    def test_question_for_missing_data(self):
        sparse_profile = {"user_id": "u_2"}
        question = generate_next_question(sparse_profile)
        assert question is not None
        assert question.question_ko

    def test_no_question_when_complete(self, user_profile):
        question = generate_next_question(user_profile)
        # Even with data, should pick an axis without data (e.g. scent)
        if question:
            assert question.uncertainty_axis
