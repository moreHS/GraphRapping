"""
KG Adapter: evidence graph → canonical fact layer bridge.

Classifies edges for promotion (PROMOTE/KEEP_EVIDENCE_ONLY/DROP/QUARANTINE)
and passes metadata (negated/intensity/evidence_kind/confidence) to the
canonical fact builder. Does NOT produce serving signals directly.
"""

from __future__ import annotations

import logging

from src.canonical.canonical_fact_builder import (
    CanonicalFactBuilder, CanonicalEntity, FactProvenance, FactQualifier,
)
from src.common.config_loader import (
    load_concern_dict, load_goal_alias_map, load_predicate_contracts,
)
from src.common.enums import ObjectRefKind, PromotionDecision
from src.common.ids import make_concept_iri
from src.common.text_normalize import normalize_text
from src.kg.models import KGResult, KGEntity, KGEdge

logger = logging.getLogger(__name__)


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


_bee_attr_dict: dict | None = None
_SOURCE_BACKED_KEYWORD_CONFIDENCE = 0.8


def _get_bee_attr_dict() -> dict:
    global _bee_attr_dict
    if _bee_attr_dict is None:
        from src.common.config_loader import load_yaml
        _bee_attr_dict = load_yaml("bee_attr_dict.yaml")
    return _bee_attr_dict


# ---------------------------------------------------------------------------
# P7-2 C1: NLP type resolution (mistyped Concern/Goal surface forms)
#
# The NER/relation model has no Concern/Goal entity type of its own — a
# concern/goal word like "건조" or "보습" gets tagged with whatever generic
# NER group the model happened to pick (usually CAT/Category, sometimes
# PER/Event/etc.), which maps to a GraphRapping type the predicate contract
# (configs/predicate_contracts.csv) does not allow for that predicate side
# (e.g. `affects` requires object_type=Concern, not Category). The fact is
# then rejected by CanonicalFactBuilder.add_fact's contract check and lands
# in quarantine_projection_miss as PREDICATE_CONTRACT_VIOLATION — even though
# the surface form is a perfectly valid, dictionary-registered concern/goal.
#
# This resolves that class of rejection BEFORE the contract gate runs, using
# dictionary membership as the *only* gate (configs/concern_dict.yaml /
# configs/goal_alias_map.yaml — exact key match on raw or normalized surface
# form). No heuristic/partial-match normalization is used here: retyping an
# entity is a stronger claim than declining a keyword, so the bar is a human
# curated vocabulary hit, never a guess. This bounds blast radius to words a
# domain curator already vetted, and never invents a Concern/Goal that
# wouldn't otherwise exist in the vocabulary.
#
# A type is only ever promoted (Category/etc. -> Concern/Goal) when doing so
# would let the fact clear a predicate-contract slot it currently fails —
# an already-valid type is left untouched, so this is purely additive: it
# cannot turn a previously-accepted fact into a rejected one.
# ---------------------------------------------------------------------------

# Types this resolver is allowed to promote *to*. Kept as an explicit allow-
# list (not "any type in the contract") so a future contract addition
# (e.g. a new UserSegment slot) doesn't silently start being resolved by
# dictionaries that were never curated for it.
_RESOLVABLE_TARGET_TYPES = ("Concern", "Goal")


def _lookup_concern_concept_id(word: str) -> str | None:
    """Exact concern_dict.yaml membership lookup (raw or normalized key)."""
    if not word:
        return None
    concern_dict = load_concern_dict()
    entry = concern_dict.get(word) or concern_dict.get(normalize_text(word))
    if isinstance(entry, dict) and entry.get("concept_id"):
        return str(entry["concept_id"])
    return None


def _lookup_goal_token(word: str) -> str | None:
    """Exact goal_alias_map.yaml membership lookup (raw or normalized key)."""
    if not word:
        return None
    goal_map = load_goal_alias_map()
    canonical = goal_map.get(word) or goal_map.get(normalize_text(word))
    if canonical:
        return str(canonical)
    return None


_TARGET_TYPE_LOOKUP = {
    "Concern": _lookup_concern_concept_id,
    "Goal": _lookup_goal_token,
}


def _resolve_mistyped_concept(predicate: str, side: str, current_type: str, word: str) -> tuple[str, str] | None:
    """Conservative NLP type resolution for one side (subject/object) of an edge.

    Returns (new_gr_type, concept_key) if `word` is a dictionary member of a
    type the predicate contract allows on this side (and `current_type` is
    not already allowed), else None.
    """
    contract = load_predicate_contracts().get(predicate)
    if not contract:
        return None
    allowed_raw = contract.get("allowed_subject_types" if side == "subject" else "allowed_object_types", "")
    allowed = set(allowed_raw.split("|")) if allowed_raw else set()
    if not allowed or current_type in allowed:
        return None  # no contract restriction on this side, or already valid

    for target_type in _RESOLVABLE_TARGET_TYPES:
        if target_type not in allowed:
            continue
        concept_key = _TARGET_TYPE_LOOKUP[target_type](word)
        if concept_key:
            return target_type, concept_key
    return None


def _register_resolved_entity(builder: CanonicalFactBuilder, gr_type: str, concept_key: str, display_word: str) -> str:
    """Register the retyped entity under its corrected concept IRI and return it.

    The original (mistyped) entity registered earlier in `kg_result_to_facts`
    step 1 is left in place — it is simply unused by this fact now that the
    corrected IRI takes over for it. Idempotent: safe to call once per edge
    even if the same concern/goal recurs across many edges in a review.
    """
    iri = make_concept_iri(gr_type, concept_key)
    builder.register_entity(CanonicalEntity(
        entity_iri=iri,
        entity_type=gr_type,
        canonical_name=display_word or concept_key,
        canonical_name_norm=normalize_text(display_word) if display_word else concept_key,
    ))
    return iri


def to_graphrapping_iri(kg_entity: KGEntity) -> str:
    """Convert KG entity to GraphRapping concept IRI."""
    gr_type = _KG_TYPE_TO_GR_TYPE.get(kg_entity.entity_type, kg_entity.entity_type)

    if kg_entity.entity_type == "BEE_ATTR":
        # BEE_ATTR: use canonical attr_id from bee_attr_dict (not raw Korean label)
        raw_label = kg_entity.bee_type or kg_entity.word or ""
        bee_dict = _get_bee_attr_dict()
        entry = bee_dict.get(raw_label) or bee_dict.get(normalize_text(raw_label))
        if entry and entry.get("attr_id"):
            return make_concept_iri(gr_type, entry["attr_id"])
        # Fallback: use normalized label
        return make_concept_iri(gr_type, normalize_text(raw_label))
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


def _is_bee_edge(edge: KGEdge, kg_subj: KGEntity | None, kg_obj: KGEntity | None) -> bool:
    """Check if an edge involves BEE_ATTR or KEYWORD entities."""
    return bool(
        (kg_subj is not None and kg_subj.entity_type in ("BEE_ATTR", "KEYWORD")) or
        (kg_obj is not None and kg_obj.entity_type in ("BEE_ATTR", "KEYWORD"))
    )


def _classify_promotion(edge: KGEdge, kg_subj: KGEntity | None, kg_obj: KGEntity | None) -> str:
    """Classify an edge for promotion gate.

    Returns PromotionDecision value.

    P3-4 priority order — terminal/strict decisions first, then weaker gates:
      DROP → QUARANTINE → KEEP_EVIDENCE_ONLY → PROMOTE
    This makes corpus-quality protection (low confidence, auto-candidates)
    win over downstream markers (BEE unlinked, BEE_SYNTHETIC). A
    low-confidence BEE_SYNTHETIC edge is dropped rather than retained as
    evidence-only.
    """
    # DROP: low confidence — terminal, never promoted
    if edge.confidence is not None and edge.confidence < 0.2:
        return PromotionDecision.DROP

    # QUARANTINE: auto-generated keyword candidates need review
    if edge.evidence_kind == "AUTO_KEYWORD":
        return PromotionDecision.QUARANTINE

    # KEEP_EVIDENCE_ONLY: BEE-related markers preserved for trace but not signaled
    if _is_bee_edge(edge, kg_subj, kg_obj) and edge.target_linked is False:
        return PromotionDecision.KEEP_EVIDENCE_ONLY
    if edge.evidence_kind == "BEE_SYNTHETIC":
        return PromotionDecision.KEEP_EVIDENCE_ONLY

    # PROMOTE: standard relations (evidence_kind=None, NER-NER, NER-BeE)
    return PromotionDecision.PROMOTE


def _is_source_backed_keyword_edge(
    edge: KGEdge,
    kg_subj: KGEntity | None,
    kg_obj: KGEntity | None,
) -> bool:
    """Return True for validated review keyword edges, excluding auto candidates."""
    if edge.relation_type != "HAS_KEYWORD":
        return False
    if kg_subj is None or kg_obj is None:
        return False
    if kg_subj.entity_type != "BEE_ATTR" or kg_obj.entity_type != "KEYWORD":
        return False
    return edge.evidence_kind not in ("AUTO_KEYWORD", "BEE_SYNTHETIC")


def _fact_evidence_kind(edge: KGEdge, kg_subj: KGEntity | None, kg_obj: KGEntity | None) -> str | None:
    """Normalize source-backed KG keyword evidence for projection policy."""
    if _is_source_backed_keyword_edge(edge, kg_subj, kg_obj):
        return "BEE_DICT"
    return edge.evidence_kind


def _fact_confidence(edge: KGEdge, kg_subj: KGEntity | None, kg_obj: KGEntity | None) -> float | None:
    """Preserve KG confidence, adding the KG NER-BeE default for validated keywords."""
    if edge.confidence is not None:
        return edge.confidence
    if _is_source_backed_keyword_edge(edge, kg_subj, kg_obj):
        return _SOURCE_BACKED_KEYWORD_CONFIDENCE
    return None


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
    # P7-2 C1 monitoring counter (not part of the `stats` contract other
    # callers assert on exactly — kept as a local + a single log line so a
    # future relation-model improvement's effect on the contract-violation
    # rejection rate can be grepped for, without widening the stats dict).
    type_resolved_count = 0

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
        fact_evidence_kind = _fact_evidence_kind(edge, kg_subj, kg_obj)
        fact_confidence = _fact_confidence(edge, kg_subj, kg_obj)

        # Determine predicate, polarity, modality from actual KG data
        predicate = edge.relation_type.lower()
        if edge.relation_type == "HAS_KEYWORD":
            predicate = "HAS_KEYWORD"  # keep uppercase — registry expects this exact case
        # has_attribute stays lowercase — registry maps has_attribute(Product, BEEAttr) -> BEE_ATTR

        # P7-2 C1: conservative NLP type resolution, BEFORE the predicate
        # contract gate (CanonicalFactBuilder.add_fact) would otherwise reject
        # a mistyped-but-dictionary-valid concern/goal surface form. See the
        # module-level docstring above _resolve_mistyped_concept for the why.
        # The retyped side keeps the edge's original word/polarity/sentiment —
        # only its type + IRI are corrected, and the pre-resolution type is
        # preserved as a fact qualifier for audit (never silently overwritten).
        type_resolution_qualifiers: list[FactQualifier] = []

        subj_word = kg_subj.word if kg_subj else ""
        subj_resolved = _resolve_mistyped_concept(predicate, "subject", subj_type, subj_word)
        if subj_resolved:
            new_subj_type, concept_key = subj_resolved
            subj_iri = _register_resolved_entity(builder, new_subj_type, concept_key, subj_word)
            type_resolution_qualifiers.append(FactQualifier(
                qualifier_key="type_resolved_from_subject",
                qualifier_type="string",
                qualifier_value_text=subj_type,
            ))
            subj_type = new_subj_type

        obj_word = kg_obj.word if kg_obj else ""
        obj_resolved = _resolve_mistyped_concept(predicate, "object", obj_type, obj_word)
        if obj_resolved:
            new_obj_type, concept_key = obj_resolved
            obj_iri = _register_resolved_entity(builder, new_obj_type, concept_key, obj_word)
            type_resolution_qualifiers.append(FactQualifier(
                qualifier_key="type_resolved_from_object",
                qualifier_type="string",
                qualifier_value_text=obj_type,
            ))
            obj_type = new_obj_type

        type_resolved_count += len(type_resolution_qualifiers)

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
            confidence=fact_confidence,
            source_modality=modality,
            provenance=FactProvenance(
                raw_table="bee_raw" if is_bee else "rel_raw",
                raw_row_id=str(review_idx),
                review_id=review_id,
                source_modality=modality,
            ),
            qualifiers=type_resolution_qualifiers or None,
            negated=edge.negated,
            intensity=edge.intensity,
            evidence_kind=fact_evidence_kind,
            fact_status=fact_status,
        )

        if decision == PromotionDecision.PROMOTE:
            stats["promoted"] += 1
        else:
            stats["evidence_only"] += 1

    if type_resolved_count:
        # P7-2 C1 monitoring line: grep "KG type resolution" to track the
        # Concern/Goal rescue rate over time. As the relation-model's NLP
        # typing improves, this count (and the residual contract-violation
        # rate in quarantine_projection_miss) should both trend down —
        # a rising resolution count alongside a flat/rising violation count
        # would flag the dictionary falling behind the model's vocabulary.
        logger.info(
            "KG type resolution: %d edge side(s) retyped to Concern/Goal via "
            "dictionary membership (review=%s)", type_resolved_count, review_id,
        )

    return stats
