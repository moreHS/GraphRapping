from __future__ import annotations

from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.ingest.review_ingest import RawReviewRecord
from src.jobs.run_daily_pipeline import process_review
from src.kg.adapter import kg_result_to_facts
from src.kg.models import KGEdge, KGEntity, KGResult
from src.link.product_matcher import ProductIndex
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.qa.quarantine_handler import QuarantineHandler
from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter


def _kg_result_with_keyword_edge(*, evidence_kind: str | None = None) -> KGResult:
    entities = [
        KGEntity(
            entity_id="bee_attr",
            entity_type="BEE_ATTR",
            normalized_value="보습력",
            word="보습력",
            bee_type="보습력",
            polarity="POS",
        ),
        KGEntity(
            entity_id="keyword",
            entity_type="KEYWORD",
            normalized_value="촉촉",
            word="촉촉",
        ),
    ]
    result = KGResult(
        entities=entities,
        edges=[
            KGEdge(
                edge_id="edge_keyword",
                subj_entity_id="bee_attr",
                obj_entity_id="keyword",
                relation_type="HAS_KEYWORD",
                evidence_kind=evidence_kind,
            )
        ],
    )
    result.entity_map = {entity.entity_id: entity for entity in entities}
    return result


def _emit_keyword_signals(builder: CanonicalFactBuilder):
    registry = ProjectionRegistry()
    registry.load()
    emitter = SignalEmitter(registry)
    return emitter.emit_from_facts(builder.facts, target_product_id="P1")


def test_kg_on_source_backed_keyword_projects_to_bee_keyword_signal() -> None:
    builder = CanonicalFactBuilder()

    stats = kg_result_to_facts(
        kg_result=_kg_result_with_keyword_edge(),
        review_id="rv_keyword",
        target_product_iri="entity:Product:P1",
        builder=builder,
    )

    assert stats == {"promoted": 1, "evidence_only": 0, "dropped": 0, "quarantined": 0}
    [fact] = builder.facts
    assert fact.predicate == "HAS_KEYWORD"
    assert fact.subject_type == "BEEAttr"
    assert fact.object_type == "Keyword"
    assert fact.fact_status == "CANONICAL_PROMOTED"
    assert fact.evidence_kind == "BEE_DICT"
    assert fact.confidence is not None and fact.confidence >= 0.6

    emitted = _emit_keyword_signals(builder)

    assert emitted.quarantined_facts == []
    [signal] = emitted.signals
    assert signal.signal_family == "BEE_KEYWORD"
    assert signal.edge_type == "HAS_BEE_KEYWORD_SIGNAL"
    assert signal.keyword_id == fact.object_iri
    assert signal.bee_attr_id == fact.subject_iri
    assert signal.evidence_kind == "BEE_DICT"


def test_kg_on_auto_keyword_remains_quarantined_and_not_promoted() -> None:
    builder = CanonicalFactBuilder()

    stats = kg_result_to_facts(
        kg_result=_kg_result_with_keyword_edge(evidence_kind="AUTO_KEYWORD"),
        review_id="rv_auto_keyword",
        target_product_iri="entity:Product:P1",
        builder=builder,
    )

    assert stats == {"promoted": 0, "evidence_only": 0, "dropped": 0, "quarantined": 1}
    assert builder.facts == []

    emitted = _emit_keyword_signals(builder)
    assert emitted.signals == []
    assert emitted.quarantined_facts == []


def _process_review_with_phrase(phrase: str, *, kg_mode: str = "on") -> object:
    bee_normalizer = BEENormalizer()
    bee_normalizer.load_from_dicts(
        attr_dict={"보습력": {"attr_id": "bee_attr_moisturizing_power", "label_ko": "보습력"}},
        keyword_map={"촉촉": [{"keyword_id": "kw_moist", "label_ko": "촉촉함"}]},
    )
    registry = ProjectionRegistry()
    registry.load()

    record = RawReviewRecord(
        brnd_nm="Brand",
        prod_nm="Product",
        text=f"이 제품 {phrase}",
        ner=[
            {
                "word": "Review Target",
                "entity_group": "PRD",
                "start": None,
                "end": None,
                "sentiment": "중립",
            }
        ],
        bee=[
            {
                "word": phrase,
                "entity_group": "보습력",
                "start": 5,
                "end": 5 + len(phrase),
                "sentiment": "긍정",
            }
        ],
        relation=[
            {
                "subject": {"word": "Review Target", "entity_group": "PRD"},
                "object": {
                    "word": phrase,
                    "entity_group": "보습력",
                    "start": 5,
                    "end": 5 + len(phrase),
                },
                "relation": "has_attribute",
                "source_type": "NER-BeE",
            }
        ],
    )

    return process_review(
        record=record,
        source="test",
        product_index=ProductIndex.build([
            {"product_id": "P1", "brand_name": "Brand", "product_name": "Product"}
        ]),
        bee_normalizer=bee_normalizer,
        relation_canonicalizer=RelationCanonicalizer(),
        projection_registry=registry,
        quarantine=QuarantineHandler(),
        predicate_contracts={},
        kg_mode=kg_mode,
    )


def test_kg_on_surface_map_bee_keyword_helper_emits_signal_without_attribute_duplication() -> None:
    bundle = _process_review_with_phrase("촉촉해서 좋아요", kg_mode="on")

    keyword_signals = [
        signal for signal in bundle.wrapped_signals
        if signal.edge_type == "HAS_BEE_KEYWORD_SIGNAL"
    ]
    assert len(keyword_signals) == 1
    assert keyword_signals[0].dst_id == "concept:Keyword:kw_moist"
    assert keyword_signals[0].evidence_kind == "BEE_DICT"
    assert keyword_signals[0].source_confidence is not None
    assert keyword_signals[0].source_confidence >= 0.6

    product_attr_facts = [
        fact for fact in bundle.canonical_facts
        if fact.predicate == "has_attribute"
        and fact.subject_type == "Product"
        and fact.object_type == "BEEAttr"
    ]
    assert len(product_attr_facts) == 1


def test_kg_on_unknown_bee_surface_does_not_emit_promoted_keyword_signal() -> None:
    bundle = _process_review_with_phrase("처음 보는 표현", kg_mode="on")

    assert all(
        signal.edge_type != "HAS_BEE_KEYWORD_SIGNAL"
        for signal in bundle.wrapped_signals
    )


def test_shadow_mode_keeps_legacy_keyword_path_without_kg_on_helper(monkeypatch) -> None:
    import src.jobs.run_daily_pipeline as daily

    def fail_if_called(**_kwargs):
        raise AssertionError("kg_on keyword helper must not run in shadow mode")

    monkeypatch.setattr(daily, "_add_source_backed_keyword_helper_facts", fail_if_called)

    bundle = _process_review_with_phrase("촉촉해서 좋아요", kg_mode="shadow")

    keyword_signals = [
        signal for signal in bundle.wrapped_signals
        if signal.edge_type == "HAS_BEE_KEYWORD_SIGNAL"
    ]
    assert len(keyword_signals) == 1
