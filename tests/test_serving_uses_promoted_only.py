"""Tests: serving product profile respects corpus promotion."""
from src.mart.build_serving_views import build_serving_product_profile


def _make_agg_signal(edge_type, dst_id, score, review_cnt, window_type="all", is_promoted=True):
    return {
        "canonical_edge_type": edge_type,
        "dst_node_type": "BEEAttr",
        "dst_node_id": dst_id,
        "window_type": window_type,
        "score": score,
        "review_cnt": review_cnt,
        "last_seen_at": "2025-01-01",
        "is_promoted": is_promoted,
    }


def _make_product_master():
    return {
        "product_id": "p1",
        "brand_id": "b1",
        "brand_name": "TestBrand",
        "category_id": "c1",
        "category_name": "Skincare",
    }


def test_promoted_only_filters_unpromoted():
    """Only promoted signals should appear in top_* fields by default."""
    signals = [
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "moisture", 0.8, 5, is_promoted=True),
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "sticky", 0.9, 1, is_promoted=False),
    ]
    profile = build_serving_product_profile(_make_product_master(), signals)
    ids = [item["id"] for item in profile["top_bee_attr_ids"]]
    assert "moisture" in ids
    assert "sticky" not in ids


def test_promoted_only_false_includes_all():
    """Debug mode: all signals included when promoted_only=False."""
    signals = [
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "moisture", 0.8, 5, is_promoted=True),
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "sticky", 0.9, 1, is_promoted=False),
    ]
    profile = build_serving_product_profile(_make_product_master(), signals, promoted_only=False)
    ids = [item["id"] for item in profile["top_bee_attr_ids"]]
    assert "moisture" in ids
    assert "sticky" in ids


def test_empty_promoted_returns_empty_signals():
    """When no signals are promoted, truth profile is preserved but signal lists are empty."""
    signals = [
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "sticky", 0.9, 1, is_promoted=False),
    ]
    profile = build_serving_product_profile(_make_product_master(), signals)
    assert profile["product_id"] == "p1"
    assert profile["brand_id"] == "b1"
    assert profile["top_bee_attr_ids"] == []


def test_freshness_counts_respect_promotion():
    """Review counts for freshness must only count promoted signals."""
    signals = [
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "m1", 0.8, 10, window_type="30d", is_promoted=True),
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "m2", 0.5, 20, window_type="30d", is_promoted=False),
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "m3", 0.6, 15, window_type="90d", is_promoted=True),
        _make_agg_signal("HAS_BEE_ATTR_SIGNAL", "m4", 0.3, 30, window_type="90d", is_promoted=False),
    ]
    profile = build_serving_product_profile(_make_product_master(), signals)
    assert profile["review_count_30d"] == 10  # only promoted
    assert profile["review_count_90d"] == 15  # only promoted
