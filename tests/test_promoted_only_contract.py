"""Tests: promoted-only serving contract is enforced across all standard paths."""
from src.mart.build_serving_views import build_serving_product_profile


def _signal(family, dst_id, promoted=True, score=1.0):
    return {
        "canonical_edge_type": family,
        "dst_node_id": dst_id,
        "score": score,
        "is_promoted": promoted,
        "polarity": "POS",
        "review_cnt": 5,
        "window_type": "all",
    }


def test_standard_profile_excludes_non_promoted():
    """Standard serving profile (promoted_only=True) must exclude non-promoted signals."""
    agg_rows = [
        _signal("HAS_BEE_KEYWORD_SIGNAL", "concept:Keyword:kw1", promoted=True),
        _signal("HAS_BEE_KEYWORD_SIGNAL", "concept:Keyword:kw2", promoted=False),
        _signal("HAS_BEE_ATTR_SIGNAL", "concept:Attr:a1", promoted=True),
    ]
    profile = build_serving_product_profile(
        {"product_id": "P001", "brand_id": "b1", "category_id": "c1"},
        agg_rows, promoted_only=True,
    )
    keyword_ids = [k["id"] for k in profile.get("top_keyword_ids", [])]
    assert "concept:Keyword:kw1" in keyword_ids
    assert "concept:Keyword:kw2" not in keyword_ids, "Non-promoted signal must not appear in standard profile"


def test_debug_profile_includes_non_promoted():
    """Debug profile (promoted_only=False) may include non-promoted signals."""
    agg_rows = [
        _signal("HAS_BEE_KEYWORD_SIGNAL", "concept:Keyword:kw1", promoted=True),
        _signal("HAS_BEE_KEYWORD_SIGNAL", "concept:Keyword:kw2", promoted=False),
    ]
    profile = build_serving_product_profile(
        {"product_id": "P001", "brand_id": "b1", "category_id": "c1"},
        agg_rows, promoted_only=False,
    )
    keyword_ids = [k["id"] for k in profile.get("top_keyword_ids", [])]
    assert "concept:Keyword:kw1" in keyword_ids
    assert "concept:Keyword:kw2" in keyword_ids


def test_catalog_validation_excluded_from_standard():
    """CATALOG_VALIDATION_SIGNAL must never appear in standard serving profile."""
    agg_rows = [
        _signal("HAS_BEE_KEYWORD_SIGNAL", "concept:Keyword:kw1", promoted=True),
        _signal("CATALOG_VALIDATION_SIGNAL", "concept:Brand:b1", promoted=True),
    ]
    profile = build_serving_product_profile(
        {"product_id": "P001", "brand_id": "b1", "category_id": "c1"},
        agg_rows, promoted_only=True,
    )
    # Check that catalog validation signal doesn't appear in any signal list
    all_signal_ids = set()
    for key in ("top_keyword_ids", "top_bee_attr_ids", "top_context_ids",
                "top_concern_pos_ids", "top_concern_neg_ids", "top_tool_ids"):
        for item in profile.get(key, []):
            if isinstance(item, dict):
                all_signal_ids.add(item.get("id", ""))
    assert "concept:Brand:b1" not in all_signal_ids, \
        "CATALOG_VALIDATION_SIGNAL must not appear in standard profile"


def test_promoted_only_is_default():
    """build_serving_product_profile must default to promoted_only=True."""
    import inspect
    sig = inspect.signature(build_serving_product_profile)
    param = sig.parameters.get("promoted_only")
    assert param is not None, "promoted_only parameter must exist"
    assert param.default is True, "promoted_only must default to True (standard serving contract)"


def test_recommend_path_uses_only_promoted_signals():
    """Full recommend path: non-promoted signals must not contribute to scoring."""
    from src.rec.candidate_generator import generate_candidates
    from src.rec.scorer import Scorer
    from src.common.enums import RecommendationMode

    # Build a product profile where ALL signals are non-promoted
    # (simulates what build_serving_product_profile would produce with promoted_only=False)
    product_non_promoted = {
        "product_id": "P_NONPROM",
        "brand_id": "b1", "brand_name": "B",
        "category_id": "c1", "category_name": "C",
        "variant_family_id": None,
        "price": 10000,
        "ingredient_concept_ids": [],
        "category_concept_ids": ["concept:Category:c1"],
        "brand_concept_ids": ["concept:Brand:b1"],
        "main_benefit_concept_ids": [],
        # Signal lists are empty because promoted_only=True filtered everything
        "top_bee_attr_ids": [],
        "top_keyword_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_comparison_product_ids": [],
        "top_coused_product_ids": [],
        "review_count_30d": 0,
        "review_count_90d": 0,
        "review_count_all": 0,
        "last_signal_at": None,
    }

    # Product with promoted signals
    product_promoted = dict(product_non_promoted)
    product_promoted["product_id"] = "P_PROM"
    product_promoted["top_keyword_ids"] = [
        {"id": "concept:Keyword:kw1", "score": 1.0, "review_cnt": 5},
    ]
    product_promoted["review_count_all"] = 50

    user = {
        "user_id": "u1",
        "skin_type": None,
        "owned_product_ids": [],
        "owned_family_ids": [],
        "repurchased_family_ids": [],
        "preferred_brand_ids": [{"id": "concept:Brand:b1", "weight": 0.8}],
        "preferred_category_ids": [{"id": "concept:Category:c1", "weight": 0.8}],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "goal_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_keyword_ids": [{"id": "concept:Keyword:kw1", "weight": 0.8}],
        "preferred_context_ids": [],
        "recent_purchase_brand_ids": [],
        "repurchase_brand_ids": [],
    }

    candidates = generate_candidates(
        user, [product_non_promoted, product_promoted],
        mode=RecommendationMode.EXPLORE,
    )
    scorer = Scorer()
    scorer.load_config()

    scores = {}
    for c in candidates:
        product = product_promoted if c.product_id == "P_PROM" else product_non_promoted
        s = scorer.score(user, product, c.overlap_concepts)
        scores[c.product_id] = s

    # Product with promoted signals should score higher
    assert scores["P_PROM"].raw_score > scores["P_NONPROM"].raw_score, \
        "Product with promoted signals must score higher than one without"
    # Non-promoted product should have minimal signal-based contributions
    # (only brand/category match from truth, no keyword/attr/context/concern)
    non_prom_contribs = scores["P_NONPROM"].feature_contributions
    assert "keyword_match" not in non_prom_contribs, \
        "Non-promoted product should have no keyword_match contribution"
