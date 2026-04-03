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
