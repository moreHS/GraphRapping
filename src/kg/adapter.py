"""
KG → GraphRapping Adapter: converts KGResult to CanonicalFacts.

Key adaptations:
- KG entity_id (SHA256) → GraphRapping concept IRI
- BEE_ATTR polarity on entity → polarity on fact
- KG edge → CanonicalFact with provenance
"""

from __future__ import annotations

import logging

from src.kg.models import KGResult, KGEntity, KGEdge

logger = logging.getLogger(__name__)
from src.canonical.canonical_fact_builder import CanonicalFactBuilder, CanonicalEntity, FactProvenance
from src.common.ids import make_concept_iri
from src.common.text_normalize import normalize_text
from src.common.enums import ObjectRefKind


# KG entity_type → GraphRapping concept_type
_KG_TYPE_TO_GR_TYPE = {
    "BEE_ATTR": "BEEAttr",
    "KEYWORD": "Keyword",
    "BRD": "Brand",
    "CAT": "Category",
    "PRD": "Product",
    "PER": "ReviewerProxy",
    "DATE": "TemporalContext",
    "ING": "Ingredient",
    "COL": "Color",
    "AGE": "AgeBand",
    "VOL": "Volume",
    "EVN": "Event",
}


def to_graphrapping_iri(kg_entity: KGEntity) -> str:
    """Convert KG entity to GraphRapping concept IRI."""
    gr_type = _KG_TYPE_TO_GR_TYPE.get(kg_entity.entity_type, kg_entity.entity_type)

    if kg_entity.entity_type == "BEE_ATTR":
        # BEE_ATTR: use bee_type only (polarity moves to fact)
        return make_concept_iri(gr_type, normalize_text(kg_entity.bee_type or kg_entity.word))
    elif kg_entity.entity_type == "KEYWORD":
        return make_concept_iri(gr_type, kg_entity.normalized_value)
    elif kg_entity.is_placeholder:
        return f"placeholder:{kg_entity.normalized_value}:{kg_entity.scope_key or ''}"
    else:
        return make_concept_iri(gr_type, kg_entity.normalized_value)


def to_graphrapping_entity(kg_entity: KGEntity) -> CanonicalEntity:
    """Convert KG entity to GraphRapping CanonicalEntity."""
    iri = to_graphrapping_iri(kg_entity)
    gr_type = _KG_TYPE_TO_GR_TYPE.get(kg_entity.entity_type, kg_entity.entity_type)

    return CanonicalEntity(
        entity_iri=iri,
        entity_type=gr_type,
        canonical_name=kg_entity.word,
        canonical_name_norm=normalize_text(kg_entity.word),
    )


def kg_result_to_facts(
    kg_result: KGResult,
    review_id: str,
    target_product_iri: str | None,
    builder: CanonicalFactBuilder,
    reviewer_proxy_iri: str = "",
    review_idx: int = 0,
) -> None:
    """Convert KG result to GraphRapping canonical facts.

    Registers entities and adds facts to the builder.
    """
    # Build placeholder IRI override map
    placeholder_iri: dict[str, str] = {}
    for e in kg_result.entities:
        if e.is_placeholder:
            if e.normalized_value == "review_target" and target_product_iri:
                placeholder_iri[e.entity_id] = target_product_iri
            elif e.normalized_value == "reviewer" and reviewer_proxy_iri:
                placeholder_iri[e.entity_id] = reviewer_proxy_iri

    # Build entity_id → IRI lookup
    id_to_iri: dict[str, str] = {}
    id_to_type: dict[str, str] = {}

    # 1. Register all entities (skip placeholders — use external IRIs)
    for kg_entity in kg_result.entities:
        if kg_entity.entity_id in placeholder_iri:
            id_to_iri[kg_entity.entity_id] = placeholder_iri[kg_entity.entity_id]
            id_to_type[kg_entity.entity_id] = _KG_TYPE_TO_GR_TYPE.get(kg_entity.entity_type, kg_entity.entity_type)
            continue
        iri = to_graphrapping_iri(kg_entity)
        gr_entity = to_graphrapping_entity(kg_entity)
        builder.register_entity(gr_entity)
        id_to_iri[kg_entity.entity_id] = iri
        id_to_type[kg_entity.entity_id] = _KG_TYPE_TO_GR_TYPE.get(
            kg_entity.entity_type, kg_entity.entity_type
        )

    # 2. Process edges → facts
    for edge in kg_result.edges:
        subj_iri = id_to_iri.get(edge.subj_entity_id)
        obj_iri = id_to_iri.get(edge.obj_entity_id)
        if not subj_iri or not obj_iri:
            logger.debug("Drop edge: unmapped IRI (rel=%s subj=%s obj=%s)",
                         edge.relation_type, edge.subj_entity_id[:8], edge.obj_entity_id[:8])
            continue

        subj_type = id_to_type.get(edge.subj_entity_id, "")
        obj_type = id_to_type.get(edge.obj_entity_id, "")

        # Determine predicate and modality based on edge type
        if edge.relation_type == "HAS_ATTRIBUTE":
            # BEE_ATTR fact: Product → BEEAttr
            # Use product_iri as subject if available
            subj = target_product_iri or subj_iri
            # Get polarity from BEE_ATTR entity
            kg_obj = kg_result.entity_map.get(edge.obj_entity_id)
            polarity = kg_obj.polarity if kg_obj else None

            builder.add_fact(
                review_id=review_id,
                subject_iri=subj,
                predicate="has_attribute",
                object_iri=obj_iri,
                object_ref_kind=ObjectRefKind.CONCEPT,
                subject_type="Product",
                object_type="BEEAttr",
                polarity=polarity or edge.sentiment,
                source_modality="BEE",
                provenance=FactProvenance(
                    raw_table="bee_raw", raw_row_id=str(review_idx),
                    review_id=review_id, source_modality="BEE",
                ),
            )

        elif edge.relation_type == "HAS_KEYWORD":
            # KEYWORD fact: BEEAttr → Keyword (propagate parent BEE_ATTR polarity)
            kg_subj = kg_result.entity_map.get(edge.subj_entity_id)
            kw_polarity = kg_subj.polarity if kg_subj else None
            builder.add_fact(
                review_id=review_id,
                subject_iri=subj_iri,
                predicate="HAS_KEYWORD",
                object_iri=obj_iri,
                object_ref_kind=ObjectRefKind.CONCEPT,
                subject_type="BEEAttr",
                object_type="Keyword",
                polarity=kw_polarity,
                source_modality="BEE",
                provenance=FactProvenance(
                    raw_table="bee_raw", raw_row_id=str(review_idx),
                    review_id=review_id, source_modality="BEE",
                ),
            )

        elif edge.relation_type == "OFFICIAL_BRAND":
            # Skip — handled by product_ingest
            continue

        else:
            # NER-NER relation
            predicate = edge.relation_type.lower()
            builder.add_fact(
                review_id=review_id,
                subject_iri=subj_iri,
                predicate=predicate,
                object_iri=obj_iri,
                object_ref_kind=ObjectRefKind.ENTITY,
                subject_type=subj_type,
                object_type=obj_type,
                polarity=edge.sentiment if edge.sentiment != "NEU" else None,
                source_modality="REL",
                provenance=FactProvenance(
                    raw_table="rel_raw", raw_row_id=str(review_idx),
                    review_id=review_id, source_modality="REL",
                ),
            )
