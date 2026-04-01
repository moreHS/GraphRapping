"""
KG Adapter: evidence graph → canonical fact layer bridge.

Classifies edges for promotion (PROMOTE/KEEP_EVIDENCE_ONLY/DROP/QUARANTINE)
and passes metadata (negated/intensity/evidence_kind/confidence) to the
canonical fact builder. Does NOT produce serving signals directly.
"""

from __future__ import annotations

import logging

from src.kg.models import KGResult, KGEntity, KGEdge

logger = logging.getLogger(__name__)
from src.canonical.canonical_fact_builder import CanonicalFactBuilder, CanonicalEntity, FactProvenance
from src.common.ids import make_concept_iri
from src.common.text_normalize import normalize_text
from src.common.enums import ObjectRefKind, PromotionDecision


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


def _classify_promotion(edge: KGEdge, kg_subj: KGEntity | None, kg_obj: KGEntity | None) -> str:
    """Classify an edge for promotion gate.

    Returns PromotionDecision value.
    """
    # Synthetic BEE relations → evidence only
    if edge.evidence_kind == "BEE_SYNTHETIC":
        return PromotionDecision.KEEP_EVIDENCE_ONLY

    # Auto-generated keyword candidate edges → quarantine
    if edge.evidence_kind == "AUTO_KEYWORD":
        return PromotionDecision.QUARANTINE

    # Low-confidence edges
    if edge.confidence is not None and edge.confidence < 0.2:
        return PromotionDecision.DROP

    # evidence_kind=None is acceptable — means standard relation without special marking
    # Standard NER-NER and NER-BeE relations have None evidence_kind and should promote
    return PromotionDecision.PROMOTE


def kg_result_to_facts(
    kg_result: KGResult,
    review_id: str,
    target_product_iri: str | None,
    builder: CanonicalFactBuilder,
    reviewer_proxy_iri: str = "",
    review_idx: int = 0,
) -> dict:
    """Convert KG result to GraphRapping canonical facts.

    Registers entities and adds facts to the builder.
    Returns: {"promoted": int, "evidence_only": int, "dropped": int, "quarantined": int}
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

    # 2. Process edges → facts with promotion gate
    stats = {"promoted": 0, "evidence_only": 0, "dropped": 0, "quarantined": 0}

    for edge in kg_result.edges:
        subj_iri = id_to_iri.get(edge.subj_entity_id)
        obj_iri = id_to_iri.get(edge.obj_entity_id)
        if not subj_iri or not obj_iri:
            logger.debug("Drop edge: unmapped IRI (rel=%s subj=%s obj=%s)",
                         edge.relation_type, edge.subj_entity_id[:8], edge.obj_entity_id[:8])
            stats["dropped"] += 1
            continue

        subj_type = id_to_type.get(edge.subj_entity_id, "")
        obj_type = id_to_type.get(edge.obj_entity_id, "")

        # Use actual KG entity data — no hardcoded types
        kg_subj = kg_result.entity_map.get(edge.subj_entity_id)
        kg_obj = kg_result.entity_map.get(edge.obj_entity_id)

        if edge.relation_type == "OFFICIAL_BRAND":
            continue  # handled by product_ingest

        # Promotion gate: classify edge
        decision = _classify_promotion(edge, kg_subj, kg_obj)
        if decision == PromotionDecision.DROP:
            stats["dropped"] += 1
            logger.debug("DROP edge: %s (confidence=%s)", edge.relation_type, edge.confidence)
            continue
        if decision == PromotionDecision.QUARANTINE:
            stats["quarantined"] += 1
            logger.debug("QUARANTINE edge: %s (evidence_kind=%s)", edge.relation_type, edge.evidence_kind)
            continue

        # Determine fact_status based on promotion decision
        fact_status = "CANONICAL_PROMOTED" if decision == PromotionDecision.PROMOTE else "EVIDENCE_ONLY"

        # Determine predicate, polarity, modality from actual KG data
        predicate = edge.relation_type.lower()
        if edge.relation_type in ("HAS_ATTRIBUTE", "HAS_KEYWORD"):
            predicate = edge.relation_type  # keep original case for registry

        # Polarity: from BEE_ATTR entity if available, else edge sentiment
        polarity = None
        if kg_obj and kg_obj.polarity:
            polarity = kg_obj.polarity
        elif kg_subj and kg_subj.polarity:
            polarity = kg_subj.polarity
        elif edge.sentiment and edge.sentiment != "NEU":
            polarity = edge.sentiment

        # Source modality: BEE if either side is BEE_ATTR/KEYWORD, else REL
        is_bee = (kg_subj and kg_subj.entity_type in ("BEE_ATTR", "KEYWORD")) or \
                 (kg_obj and kg_obj.entity_type in ("BEE_ATTR", "KEYWORD"))
        modality = "BEE" if is_bee else "REL"

        # Object ref kind
        ref_kind = ObjectRefKind.CONCEPT if obj_type in ("BEEAttr", "Keyword", "Brand", "Category", "Ingredient", "TemporalContext", "Concern", "Goal") else ObjectRefKind.ENTITY

        builder.add_fact(
            review_id=review_id,
            subject_iri=subj_iri,
            predicate=predicate,
            object_iri=obj_iri,
            object_ref_kind=ref_kind,
            subject_type=subj_type,
            object_type=obj_type,
            polarity=polarity,
            confidence=edge.confidence,
            source_modality=modality,
            provenance=FactProvenance(
                raw_table="bee_raw" if is_bee else "rel_raw",
                raw_row_id=str(review_idx),
                review_id=review_id,
                source_modality=modality,
            ),
            negated=edge.negated,
            intensity=edge.intensity,
            evidence_kind=edge.evidence_kind,
            fact_status=fact_status,
        )

        if decision == PromotionDecision.PROMOTE:
            stats["promoted"] += 1
        else:
            stats["evidence_only"] += 1

    return stats
