"""
P3-6 + P3-7 (Wave 2.9): promotion semantics restructure.

- P3-6: `avg_confidence` is computed from fact-level `source_confidence`
        (not transformed `weight`).
- P3-7: `review_count_all`/`30d`/`90d` are product-level distinct review_id
        counts (union across signal rows). `signal_support_count_all` is
        the legacy "sum of review_cnt" exposed explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.mart.aggregate_product_signals import (
    aggregate_product_signals,
)
from src.mart.build_serving_views import build_serving_product_profile


_NOW = datetime.now(timezone.utc)
_RECENT = _NOW.isoformat()


def _sig(*, review_id: str, source_confidence: float | None = 0.7,
         weight: float = 1.0, evidence_kind: str | None = None) -> dict:
    return {
        "target_product_id": "p1",
        "edge_type": "HAS_BEE_ATTR_SIGNAL",
        "dst_type": "BEEAttr",
        "dst_id": "moisture",
        "polarity": "POS",
        "review_id": review_id,
        "window_ts": _RECENT,
        "weight": weight,
        "source_confidence": source_confidence,
        "evidence_kind": evidence_kind,
        "signal_family": "BEE_ATTR",
    }


# ---------------------------------------------------------------------------
# P3-6: avg_confidence reflects source_confidence, not weight
# ---------------------------------------------------------------------------

def test_avg_confidence_from_source_confidence_not_weight() -> None:
    """avg_confidence must use source_confidence, regardless of weight."""
    signals = [
        _sig(review_id="r1", source_confidence=0.8, weight=0.3),
        _sig(review_id="r2", source_confidence=0.8, weight=0.3),
        _sig(review_id="r3", source_confidence=0.8, weight=0.3),
    ]
    rows = aggregate_product_signals(signals, now=_NOW)
    rows_all = [r for r in rows if r.window_type == "all"]
    assert rows_all
    # avg_confidence ≈ 0.8 (source), not 0.3 (weight)
    for r in rows_all:
        assert abs(r.avg_confidence - 0.8) < 0.001, \
            f"avg_confidence={r.avg_confidence} should be ~0.8 (source_confidence), not weight"


def test_missing_source_confidence_excluded_from_average() -> None:
    """Signals without source_confidence are excluded — avg over the rest."""
    signals = [
        _sig(review_id="r1", source_confidence=0.8),
        _sig(review_id="r2", source_confidence=None),
        _sig(review_id="r3", source_confidence=0.8),
    ]
    rows = aggregate_product_signals(signals, now=_NOW)
    rows_all = [r for r in rows if r.window_type == "all"]
    assert rows_all
    for r in rows_all:
        assert abs(r.avg_confidence - 0.8) < 0.001, \
            f"avg should ignore None entries: got {r.avg_confidence}"


def test_all_missing_source_confidence_yields_zero_blocks_promotion() -> None:
    """If no signal has source_confidence, avg=0 and promotion is blocked."""
    signals = [
        _sig(review_id="r1", source_confidence=None),
        _sig(review_id="r2", source_confidence=None),
        _sig(review_id="r3", source_confidence=None),
    ]
    rows = aggregate_product_signals(signals, now=_NOW)
    rows_all = [r for r in rows if r.window_type == "all"]
    assert rows_all
    for r in rows_all:
        assert r.avg_confidence == 0.0
        assert not r.is_promoted, \
            "no source_confidence on any signal must block promotion"


# ---------------------------------------------------------------------------
# P3-7: review_count_all is product-level distinct, signal_support_count_all
#       is the legacy sum.
# ---------------------------------------------------------------------------

def _agg_row(*, edge_type: str, dst_id: str, review_ids: list[str],
             window_type: str = "all", is_promoted: bool = True) -> dict:
    return {
        "canonical_edge_type": edge_type,
        "dst_node_type": "BEEAttr",
        "dst_node_id": dst_id,
        "window_type": window_type,
        "score": 0.8,
        "review_cnt": len(review_ids),
        "review_ids": review_ids,
        "last_seen_at": "2025-01-01",
        "is_promoted": is_promoted,
    }


def _master() -> dict:
    return {"product_id": "p1", "brand_id": "b1", "brand_name": "B1"}


def test_review_count_all_unions_review_ids_no_double_count() -> None:
    """Same reviewer contributing to two edges counts once at product level."""
    signals = [
        _agg_row(edge_type="HAS_BEE_ATTR_SIGNAL", dst_id="moisture",
                 review_ids=["rA", "rB"]),
        _agg_row(edge_type="HAS_BEE_KEYWORD_SIGNAL", dst_id="dewy",
                 review_ids=["rB", "rC"]),
    ]
    profile = build_serving_product_profile(_master(), signals)
    # Union: {rA, rB, rC} = 3 distinct, NOT 4 (the prior inflated sum)
    assert profile["review_count_all"] == 3, \
        f"distinct review count = 3 (rA, rB, rC); got {profile['review_count_all']}"


def test_signal_support_count_all_is_legacy_sum() -> None:
    """signal_support_count_all exposes the prior inflated sum-of-review_cnt."""
    signals = [
        _agg_row(edge_type="HAS_BEE_ATTR_SIGNAL", dst_id="moisture",
                 review_ids=["rA", "rB"]),
        _agg_row(edge_type="HAS_BEE_KEYWORD_SIGNAL", dst_id="dewy",
                 review_ids=["rB", "rC"]),
    ]
    profile = build_serving_product_profile(_master(), signals)
    # Sum: 2 + 2 = 4, even though distinct reviewers is 3.
    assert profile["signal_support_count_all"] == 4, \
        f"signal_support_count_all = sum(review_cnt) = 4; got {profile['signal_support_count_all']}"


def test_window_review_counts_use_union_within_window() -> None:
    """30d/90d distinct counts respect their window filter."""
    signals = [
        _agg_row(edge_type="HAS_BEE_ATTR_SIGNAL", dst_id="moisture",
                 review_ids=["rA", "rB"], window_type="30d"),
        _agg_row(edge_type="HAS_BEE_KEYWORD_SIGNAL", dst_id="dewy",
                 review_ids=["rB"], window_type="30d"),
        _agg_row(edge_type="HAS_BEE_ATTR_SIGNAL", dst_id="moisture",
                 review_ids=["rX"], window_type="90d"),
    ]
    profile = build_serving_product_profile(_master(), signals)
    assert profile["review_count_30d"] == 2, "30d distinct = {rA, rB}"
    assert profile["review_count_90d"] == 1, "90d distinct = {rX}"


def test_empty_signals_yield_zero_counts() -> None:
    profile = build_serving_product_profile(_master(), [])
    assert profile["review_count_all"] == 0
    assert profile["review_count_30d"] == 0
    assert profile["review_count_90d"] == 0
    assert profile["signal_support_count_all"] == 0


def test_aggregate_populates_review_ids() -> None:
    """aggregate_product_signals must populate review_ids on each row."""
    signals = [
        _sig(review_id="r1", source_confidence=0.7),
        _sig(review_id="r2", source_confidence=0.7),
        _sig(review_id="r1", source_confidence=0.7),  # duplicate review
    ]
    rows = aggregate_product_signals(signals, now=_NOW)
    for r in rows:
        # set semantics — deduplicated and sorted
        assert sorted(r.review_ids) == ["r1", "r2"], \
            f"review_ids should be deduplicated: got {r.review_ids}"
        assert r.distinct_review_count == 2


# ---------------------------------------------------------------------------
# End-to-end: P3-6 and P3-7 together
# ---------------------------------------------------------------------------

def test_null_or_empty_review_id_excluded_from_distinct_count() -> None:
    """Legacy DB rows can carry NULL/empty review_id (DDL nullable).

    Codex regression catch: distinct_review_count must match what review_ids
    transients carry — neither can silently include empty strings.
    """
    signals = [
        _sig(review_id="r1", source_confidence=0.7),
        _sig(review_id="", source_confidence=0.7),  # legacy empty
        _sig(review_id="r2", source_confidence=0.7),
    ]
    rows = aggregate_product_signals(signals, now=_NOW)
    for r in rows:
        assert r.distinct_review_count == len(r.review_ids), (
            f"divergence: distinct_review_count={r.distinct_review_count} "
            f"vs len(review_ids)={len(r.review_ids)}"
        )
        assert "" not in r.review_ids, "empty review_id leaked into row.review_ids"
        assert sorted(r.review_ids) == ["r1", "r2"]


def test_e2e_p3_6_p3_7_combined() -> None:
    """Round-trip: aggregate → build_serving propagates both new metrics."""
    signals = [
        _sig(review_id="r1", source_confidence=0.7),
        _sig(review_id="r2", source_confidence=0.7),
        _sig(review_id="r3", source_confidence=0.7),
    ]
    agg_rows = aggregate_product_signals(signals, now=_NOW)
    agg_dicts = []
    for r in agg_rows:
        agg_dicts.append({
            "canonical_edge_type": r.canonical_edge_type,
            "dst_node_type": r.dst_node_type,
            "dst_node_id": r.dst_node_id,
            "window_type": r.window_type,
            "score": r.score,
            "review_cnt": r.review_cnt,
            "review_ids": r.review_ids,
            "last_seen_at": r.last_seen_at,
            "is_promoted": r.is_promoted,
        })
    profile = build_serving_product_profile(_master(), agg_dicts)
    # All three windows contain the same 3 reviewers, but build_serving
    # filters by window_type="all" by default.
    assert profile["review_count_all"] == 3
    assert profile["signal_support_count_all"] >= 3
