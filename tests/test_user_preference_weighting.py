"""Tests: user preference weighting with recency/frequency/source_type."""
from datetime import datetime, timezone, timedelta

from src.mart.aggregate_user_preferences import refresh_user_preferences


def test_frequency_boost():
    """Repeated signals for same concept should increase weight."""
    facts_single = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["chat"], "object_type": "Brand"},
    ]
    facts_triple = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["chat"], "object_type": "Brand"},
    ] * 3

    result_single = refresh_user_preferences("u1", facts_single)
    result_triple = refresh_user_preferences("u1", facts_triple)

    w_single = result_single[0]["weight"]
    w_triple = result_triple[0]["weight"]
    assert w_triple > w_single, "Repeated signals should have higher weight"


def test_recency_decay():
    """Old signals should have lower weight than recent ones."""
    now = datetime(2025, 6, 1, tzinfo=timezone.utc)
    facts_recent = [
        {"predicate": "HAS_CONCERN", "object_iri": "acne", "confidence": 0.9,
         "source_modalities": ["chat"], "object_type": "Concern",
         "last_seen_at": (now - timedelta(days=1)).isoformat()},
    ]
    facts_old = [
        {"predicate": "HAS_CONCERN", "object_iri": "acne", "confidence": 0.9,
         "source_modalities": ["chat"], "object_type": "Concern",
         "last_seen_at": (now - timedelta(days=90)).isoformat()},
    ]

    result_recent = refresh_user_preferences("u1", facts_recent, now=now)
    result_old = refresh_user_preferences("u1", facts_old, now=now)

    assert result_recent[0]["weight"] > result_old[0]["weight"]


def test_source_type_weight():
    """Purchase-derived preferences should weigh more than basic ones."""
    facts_purchase = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["purchase"], "object_type": "Brand"},
    ]
    facts_basic = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["basic"], "object_type": "Brand"},
    ]

    result_purchase = refresh_user_preferences("u1", facts_purchase)
    result_basic = refresh_user_preferences("u1", facts_basic)

    assert result_purchase[0]["weight"] > result_basic[0]["weight"]


def test_purchase_boost_extends_to_category():
    """Purchase brand confidence boost should also apply to PREFERS_CATEGORY."""
    facts = [
        {"predicate": "PREFERS_CATEGORY", "object_iri": "skincare", "confidence": 0.5,
         "source_modalities": ["chat"], "object_type": "Category"},
    ]
    purchase_conf = {"skincare": 0.95}

    result = refresh_user_preferences("u1", facts, purchase_brand_confidence=purchase_conf)
    # With purchase boost: max_confidence=0.95, freq=1/3, recency=1.0, source_weight=1.2 (purchase)
    # weight = 0.95 * (1/3) * 1.0 * 1.2 ≈ 0.38
    assert result[0]["weight"] > 0.3  # boosted above what 0.5 confidence alone would give
    # Verify the purchase source was added
    assert "purchase" in result[0]["source_mix"]


def test_backward_compat_no_last_seen():
    """Facts without last_seen_at should still aggregate with recency_factor=1.0."""
    facts = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["chat"], "object_type": "Brand"},
    ]
    result = refresh_user_preferences("u1", facts)
    assert result[0]["recency_weight"] == 1.0


def test_source_mix_includes_weights():
    """source_mix should include per-source weights."""
    facts = [
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.8,
         "source_modalities": ["purchase"], "object_type": "Brand"},
        {"predicate": "PREFERS_BRAND", "object_iri": "brand_a", "confidence": 0.6,
         "source_modalities": ["chat"], "object_type": "Brand"},
    ]
    result = refresh_user_preferences("u1", facts)
    sm = result[0]["source_mix"]
    assert "purchase" in sm
    assert "chat" in sm
