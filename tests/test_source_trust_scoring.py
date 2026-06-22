import pytest

from src.rec.scorer import SCORING_FEATURE_KEYS, Scorer


def _score_with(weights: dict[str, float], product_profile: dict) -> float:
    scorer = Scorer()
    scorer.load_from_dict(weights, shrinkage_k=10)
    product = {"product_id": "P1", "review_count_all": 100, **product_profile}
    return scorer.score({}, product, [])


def test_source_trust_features_are_declared() -> None:
    assert "source_popularity_score" in SCORING_FEATURE_KEYS
    assert "source_rating_score" in SCORING_FEATURE_KEYS


def test_source_popularity_score_is_bounded_and_log_scaled() -> None:
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "source_popularity_score": 1.0,
            "source_rating_score": 0.0,
        },
        shrinkage_k=10,
    )

    low = scorer.score({}, {"product_id": "low", "source_review_count_6m": 5, "review_count_all": 100}, [])
    high = scorer.score({}, {"product_id": "high", "source_review_count_6m": 5000, "review_count_all": 100}, [])

    assert high.raw_score > low.raw_score
    assert high.raw_score <= 1.0
    assert high.feature_contributions["source_popularity_score"] <= 1.0


def test_source_popularity_uses_recent_count_before_all_time_fallback() -> None:
    weights = {"source_popularity_score": 1.0}

    recent_missing = _score_with(weights, {"source_review_count_all": 1000})
    recent_zero = _score_with(
        weights,
        {
            "source_review_count_6m": 0,
            "source_review_count_all": 1000,
        },
    )

    assert recent_missing.raw_score == pytest.approx(1.0)
    assert recent_zero.raw_score == pytest.approx(0.0)


def test_source_rating_score_rewards_high_recent_rating_only() -> None:
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "source_popularity_score": 0.0,
            "source_rating_score": 1.0,
        },
        shrinkage_k=10,
    )

    low = scorer.score({}, {"product_id": "low", "source_avg_rating_6m": 3.9, "review_count_all": 100}, [])
    high = scorer.score({}, {"product_id": "high", "source_avg_rating_6m": 4.8, "review_count_all": 100}, [])
    capped = scorer.score({}, {"product_id": "capped", "source_avg_rating_6m": 5.2, "review_count_all": 100}, [])

    assert low.raw_score == pytest.approx(0.0)
    assert high.raw_score == pytest.approx(0.8)
    assert capped.raw_score == pytest.approx(1.0)


def test_source_rating_uses_recent_rating_before_all_time_fallback() -> None:
    weights = {"source_rating_score": 1.0}

    recent_missing = _score_with(weights, {"source_avg_rating_all": 4.6})
    recent_low = _score_with(
        weights,
        {
            "source_avg_rating_6m": 3.8,
            "source_avg_rating_all": 4.9,
        },
    )

    assert recent_missing.raw_score == pytest.approx(0.6)
    assert recent_low.raw_score == pytest.approx(0.0)


def test_source_trust_does_not_replace_graph_support_shrinkage() -> None:
    scorer = Scorer()
    scorer.load_from_dict({"source_popularity_score": 1.0}, shrinkage_k=10)

    no_graph_support = scorer.score(
        {},
        {"product_id": "p1", "source_review_count_6m": 5000, "review_count_all": 0},
        [],
    )
    graph_supported = scorer.score(
        {},
        {"product_id": "p2", "source_review_count_6m": 5000, "review_count_all": 100},
        [],
    )

    assert no_graph_support.raw_score == graph_supported.raw_score
    assert no_graph_support.shrinked_score < graph_supported.shrinked_score
