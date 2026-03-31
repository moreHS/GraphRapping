"""
P0-4: signal dedup key includes polarity, negated, qualifier_fingerprint.

Signal dedup contract:
  key = (review_id, target_product_id, edge_type, dst_id, polarity, negated, qualifier_fingerprint, registry_version)
  merge: weight=max, confidence=max, source_modalities=union, evidence=accumulate
  NEVER merge if polarity or negated differ.
"""

import pytest

from src.common.ids import make_signal_id, make_qualifier_fingerprint
from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter
from src.canonical.canonical_fact_builder import CanonicalFact, FactQualifier
from src.common.enums import ObjectRefKind


@pytest.fixture
def registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


class TestSignalDedupPolarityQualifier:
    def test_same_dst_different_polarity_separate_signals(self):
        """Same dst_id, different polarity → different signal_ids."""
        sid_pos = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "concept:BEEAttr:adhesion", "POS", "v1")
        sid_neg = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "concept:BEEAttr:adhesion", "NEG", "v1")
        assert sid_pos != sid_neg

    def test_same_dst_different_qualifier_separate_signals(self):
        """Same dst_id+polarity, different qualifier → different signal_ids."""
        qfp1 = make_qualifier_fingerprint([("context", "morning")])
        qfp2 = make_qualifier_fingerprint([("context", "evening")])
        sid1 = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1", qualifier_fingerprint=qfp1)
        sid2 = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1", qualifier_fingerprint=qfp2)
        assert sid1 != sid2

    def test_same_semantic_signal_idempotent(self):
        """Same semantic signal input twice → same signal_id."""
        sid1 = make_signal_id("r1", "P1", "EDGE", "dst", "POS", "v1", negated="false")
        sid2 = make_signal_id("r1", "P1", "EDGE", "dst", "POS", "v1", negated="false")
        assert sid1 == sid2

    def test_emitter_separates_different_polarity(self, registry):
        """E2E: two facts with different polarity → two signals."""
        fact_pos = CanonicalFact(
            fact_id="fact:pos", review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            object_ref_kind=ObjectRefKind.CONCEPT,
            subject_type="Product", object_type="BEEAttr", polarity="POS",
        )
        fact_neg = CanonicalFact(
            fact_id="fact:neg", review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            object_ref_kind=ObjectRefKind.CONCEPT,
            subject_type="Product", object_type="BEEAttr", polarity="NEG",
        )
        emitter = SignalEmitter(registry)
        emitter.emit_from_fact(fact_pos, target_product_id="P1")
        emitter.emit_from_fact(fact_neg, target_product_id="P1")
        assert emitter.signal_count == 2

    def test_emitter_merges_same_semantic(self, registry):
        """Same semantic fact emitted twice → merged into 1 signal."""
        fact = CanonicalFact(
            fact_id="fact:1", review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            object_ref_kind=ObjectRefKind.CONCEPT,
            subject_type="Product", object_type="BEEAttr", polarity="POS",
        )
        emitter = SignalEmitter(registry)
        emitter.emit_from_fact(fact, target_product_id="P1")
        emitter.emit_from_fact(fact, target_product_id="P1")
        assert emitter.signal_count == 1
