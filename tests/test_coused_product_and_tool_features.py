"""Tests: co-used product and tool signal features in recommendation."""
from src.rec.scorer import Scorer
from src.rec.explainer import explain, ScoredProduct

def test_tool_alignment_score():
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 0.0, "residual_bee_attr_match": 0.0,
        "context_match": 0.0, "concern_fit": 0.0, "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0, "goal_fit_master": 0.0,
        "category_affinity": 0.0,
        "freshness_boost": 0.0, "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0, "novelty_bonus": 0.0,
        "owned_family_penalty": 0.0, "repurchase_family_affinity": 0.0,
        "tool_alignment": 1.0, "coused_product_bonus": 0.0,
    })
    user = {"skin_type": None, "owned_product_ids": [], "owned_family_ids": [],
            "repurchased_family_ids": [], "repurchase_brand_ids": [],
            "recent_purchase_brand_ids": [], "preferred_brand_ids": []}
    product = {"product_id": "P1", "brand_id": "", "variant_family_id": None,
               "review_count_all": 100, "review_count_30d": 5,
               "top_concern_pos_ids": [], "top_concern_neg_ids": []}

    s_with_tool = scorer.score(user, product, overlap_concepts=["tool:퍼프"])
    s_without = scorer.score(user, product, overlap_concepts=[])
    assert s_with_tool.raw_score > s_without.raw_score

def test_coused_product_bonus():
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 0.0, "residual_bee_attr_match": 0.0,
        "context_match": 0.0, "concern_fit": 0.0, "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0, "goal_fit_master": 0.0,
        "category_affinity": 0.0,
        "freshness_boost": 0.0, "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0, "novelty_bonus": 0.0,
        "owned_family_penalty": 0.0, "repurchase_family_affinity": 0.0,
        "tool_alignment": 0.0, "coused_product_bonus": 1.0,
    })
    user = {"skin_type": None, "owned_product_ids": [], "owned_family_ids": [],
            "repurchased_family_ids": [], "repurchase_brand_ids": [],
            "recent_purchase_brand_ids": [], "preferred_brand_ids": []}
    product = {"product_id": "P1", "brand_id": "", "variant_family_id": None,
               "review_count_all": 100, "review_count_30d": 5,
               "top_concern_pos_ids": [], "top_concern_neg_ids": []}

    s_with_co = scorer.score(user, product, overlap_concepts=["coused:P002"])
    s_without = scorer.score(user, product, overlap_concepts=[])
    assert s_with_co.raw_score > s_without.raw_score

def test_default_weights_low_impact():
    """Default weights should keep tool/co-use impact minimal."""
    scorer = Scorer()
    # Use reasonable default weights
    scorer.load_from_dict({
        "keyword_match": 0.28, "residual_bee_attr_match": 0.12,
        "context_match": 0.15, "concern_fit": 0.15, "ingredient_match": 0.10,
        "brand_match_conf_weighted": 0.08, "goal_fit_master": 0.08,
        "category_affinity": 0.05,
        "freshness_boost": 0.05, "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0, "novelty_bonus": 0.0,
        "owned_family_penalty": 0.0, "repurchase_family_affinity": 0.0,
        "tool_alignment": 0.03, "coused_product_bonus": 0.03,
    })
    user = {"skin_type": None, "owned_product_ids": [], "owned_family_ids": [],
            "repurchased_family_ids": [], "repurchase_brand_ids": [],
            "recent_purchase_brand_ids": [], "preferred_brand_ids": []}
    product = {"product_id": "P1", "brand_id": "", "variant_family_id": None,
               "review_count_all": 100, "review_count_30d": 5,
               "top_concern_pos_ids": [], "top_concern_neg_ids": []}

    s = scorer.score(user, product, overlap_concepts=["tool:퍼프", "coused:P002"])
    # Tool + co-use should be small portion of possible score
    assert s.raw_score < 0.1  # limited impact

def test_tool_explanation():
    scored = ScoredProduct(
        product_id="P1", raw_score=1.0, shrinked_score=0.8, final_score=0.8,
        feature_contributions={"tool_alignment": 0.5},
    )
    result = explain(scored, ["tool:퍼프"])
    assert "퍼프" in result.summary_ko
    assert "도구" in result.summary_ko or "함께" in result.summary_ko
