"""
P0-1: recommended_to projection contract tests.

Verifies that object=UserSegment facts are projected without qualifier requirement.
"""

import pytest

from src.wrap.projection_registry import ProjectionRegistry, ProjectionResult
from src.wrap.signal_emitter import SignalEmitter
from src.canonical.canonical_fact_builder import CanonicalFact, CanonicalFactBuilder, FactQualifier
from src.common.enums import ObjectRefKind


@pytest.fixture
def registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


class TestRecommendedToProjection:
    def test_user_segment_no_qualifier_produces_signal(self, registry):
        """object=UserSegment, no qualifier → signal generated (not quarantined)."""
        result = registry.project("recommended_to", "Product", "UserSegment")
        assert isinstance(result, ProjectionResult)
        assert result.signal_family == "SEGMENT"
        assert result.edge_type == "RECOMMENDED_TO_SEGMENT_SIGNAL"
        assert result.qualifier_required is False

    def test_user_segment_emitter_produces_signal(self, registry):
        """E2E: UserSegment fact without qualifier → signal emitted."""
        fact = CanonicalFact(
            fact_id="fact:test1",
            review_id="r1",
            subject_iri="product:P1",
            predicate="recommended_to",
            object_iri="concept:UserSegment:oily_skin",
            object_ref_kind=ObjectRefKind.CONCEPT,
            subject_type="Product",
            object_type="UserSegment",
            polarity=None,
        )
        emitter = SignalEmitter(registry)
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        assert sid is not None
        assert emitter.quarantined_count == 0

    def test_non_segment_without_qualifier_quarantined(self, registry):
        """object=Person, no qualifier → depends on registry rule (should not match UserSegment)."""
        result = registry.project("recommended_to", "Product", "Person")
        # No rule for Product→Person recommended_to → QUARANTINE
        assert isinstance(result, str)  # "QUARANTINE" string

    def test_targeted_at_also_no_qualifier(self, registry):
        """targeted_at,Product,UserSegment should also not require qualifier."""
        result = registry.project("targeted_at", "Product", "UserSegment")
        assert isinstance(result, ProjectionResult)
        assert result.qualifier_required is False
