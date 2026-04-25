"""Tests: texture preference dual-layer in scorer and explainer."""

from src.rec.scorer import Scorer, ScoredProduct
from src.rec.explainer import explain


def test_texture_keyword_stronger_than_attr_only():
    """Keyword match for texture should contribute more than attr-only."""
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 2.0,
        "residual_bee_attr_match": 0.5,
        "context_match": 0.0,
        "concern_fit": 0.0,
        "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0,
        "goal_fit_master": 0.0,
        "category_affinity": 0.0,
        "freshness_boost": 0.0,
        "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0,
        "novelty_bonus": 0.0,
    })

    user = {
        "skin_type": None,
        "owned_product_ids": [],
        "repurchase_brand_ids": [],
        "recent_purchase_brand_ids": [],
        "preferred_brand_ids": [],
    }
    product = {
        "product_id": "P1",
        "brand_id": "",
        "review_count_all": 100,
        "review_count_30d": 5,
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
    }

    # Product A: has both keyword + attr match
    score_both = scorer.score(
        user, product,
        overlap_concepts=["keyword:GelLike", "bee_attr:Texture"],
    )
    # Product B: has only attr match
    score_attr = scorer.score(
        user, product,
        overlap_concepts=["bee_attr:Texture"],
    )

    assert score_both.raw_score > score_attr.raw_score


def test_residual_bee_attr_zero_when_keyword_covers():
    """When keyword count >= bee_attr count, residual_bee_attr should be 0."""
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 2.0,
        "residual_bee_attr_match": 0.5,
        "context_match": 0.0,
        "concern_fit": 0.0,
        "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0,
        "goal_fit_master": 0.0,
        "category_affinity": 0.0,
        "freshness_boost": 0.0,
        "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0,
        "novelty_bonus": 0.0,
    })

    user = {
        "skin_type": None,
        "owned_product_ids": [],
        "repurchase_brand_ids": [],
        "recent_purchase_brand_ids": [],
        "preferred_brand_ids": [],
    }
    product = {
        "product_id": "P1",
        "brand_id": "",
        "review_count_all": 100,
        "review_count_30d": 5,
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
    }

    scored = scorer.score(
        user, product,
        overlap_concepts=["keyword:GelLike", "bee_attr:Texture"],
    )

    # residual_bee_attr_match should not appear in contributions
    # because max(0, 1 - 1) = 0
    assert scored.feature_contributions.get("residual_bee_attr_match", 0.0) == 0.0


def test_texture_explanation_two_layers():
    """Explanation should show texture axis + specific texture expression."""
    scored = ScoredProduct(
        product_id="P1",
        raw_score=2.0,
        shrinked_score=1.5,
        final_score=1.5,
        feature_contributions={
            "keyword_match": 1.5,
            "residual_bee_attr_match": 0.3,
        },
    )
    result = explain(scored, ["keyword:GelLike", "bee_attr:Texture"])
    # Should mention both texture axis and specific keyword
    assert "제형" in result.summary_ko
    assert "젤" in result.summary_ko


def test_texture_attr_only_explanation():
    """When only bee_attr Texture matches (no keyword), use axis-level text."""
    scored = ScoredProduct(
        product_id="P1",
        raw_score=0.5,
        shrinked_score=0.4,
        final_score=0.4,
        feature_contributions={
            "residual_bee_attr_match": 0.5,
        },
    )
    result = explain(scored, ["bee_attr:Texture"])
    assert "제형 축 선호와 일치" in result.summary_ko


def test_non_texture_keyword_normal_explanation():
    """Non-texture keywords should get normal explanation."""
    scored = ScoredProduct(
        product_id="P1",
        raw_score=1.0,
        shrinked_score=0.8,
        final_score=0.8,
        feature_contributions={"keyword_match": 1.0},
    )
    result = explain(scored, ["keyword:SomeOtherKeyword"])
    assert "제형" not in result.summary_ko
    assert "키워드" in result.summary_ko


def test_non_texture_bee_attr_normal_explanation():
    """Non-texture bee_attr should get normal explanation."""
    scored = ScoredProduct(
        product_id="P1",
        raw_score=0.5,
        shrinked_score=0.4,
        final_score=0.4,
        feature_contributions={"residual_bee_attr_match": 0.5},
    )
    result = explain(scored, ["bee_attr:Moisturizing"])
    assert "제형" not in result.summary_ko
    assert "속성 선호와 일치" in result.summary_ko
