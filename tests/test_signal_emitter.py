"""Tests for signal emitter: transform dispatch, qualifier check, merge, evidence."""

import pytest
from src.wrap.signal_emitter import SignalEmitter
from src.wrap.projection_registry import ProjectionRegistry, ProjectionKey, ProjectionRule
from src.canonical.canonical_fact_builder import CanonicalFact


def _make_registry(rules_data: list[dict]) -> ProjectionRegistry:
    reg = ProjectionRegistry()
    for r in rules_data:
        key = ProjectionKey(r["pred"], r.get("subj", ""), r.get("obj", ""), r.get("pol", ""))
        reg._rules[key] = ProjectionRule(
            registry_version="v1", input_predicate=r["pred"],
            subject_type=r.get("subj", ""), object_type=r.get("obj", ""),
            polarity=r.get("pol", ""),
            qualifier_required=r.get("qual_req", False), qualifier_type=r.get("qual_type", ""),
            output_signal_family=r.get("family", "TEST"),
            output_edge_type=r.get("edge", "TEST_SIGNAL"),
            output_dst_type=r.get("dst_type", "Entity"),
            output_transform=r.get("transform", "identity"),
            output_weight_rule=r.get("weight_rule", "default_weight"),
            if_unresolved_action="", notes="",
        )
    reg._version = "v1"
    return reg


class TestTransformDispatch:
    def test_identity_transform(self):
        reg = _make_registry([{"pred": "used_on", "subj": "Product", "obj": "TemporalContext",
                               "family": "CONTEXT", "edge": "USED_IN_CONTEXT_SIGNAL", "dst_type": "TemporalContext"}])
        emitter = SignalEmitter(reg)
        fact = CanonicalFact(
            fact_id="f1", review_id="rv1", subject_iri="product:P1",
            predicate="used_on", object_iri="concept:TemporalContext:morning",
            object_ref_kind="CONCEPT", subject_type="Product", object_type="TemporalContext",
        )
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        assert sid is not None
        signal = emitter._signals[sid]
        assert signal.dst_id == "concept:TemporalContext:morning"
        assert signal.edge_type == "USED_IN_CONTEXT_SIGNAL"

    def test_reverse_transform(self):
        """caused_by(Concern, Product) → dst should be Concern (subject), not Product (object)."""
        reg = _make_registry([{"pred": "caused_by", "subj": "Concern", "obj": "Product",
                               "family": "CONCERN_NEG", "edge": "MAY_CAUSE_CONCERN_SIGNAL",
                               "dst_type": "Concern", "transform": "reverse"}])
        emitter = SignalEmitter(reg)
        fact = CanonicalFact(
            fact_id="f2", review_id="rv1", subject_iri="concept:Concern:dryness",
            predicate="caused_by", object_iri="product:P1",
            object_ref_kind="ENTITY", subject_type="Concern", object_type="Product",
        )
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        assert sid is not None
        signal = emitter._signals[sid]
        # Reverse: dst_id should be the subject (Concern), not object (Product)
        assert signal.dst_id == "concept:Concern:dryness"
        assert signal.dst_type == "Concern"

    def test_product_linkage_transform(self):
        """HAS_KEYWORD(BEEAttr→Keyword) → signal should link Product→Keyword."""
        reg = _make_registry([{"pred": "HAS_KEYWORD", "subj": "BEEAttr", "obj": "Keyword",
                               "family": "BEE_KEYWORD", "edge": "HAS_BEE_KEYWORD_SIGNAL",
                               "dst_type": "Keyword", "transform": "product_linkage",
                               "weight_rule": "bee_weight"}])
        emitter = SignalEmitter(reg)
        fact = CanonicalFact(
            fact_id="f3", review_id="rv1", subject_iri="concept:BEEAttr:adhesion",
            predicate="HAS_KEYWORD", object_iri="concept:Keyword:kw_good",
            object_ref_kind="CONCEPT", subject_type="BEEAttr", object_type="Keyword",
            confidence=0.9,
        )
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        assert sid is not None
        signal = emitter._signals[sid]
        assert signal.dst_id == "concept:Keyword:kw_good"
        assert signal.bee_attr_id == "concept:BEEAttr:adhesion"
        assert signal.keyword_id == "concept:Keyword:kw_good"
        assert signal.weight == pytest.approx(0.9)  # bee_weight rule


class TestQualifierCheck:
    def test_qualifier_required_missing(self):
        """qualifier_required=True but fact has no qualifiers → quarantine."""
        reg = _make_registry([{"pred": "recommended_to", "subj": "Product", "obj": "UserSegment",
                               "family": "SEGMENT", "edge": "RECOMMENDED_TO_SEGMENT_SIGNAL",
                               "qual_req": True, "qual_type": "segment"}])
        emitter = SignalEmitter(reg)
        fact = CanonicalFact(
            fact_id="f4", review_id="rv1", subject_iri="product:P1",
            predicate="recommended_to", object_iri="mention:rv1:5",
            object_ref_kind="ENTITY", subject_type="Product", object_type="UserSegment",
            qualifiers=[],  # empty!
        )
        sid = emitter.emit_from_fact(fact, target_product_id="P1")
        assert sid is None
        assert "f4" in emitter._quarantined


class TestSignalMerge:
    def test_same_signal_merges(self):
        """Same review + same semantic → 1 signal with merged fact_ids."""
        reg = _make_registry([{"pred": "has_attribute", "subj": "Product", "obj": "BEEAttr",
                               "family": "BEE_ATTR", "edge": "HAS_BEE_ATTR_SIGNAL", "dst_type": "BEEAttr"}])
        emitter = SignalEmitter(reg)
        fact1 = CanonicalFact(
            fact_id="f_bee", review_id="rv1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            object_ref_kind="CONCEPT", subject_type="Product", object_type="BEEAttr",
            source_modalities=["BEE"],
        )
        fact2 = CanonicalFact(
            fact_id="f_rel", review_id="rv1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            object_ref_kind="CONCEPT", subject_type="Product", object_type="BEEAttr",
            source_modalities=["REL"],
        )
        emitter.emit_from_fact(fact1, target_product_id="P1")
        emitter.emit_from_fact(fact2, target_product_id="P1")
        assert emitter.signal_count == 1
        signal = list(emitter._signals.values())[0]
        assert len(signal.source_fact_ids) == 2


class TestEvidenceGeneration:
    def test_evidence_rows_created(self):
        reg = _make_registry([{"pred": "used_on", "subj": "Product", "obj": "TemporalContext",
                               "family": "CONTEXT", "edge": "USED_IN_CONTEXT_SIGNAL"}])
        emitter = SignalEmitter(reg)
        fact = CanonicalFact(
            fact_id="f5", review_id="rv1", subject_iri="product:P1",
            predicate="used_on", object_iri="concept:TemporalContext:morning",
            object_ref_kind="CONCEPT", subject_type="Product", object_type="TemporalContext",
        )
        emitter.emit_from_fact(fact, target_product_id="P1")
        result = emitter.emit_from_facts([], target_product_id="P1")
        # Evidence should exist from the first emit
        assert len(emitter._evidence_rows) >= 1
        assert emitter._evidence_rows[0]["fact_id"] == "f5"
