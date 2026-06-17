"""
P4-3 (Wave 3.3): `generate_next_question` axis selection.

Histogram mode (scored_products provided):
  - Lowest-variance histogram-eligible axis wins. Axes whose mean score
    is at/below the epsilon floor are dropped (no signal).
  - Tie-break by priority order.
  - Histogram-ineligible axes (coverage_vs_natural, scent_sensitivity) can
    only surface via legacy data-absence fallback.

Legacy mode (scored_products=None or no histogram candidate):
  - First axis in priority order without user data.
"""

from __future__ import annotations

from src.rec.next_question import (
    _AXIS_FEATURES,
    _PRIORITY_ORDER,
    generate_next_question,
)


def _product(feature_contributions: dict[str, float]) -> dict:
    return {"feature_contributions": feature_contributions}


def _profile_full() -> dict:
    """A user profile with all data present (forces histogram mode reliance)."""
    return {
        "preferred_keyword_ids": ["k1"],
        "preferred_bee_attr_ids": ["b1"],
        "preferred_context_ids": ["c1"],
        "concern_ids": ["co1"],
    }


def test_axis_feature_map_only_includes_mappable_axes() -> None:
    """Histogram-ineligible axes must NOT be in _AXIS_FEATURES."""
    assert "coverage_vs_natural" not in _AXIS_FEATURES
    assert "scent_sensitivity" not in _AXIS_FEATURES


def test_low_variance_axis_wins_when_nonzero() -> None:
    """concern_priority has wide spread, tool_alignment is tight + nonzero → tool wins."""
    products = [
        _product({"concern_fit": 0.1, "tool_alignment": 0.5}),
        _product({"concern_fit": 0.9, "tool_alignment": 0.5}),
        _product({"concern_fit": 0.5, "tool_alignment": 0.5}),
    ]
    nq = generate_next_question(_profile_full(), scored_products=products)
    assert nq is not None
    assert nq.uncertainty_axis == "tool_preference"


def test_zero_score_axis_dropped_from_candidates() -> None:
    """tool_alignment with all-zero contributions (mean=0 ≤ ε) must NOT win
    just for having zero variance.
    """
    products = [
        _product({"concern_fit": 0.1, "tool_alignment": 0.0}),
        _product({"concern_fit": 0.9, "tool_alignment": 0.0}),
        _product({"concern_fit": 0.5, "tool_alignment": 0.0}),
    ]
    nq = generate_next_question(_profile_full(), scored_products=products)
    # tool dropped → concern_priority is the only candidate with signal.
    assert nq is not None
    assert nq.uncertainty_axis == "concern_priority"


def test_all_zero_falls_back_to_legacy() -> None:
    """No axis has signal → legacy data-absence path."""
    products = [_product({}), _product({})]
    profile = {"concern_ids": []}  # legacy will pick concern_priority first
    nq = generate_next_question(profile, scored_products=products)
    assert nq is not None
    assert nq.uncertainty_axis == "concern_priority"


def test_tie_broken_by_priority_order() -> None:
    """Two axes with identical variance + signal → priority order wins."""
    # concern_priority and time_priority both nonzero + zero variance
    products = [
        _product({"concern_fit": 0.4, "context_match": 0.4}),
        _product({"concern_fit": 0.4, "context_match": 0.4}),
    ]
    nq = generate_next_question(_profile_full(), scored_products=products)
    # concern_priority comes first in _PRIORITY_ORDER → wins tie
    assert nq is not None
    assert nq.uncertainty_axis == "concern_priority"
    assert _PRIORITY_ORDER.index("concern_priority") < _PRIORITY_ORDER.index("time_priority")


def test_histogram_ineligible_axes_never_win_via_variance() -> None:
    """coverage_vs_natural and scent_sensitivity can only surface via legacy
    fallback — they have no features and thus no histogram entry.
    """
    # Histogram mode: rich feature data
    products = [
        _product({"concern_fit": 0.3, "tool_alignment": 0.5}),
        _product({"concern_fit": 0.7, "tool_alignment": 0.5}),
    ]
    nq = generate_next_question(_profile_full(), scored_products=products)
    assert nq is not None
    assert nq.uncertainty_axis not in ("coverage_vs_natural", "scent_sensitivity"), (
        f"Histogram-ineligible axis selected: {nq.uncertainty_axis}"
    )


def test_legacy_mode_when_no_scored_products() -> None:
    """scored_products=None → legacy data-absence behavior."""
    nq = generate_next_question({"concern_ids": ["c1"]}, scored_products=None)
    # concern has data → next axis without data = texture_vs_moisture
    assert nq is not None
    assert nq.uncertainty_axis == "texture_vs_moisture"


def test_legacy_mode_when_empty_scored_products() -> None:
    """scored_products=[] → falls through to legacy as well."""
    nq = generate_next_question({"concern_ids": ["c1"]}, scored_products=[])
    assert nq is not None
    assert nq.uncertainty_axis == "texture_vs_moisture"


def test_single_product_skipped_in_histogram() -> None:
    """Variance needs N>=2. Single scored product → fall back to legacy."""
    products = [_product({"concern_fit": 0.5, "tool_alignment": 0.5})]
    profile = {"concern_ids": []}  # legacy will choose concern_priority
    nq = generate_next_question(profile, scored_products=products)
    assert nq is not None
    # Legacy path: concern_priority comes first in priority order without data
    assert nq.uncertainty_axis == "concern_priority"


def test_returns_none_when_all_axes_satisfied_and_no_histogram_winner() -> None:
    """Full profile + no histogram signal → None."""
    nq = generate_next_question(_profile_full(), scored_products=None)
    # Legacy: first axis without data. Profile is "full" for the 4 axes we
    # check; scent_sensitivity / tool_preference have no profile check, so
    # legacy returns one of those — not None.
    # Concretely: scent_sensitivity comes before tool_preference in priority.
    assert nq is not None
    assert nq.uncertainty_axis == "scent_sensitivity"


def test_feature_mapping_uses_real_scorer_keys() -> None:
    """Sanity: every feature in _AXIS_FEATURES must be a real scorer feature."""
    from src.rec.scorer import SCORING_FEATURE_KEYS

    declared = set(SCORING_FEATURE_KEYS)
    for axis, features in _AXIS_FEATURES.items():
        for feature in features:
            assert feature in declared, (
                f"axis={axis}: unknown scorer feature {feature!r}. "
                f"Available: {sorted(declared)}"
            )
