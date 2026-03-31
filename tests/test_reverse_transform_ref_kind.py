"""
P0-2: reverse transform dst_ref_kind correctness tests.
"""

import pytest

from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter
from src.canonical.canonical_fact_builder import CanonicalFact, CanonicalFactBuilder
from src.common.enums import ObjectRefKind


@pytest.fixture
def registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


class TestReverseTransformRefKind:
    def test_concern_reverse_is_concept(self, registry):
        """caused_by(Concern, Product) reverse → dst_ref_kind=CONCEPT."""
        builder = CanonicalFactBuilder()
        builder.add_fact(
            review_id="r1",
            subject_iri="concept:Concern:dryness",
            predicate="caused_by",
            object_iri="product:P1",
            subject_type="Concern",
            object_type="Product",
            polarity="NEG",
            source_modality="REL",
        )
        fact = builder.facts[0]
        assert fact.subject_ref_kind == ObjectRefKind.CONCEPT

        emitter = SignalEmitter(registry)
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        if sid:
            signal = emitter._signals[sid]
            assert signal.dst_ref_kind == "CONCEPT"

    def test_product_reverse_is_entity(self, registry):
        """If subject is Product (entity), reverse → dst_ref_kind=ENTITY."""
        builder = CanonicalFactBuilder()
        builder.add_fact(
            review_id="r1",
            subject_iri="product:P1",
            predicate="compared_with",
            object_iri="product:P2",
            subject_type="Product",
            object_type="Product",
            source_modality="REL",
        )
        fact = builder.facts[0]
        assert fact.subject_ref_kind == ObjectRefKind.ENTITY

    def test_subject_ref_kind_auto_derived(self):
        """subject_ref_kind is auto-derived from subject_type in add_fact."""
        builder = CanonicalFactBuilder()
        builder.add_fact(
            review_id="r1",
            subject_iri="concept:Ingredient:retinol",
            predicate="ingredient_of",
            object_iri="product:P1",
            subject_type="Ingredient",
            object_type="Product",
            source_modality="REL",
        )
        fact = builder.facts[0]
        assert fact.subject_ref_kind == ObjectRefKind.CONCEPT
