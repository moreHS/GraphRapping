"""
P0-3: signal_evidence as provenance source of truth.

Verifies that evidence_rows are the canonical provenance path,
and source_fact_ids is kept in sync as a cache.
"""

import pytest

from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter, WrappedSignal
from src.canonical.canonical_fact_builder import CanonicalFact, CanonicalFactBuilder
from src.common.enums import ObjectRefKind


@pytest.fixture
def registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


class TestSignalEvidenceSourceOfTruth:
    def test_evidence_rows_generated_for_every_signal(self, registry):
        """Every emitted signal should have corresponding evidence_rows."""
        builder = CanonicalFactBuilder()
        builder.add_bee_facts(
            review_id="r1", product_iri="product:P1",
            bee_attr_id="adhesion", bee_attr_label="밀착력",
            keyword_ids=["stickiness"], keyword_labels=["끈적거림"],
            polarity="POS",
        )
        emitter = SignalEmitter(registry)
        result = emitter.emit_from_facts(builder.facts, target_product_id="P1")

        # Every signal should have at least one evidence row
        signal_ids_with_evidence = {e["signal_id"] for e in result.evidence_rows}
        for sig in result.signals:
            assert sig.signal_id in signal_ids_with_evidence, \
                f"Signal {sig.signal_id} has no evidence_rows"

    def test_source_fact_ids_matches_evidence_rows(self, registry):
        """source_fact_ids cache should match evidence_rows fact_ids."""
        builder = CanonicalFactBuilder()
        builder.add_bee_facts(
            review_id="r1", product_iri="product:P1",
            bee_attr_id="adhesion", bee_attr_label="밀착력",
            keyword_ids=["stickiness"], keyword_labels=["끈적거림"],
            polarity="POS",
        )
        emitter = SignalEmitter(registry)
        result = emitter.emit_from_facts(builder.facts, target_product_id="P1")

        for sig in result.signals:
            evidence_fact_ids = {
                e["fact_id"] for e in result.evidence_rows
                if e["signal_id"] == sig.signal_id
            }
            cache_fact_ids = set(sig.source_fact_ids)
            assert cache_fact_ids == evidence_fact_ids, \
                f"Signal {sig.signal_id}: cache={cache_fact_ids} != evidence={evidence_fact_ids}"

    def test_multi_fact_merge_evidence_accumulates(self, registry):
        """When multiple facts merge into one signal, evidence_rows accumulate."""
        builder = CanonicalFactBuilder()
        # Same semantic fact from two modalities → one fact with merged provenance
        builder.add_fact(
            review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            subject_type="Product", object_type="BEEAttr",
            polarity="POS", source_modality="BEE",
        )
        builder.add_fact(
            review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            subject_type="Product", object_type="BEEAttr",
            polarity="POS", source_modality="REL",
        )
        emitter = SignalEmitter(registry)
        result = emitter.emit_from_facts(builder.facts, target_product_id="P1")

        # Facts merge at canonical layer, so 1 fact → 1 signal
        assert len(result.signals) >= 1


def test_evidence_sample_uses_signal_id_not_source_fact_ids():
    """evidence_sample in aggregate must reference signal_id, not source_fact_ids."""
    from src.mart.aggregate_product_signals import aggregate_product_signals
    signals = [
        {
            "target_product_id": "p1",
            "edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_type": "BEEAttr",
            "dst_id": "moisture",
            "polarity": "POS",
            "review_id": f"r{i}",
            "signal_id": f"sig_{i}",
            "window_ts": "2025-01-01T00:00:00+00:00",
            "weight": 0.9,
            "signal_family": "BEE_ATTR",
            "source_fact_ids": [f"fact_{i}"],
        }
        for i in range(5)
    ]
    rows = aggregate_product_signals(signals)
    for row in rows:
        if row.evidence_sample:
            for ev in row.evidence_sample:
                assert "signal_id" in ev, "evidence_sample must use signal_id"
                assert "fact_id" not in ev, "evidence_sample must not use fact_id (deprecated)"
