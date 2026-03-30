"""
NER normalizer: NER mention → canonical entity.

Resolves NER mentions to canonical entities by type:
  PRD → Product (via product_matcher)
  BRD → Brand concept
  CAT → Category concept
  ING → Ingredient concept
  DATE → split_date() → TemporalContext/Frequency/Duration/AbsoluteDate
  COL → Color concept
  AGE → AgeBand concept
  VOL → Volume concept
  EVN → Event concept
  PER → ReviewerProxy or raw mention
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.ids import make_concept_iri, make_mention_iri
from src.common.text_normalize import normalize_text
from src.common.enums import NERType, EntityType, ConceptType
from src.normalize.date_splitter import split_date, DateSplitResult
from src.canonical.canonical_fact_builder import CanonicalEntity


# NER type → (EntityType, ConceptType or None)
_NER_TO_CANONICAL = {
    NERType.BRD: (EntityType.BRAND, ConceptType.BRAND),
    NERType.CAT: (EntityType.CATEGORY, ConceptType.CATEGORY),
    NERType.ING: (EntityType.INGREDIENT, ConceptType.INGREDIENT),
    NERType.COL: (EntityType.COLOR, None),
    NERType.AGE: (EntityType.AGE_BAND, ConceptType.AGE_BAND),
    NERType.VOL: (EntityType.VOLUME, None),
    NERType.EVN: (EntityType.TOOL, None),  # events often map to context
}


@dataclass
class NERNormalizeResult:
    entity: CanonicalEntity | None
    concept_id: str | None = None
    concept_type: str | None = None
    date_split: DateSplitResult | None = None


def normalize_ner_mention(
    mention_text: str,
    entity_group: str,
    review_id: str,
    mention_idx: int,
) -> NERNormalizeResult:
    """Normalize a single NER mention to a canonical entity.

    DATE mentions are split into sub-types via split_date().
    """
    ner_type = entity_group.upper()
    text_norm = normalize_text(mention_text)

    # DATE → 4-way split
    if ner_type == NERType.DATE:
        date_result = split_date(mention_text)
        concept_type = date_result.kind.value
        concept_id = make_concept_iri(concept_type, text_norm)
        entity = CanonicalEntity(
            entity_iri=concept_id,
            entity_type=concept_type,
            canonical_name=mention_text,
            canonical_name_norm=text_norm,
        )
        return NERNormalizeResult(
            entity=entity,
            concept_id=concept_id,
            concept_type=concept_type,
            date_split=date_result,
        )

    # Known NER types → concept
    mapping = _NER_TO_CANONICAL.get(ner_type)
    if mapping:
        entity_type, concept_type_enum = mapping
        if concept_type_enum:
            concept_id = make_concept_iri(concept_type_enum.value, text_norm)
        else:
            concept_id = make_mention_iri(review_id, mention_idx)

        entity = CanonicalEntity(
            entity_iri=concept_id,
            entity_type=entity_type.value,
            canonical_name=mention_text,
            canonical_name_norm=text_norm,
        )
        return NERNormalizeResult(
            entity=entity,
            concept_id=concept_id,
            concept_type=concept_type_enum.value if concept_type_enum else None,
        )

    # PRD/PER → handled by placeholder_resolver, not here
    # Return mention-level IRI
    mention_iri = make_mention_iri(review_id, mention_idx)
    return NERNormalizeResult(
        entity=CanonicalEntity(
            entity_iri=mention_iri,
            entity_type=entity_group,
            canonical_name=mention_text,
            canonical_name_norm=text_norm,
        ),
    )
