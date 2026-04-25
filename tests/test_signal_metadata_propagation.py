from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.jobs.run_daily_pipeline import _agg_to_dict, _signal_to_dict
from src.mart.aggregate_product_signals import AggProductSignalRow, aggregate_product_signals
from src.wrap.projection_registry import ProjectionKey, ProjectionRegistry, ProjectionRule
from src.wrap.signal_emitter import SignalEmitter


def test_signal_emitter_copies_fact_promotion_metadata():
    registry = ProjectionRegistry()
    registry.load()
    builder = CanonicalFactBuilder()
    builder.add_fact(
        review_id="r1",
        subject_iri="product:P1",
        predicate="has_attribute",
        object_iri="concept:BEEAttr:moisture",
        subject_type="Product",
        object_type="BEEAttr",
        polarity="POS",
        confidence=0.8,
        source_modality="BEE",
        evidence_kind="BEE_SYNTHETIC",
        fact_status="CANONICAL_PROMOTED",
    )
    fact = builder.facts[0]
    fact.target_linked = True
    fact.attribution_source = "direct_rel"

    result = SignalEmitter(registry).emit_from_facts([fact], target_product_id="P1")

    assert result.signals[0].evidence_kind == "BEE_SYNTHETIC"
    assert result.signals[0].fact_status == "CANONICAL_PROMOTED"
    assert result.signals[0].source_confidence == 0.8
    assert result.signals[0].target_linked is True
    assert result.signals[0].attribution_source == "direct_rel"


def test_signal_to_dict_exposes_evidence_kind_for_aggregation():
    registry = ProjectionRegistry()
    registry.load()
    builder = CanonicalFactBuilder()
    builder.add_fact(
        review_id="r1",
        subject_iri="product:P1",
        predicate="has_attribute",
        object_iri="concept:BEEAttr:moisture",
        subject_type="Product",
        object_type="BEEAttr",
        confidence=0.7,
        evidence_kind="BEE_SYNTHETIC",
    )
    signal = SignalEmitter(registry).emit_from_facts(
        builder.facts,
        target_product_id="P1",
    ).signals[0]

    signal_dict = _signal_to_dict(signal)

    assert signal_dict["evidence_kind"] == "BEE_SYNTHETIC"
    assert signal_dict["source_confidence"] == 0.7


def test_aggregate_synthetic_ratio_uses_signal_evidence_kind():
    rows = aggregate_product_signals([
        {
            "signal_id": "s1",
            "review_id": "r1",
            "target_product_id": "P1",
            "edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_type": "BEEAttr",
            "dst_id": "concept:BEEAttr:moisture",
            "polarity": "POS",
            "weight": 0.8,
            "evidence_kind": "BEE_SYNTHETIC",
        },
        {
            "signal_id": "s2",
            "review_id": "r2",
            "target_product_id": "P1",
            "edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_type": "BEEAttr",
            "dst_id": "concept:BEEAttr:moisture",
            "polarity": "POS",
            "weight": 0.9,
            "evidence_kind": "BEE_DICT",
        },
    ])

    all_window = next(row for row in rows if row.window_type == "all")
    assert all_window.synthetic_ratio == 0.5


def test_agg_to_dict_preserves_corpus_fields():
    row = AggProductSignalRow(
        target_product_id="P1",
        canonical_edge_type="HAS_BEE_ATTR_SIGNAL",
        dst_node_type="BEEAttr",
        dst_node_id="concept:BEEAttr:moisture",
        window_type="all",
        review_cnt=4,
        pos_cnt=3,
        neg_cnt=0,
        neu_cnt=1,
        support_count=4,
        score=0.75,
        recent_score=None,
        recent_support_count=None,
        last_seen_at=None,
        window_start=None,
        window_end=None,
        evidence_sample=None,
        distinct_review_count=4,
        avg_confidence=0.8,
        synthetic_ratio=0.25,
        corpus_weight=3.2,
        is_promoted=True,
    )

    d = _agg_to_dict(row)

    assert d["distinct_review_count"] == 4
    assert d["avg_confidence"] == 0.8
    assert d["synthetic_ratio"] == 0.25
    assert d["corpus_weight"] == 3.2
    assert d["is_promoted"] is True


def test_projection_registry_enforces_optional_evidence_and_confidence_gates():
    registry = ProjectionRegistry()
    key = ProjectionKey("has_attribute", "Product", "BEEAttr", "")
    registry._rules[key] = ProjectionRule(
        registry_version="test",
        input_predicate="has_attribute",
        subject_type="Product",
        object_type="BEEAttr",
        polarity="",
        qualifier_required=False,
        qualifier_type="",
        output_signal_family="BEE_ATTR",
        output_edge_type="HAS_BEE_ATTR_SIGNAL",
        output_dst_type="BEEAttr",
        output_transform="identity",
        output_weight_rule="bee_weight",
        if_unresolved_action="QUARANTINE",
        notes="",
        allowed_evidence_kind="RAW_REL",
        min_confidence=0.8,
    )

    assert registry.project(
        "has_attribute",
        "Product",
        "BEEAttr",
        evidence_kind="BEE_SYNTHETIC",
        confidence=0.9,
    ) == "QUARANTINE"
    assert registry.project(
        "has_attribute",
        "Product",
        "BEEAttr",
        evidence_kind="RAW_REL",
        confidence=0.7,
    ) == "QUARANTINE"
    assert registry.project(
        "has_attribute",
        "Product",
        "BEEAttr",
        evidence_kind="RAW_REL",
        confidence=0.9,
    ) != "QUARANTINE"
