"""Tests: catalog_validation is excluded from recommendation path."""
from src.mart.build_serving_views import build_serving_product_profile
from src.rec.explainer import explain, _EDGE_MAP, _concept_to_feature
from src.rec.scorer import ScoredProduct


def test_serving_profile_excludes_catalog_validation():
    """catalog_validation signals must not appear in serving profile top_* fields."""
    signals = [
        {
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr",
            "dst_node_id": "moisture",
            "window_type": "all",
            "score": 0.8,
            "review_cnt": 5,
            "is_promoted": True,
        },
        {
            "canonical_edge_type": "CATALOG_VALIDATION_SIGNAL",
            "dst_node_type": "Brand",
            "dst_node_id": "brand_x",
            "window_type": "all",
            "score": 1.0,
            "review_cnt": 10,
            "is_promoted": True,
        },
    ]
    profile = build_serving_product_profile(
        {"product_id": "p1"}, signals, promoted_only=False,
    )
    # catalog_validation should be filtered out even when promoted
    all_signal_ids = []
    for key in ["top_bee_attr_ids", "top_keyword_ids", "top_context_ids",
                "top_concern_pos_ids", "top_concern_neg_ids", "top_tool_ids"]:
        all_signal_ids.extend([s["id"] for s in profile.get(key, [])])
    assert "brand_x" not in all_signal_ids


def test_explainer_skips_catalog_validation():
    """explain() must skip catalog_validation concepts."""
    scored = ScoredProduct(
        product_id="p1",
        raw_score=0.8,
        shrinked_score=0.8,
        final_score=0.8,
        feature_contributions={"keyword_match": 0.3},
    )
    # catalog_validation concept should be silently skipped
    result = explain(scored, ["catalog_validation:brand_x", "keyword:hydrating"])
    concept_types = [p.concept_type for p in result.paths]
    assert "catalog_validation" not in concept_types


def test_explainer_goal_split_recognized():
    """Explainer must recognize goal_master and goal_review concept types."""
    assert "goal_master" in _EDGE_MAP
    assert "goal_review" in _EDGE_MAP
    assert _concept_to_feature("goal_master") == "goal_fit_master"
    assert _concept_to_feature("goal_review") == "goal_fit_review_signal"


def test_explainer_goal_master_path():
    """goal_master overlap should produce an explanation path."""
    scored = ScoredProduct(
        product_id="p1",
        raw_score=0.8,
        shrinked_score=0.8,
        final_score=0.8,
        feature_contributions={"goal_fit_master": 0.2, "keyword_match": 0.1},
    )
    result = explain(scored, ["goal_master:whitening", "keyword:hydrating"])
    types = [p.concept_type for p in result.paths]
    assert "goal_master" in types
