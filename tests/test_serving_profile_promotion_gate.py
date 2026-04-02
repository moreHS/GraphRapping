"""Tests: promoted-only signal gate in serving profiles.

Complements test_serving_uses_promoted_only.py with catalog_validation
defense-in-depth checks and pipeline _agg_to_dict round-trip verification.
"""
from src.mart.build_serving_views import build_serving_product_profile


def _make_signals():
    """Mix of promoted and non-promoted signals."""
    return [
        {"canonical_edge_type": "HAS_BEE_ATTR_SIGNAL", "dst_node_id": "attr_1",
         "score": 0.8, "review_cnt": 5, "window_type": "all", "is_promoted": True, "last_seen_at": None},
        {"canonical_edge_type": "HAS_BEE_ATTR_SIGNAL", "dst_node_id": "attr_2",
         "score": 0.9, "review_cnt": 2, "window_type": "all", "is_promoted": False, "last_seen_at": None},
        {"canonical_edge_type": "HAS_BEE_KEYWORD_SIGNAL", "dst_node_id": "kw_1",
         "score": 0.7, "review_cnt": 4, "window_type": "all", "is_promoted": True, "last_seen_at": None},
    ]


def _make_master():
    return {"product_id": "P001", "brand_id": "laneige", "brand_name": "Laneige",
            "category_id": "essence", "category_name": "Essence",
            "country_of_origin": None, "price": 39000, "ingredients": []}


def test_promoted_only_excludes_non_promoted():
    profile = build_serving_product_profile(_make_master(), _make_signals())
    attr_ids = [s["id"] for s in profile["top_bee_attr_ids"]]
    assert "attr_1" in attr_ids
    assert "attr_2" not in attr_ids  # non-promoted excluded


def test_debug_mode_includes_non_promoted():
    profile = build_serving_product_profile(_make_master(), _make_signals(), promoted_only=False)
    attr_ids = [s["id"] for s in profile["top_bee_attr_ids"]]
    assert "attr_1" in attr_ids
    assert "attr_2" in attr_ids  # debug mode includes all


def test_catalog_validation_excluded_even_if_promoted():
    signals = _make_signals() + [
        {"canonical_edge_type": "CATALOG_VALIDATION_SIGNAL", "dst_node_id": "cv_1",
         "score": 1.0, "review_cnt": 10, "window_type": "all", "is_promoted": True, "last_seen_at": None},
    ]
    profile = build_serving_product_profile(_make_master(), signals)
    # catalog_validation should not appear anywhere in the profile signals
    all_ids = []
    for key in profile:
        if key.startswith("top_") and isinstance(profile[key], list):
            all_ids.extend(s.get("id", "") for s in profile[key] if isinstance(s, dict))
    assert "cv_1" not in all_ids


def test_agg_to_dict_round_trip_preserves_is_promoted():
    """Verify that _agg_to_dict in the pipelines preserves is_promoted.

    This is a regression test for the bug where _agg_to_dict omitted
    is_promoted, causing all signals to be filtered out under promoted_only=True.
    """
    from src.jobs.run_daily_pipeline import _agg_to_dict as daily_agg_to_dict

    from src.mart.aggregate_product_signals import AggProductSignalRow

    row = AggProductSignalRow(
        target_product_id="P001",
        canonical_edge_type="HAS_BEE_ATTR_SIGNAL",
        dst_node_type="BEEAttr",
        dst_node_id="moisture",
        window_type="all",
        review_cnt=5,
        pos_cnt=4,
        neg_cnt=0,
        neu_cnt=1,
        support_count=5,
        score=0.8,
        recent_score=None,
        recent_support_count=None,
        last_seen_at="2025-01-01",
        window_start=None,
        window_end="2025-03-01",
        evidence_sample=None,
        distinct_review_count=5,
        avg_confidence=0.9,
        synthetic_ratio=0.0,
        corpus_weight=3.0,
        is_promoted=True,
    )

    d = daily_agg_to_dict(row)
    assert "is_promoted" in d, "_agg_to_dict must include is_promoted"
    assert d["is_promoted"] is True

    # Verify the dict works correctly with build_serving_product_profile
    profile = build_serving_product_profile(_make_master(), [d])
    attr_ids = [s["id"] for s in profile["top_bee_attr_ids"]]
    assert "moisture" in attr_ids, "promoted signal must appear in serving profile"


def test_keyword_signals_respect_promotion_gate():
    """Keyword signals must also respect promoted_only filtering."""
    signals = [
        {"canonical_edge_type": "HAS_BEE_KEYWORD_SIGNAL", "dst_node_id": "kw_promoted",
         "score": 0.9, "review_cnt": 5, "window_type": "all", "is_promoted": True, "last_seen_at": None},
        {"canonical_edge_type": "HAS_BEE_KEYWORD_SIGNAL", "dst_node_id": "kw_not_promoted",
         "score": 0.95, "review_cnt": 1, "window_type": "all", "is_promoted": False, "last_seen_at": None},
    ]
    profile = build_serving_product_profile(_make_master(), signals)
    kw_ids = [s["id"] for s in profile["top_keyword_ids"]]
    assert "kw_promoted" in kw_ids
    assert "kw_not_promoted" not in kw_ids
