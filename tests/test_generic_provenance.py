"""Tests: generic provenance model consistency."""
from src.user.canonicalize_user_facts import canonicalize_user_facts


def test_user_facts_have_provenance():
    """User-derived facts must include provenance with source_domain='user'."""
    adapted_facts = [
        {
            "predicate": "HAS_SKIN_TYPE",
            "concept_type": "SkinType",
            "concept_value": "oily",
            "concept_id": "concept:SkinType:oily",
            "confidence": 1.0,
            "source": "basic",
        },
    ]
    facts = canonicalize_user_facts("u1", adapted_facts)
    has_provenance = any(f.get("provenance") for f in facts)
    assert has_provenance, "User facts must include provenance dict"


def test_user_provenance_source_domain():
    """User provenance source_domain must be 'user'."""
    adapted_facts = [
        {
            "predicate": "HAS_CONCERN",
            "concept_type": "Concern",
            "concept_value": "acne",
            "concept_id": "concept:Concern:acne",
            "confidence": 0.8,
            "source": "chat",
        },
    ]
    facts = canonicalize_user_facts("u1", adapted_facts)
    for f in facts:
        if f.get("provenance"):
            assert f["provenance"]["source_domain"] == "user"


def test_user_provenance_source_kind_mapping():
    """source_kind should map from source modality."""
    adapted_facts = [
        {
            "predicate": "PREFERS_BRAND",
            "concept_type": "Brand",
            "concept_value": "brand_a",
            "concept_id": "concept:Brand:brand_a",
            "confidence": 0.9,
            "source": "purchase",
        },
    ]
    facts = canonicalize_user_facts("u1", adapted_facts)
    purchase_facts = [f for f in facts if f.get("provenance", {}).get("source_kind") == "derived"]
    assert len(purchase_facts) > 0, "Purchase-derived facts should have source_kind='derived'"


def test_user_provenance_chat_source_kind():
    """Chat-derived facts should have source_kind='summary'."""
    adapted_facts = [
        {
            "predicate": "WANTS_GOAL",
            "concept_type": "Goal",
            "concept_value": "whitening",
            "concept_id": "concept:Goal:whitening",
            "confidence": 0.8,
            "source": "chat",
        },
    ]
    facts = canonicalize_user_facts("u1", adapted_facts)
    for f in facts:
        assert f["provenance"]["source_kind"] == "summary"


def test_user_facts_pass_through_last_seen_at():
    """last_seen_at from adapted facts should be preserved."""
    adapted_facts = [
        {
            "predicate": "PREFERS_BRAND",
            "concept_type": "Brand",
            "concept_value": "brand_a",
            "concept_id": "concept:Brand:brand_a",
            "confidence": 0.9,
            "source": "purchase",
            "last_seen_at": "2025-06-01T00:00:00+00:00",
        },
    ]
    facts = canonicalize_user_facts("u1", adapted_facts)
    assert facts[0]["last_seen_at"] == "2025-06-01T00:00:00+00:00"
