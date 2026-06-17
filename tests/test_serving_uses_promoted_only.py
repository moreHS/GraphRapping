"""Tests: serving product profile respects corpus promotion."""
from src.mart.build_serving_views import build_serving_product_profile


def _make_agg_signal(edge_type, dst_id, score, review_cnt, window_type="all", is_promoted=True,
                     review_ids=None):
    return {
        "canonical_edge_type": edge_type,
        "dst_node_type": "BEEAttr",
        "dst_node_id": dst_id,
        "window_type": window_type,
        "score": score,
        "review_cnt": review_cnt,
        # P3-7: transient review_ids drive product-level distinct review count
        # in build_serving. Default = synthesize unique ids per signal so tests
        # that don't care about overlap still count to review_cnt.
        "review_ids": review_ids if review_ids is not None
                      else [f"{dst_id}_r{i}" for i in range(review_cnt)],
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


def test_missing_source_review_stats_remain_none():
    """Absent source stats must not become fake source-grounded zeros."""
    profile = build_serving_product_profile(_make_product_master(), [])

    assert profile["source_review_count_6m"] is None
    assert profile["source_review_score_count_6m"] is None
    assert profile["source_review_count_all"] is None
    assert profile["source_review_score_count_all"] is None


def test_explicit_zero_source_review_stats_are_preserved():
    """Zero is valid only when an actual source stats row supplies it."""
    profile = build_serving_product_profile(
        _make_product_master(),
        [],
        source_review_stats={
            "source_review_count_6m": 0,
            "source_review_score_count_6m": 0,
            "source_review_count_all": 0,
            "source_review_score_count_all": 0,
            "source": "snowflake:f_prd_rv_hist",
        },
    )

    assert profile["source_review_count_6m"] == 0
    assert profile["source_review_score_count_6m"] == 0
    assert profile["source_review_count_all"] == 0
    assert profile["source_review_score_count_all"] == 0
    assert profile["source_review_stats_source"] == "snowflake:f_prd_rv_hist"


def test_source_review_stats_do_not_redefine_graph_support_counts():
    """Source volume/rating is exposed separately from graph review support."""
    signals = [
        _make_agg_signal(
            "HAS_BEE_ATTR_SIGNAL",
            "m1",
            0.8,
            2,
            window_type="all",
            is_promoted=True,
            review_ids=["r1", "r2"],
        ),
    ]
    profile = build_serving_product_profile(
        _make_product_master(),
        signals,
        source_review_stats={
            "source_product_id": "p1",
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_review_count_6m": 120,
            "source_review_score_count_6m": 100,
            "source_avg_rating_6m": 4.5,
            "source_review_count_all": 300,
            "source_review_score_count_all": 250,
            "source_avg_rating_all": 4.3,
            "source": "snowflake:f_prd_rv_hist",
        },
    )

    assert profile["review_count_all"] == 2
    assert profile["signal_support_count_all"] == 2
    assert profile["source_product_id"] == "p1"
    assert profile["source_review_count_6m"] == 120
    assert profile["source_review_score_count_6m"] == 100
    assert profile["source_avg_rating_6m"] == 4.5
    assert profile["source_review_count_all"] == 300
    assert profile["source_review_score_count_all"] == 250
    assert profile["source_avg_rating_all"] == 4.3
    assert profile["source_review_stats_source"] == "snowflake:f_prd_rv_hist"
