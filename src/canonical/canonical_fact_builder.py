"""
Canonical Fact Builder: Layer 2 fact generation.

Responsibilities:
  - resolved mention → canonical_entity upsert
  - normalized triple/value → canonical_fact upsert
  - raw row link → fact_provenance insert
  - qualifier → fact_qualifier insert
  - multi-modality same fact → source_modalities[] union
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.ids import make_fact_id, make_qualifier_fingerprint, make_concept_iri
from src.common.enums import ObjectRefKind


@dataclass
class CanonicalEntity:
    entity_iri: str
    entity_type: str
    canonical_name: str
    canonical_name_norm: str
    source_system: str = "review_extraction"
    source_key: str | None = None
    match_confidence: float | None = None
    attrs: dict | None = None


@dataclass
class FactProvenance:
    raw_table: str       # ner_raw|bee_raw|rel_raw|user_master|product_master|manual
    raw_row_id: str
    review_id: str
    snippet: str = ""
    start_offset: int | None = None
    end_offset: int | None = None
    source_modality: str = ""  # NER|BEE|REL (review-derived only)
    evidence_rank: int = 0
    # Generic provenance fields (supports review/user/product/manual/system facts)
    source_domain: str = "review"   # review|user|product|manual|system
    source_kind: str = "raw"        # raw|summary|master|derived


@dataclass
class FactQualifier:
    qualifier_key: str
    qualifier_type: str
    qualifier_iri: str | None = None
    qualifier_value_text: str | None = None
    qualifier_value_num: float | None = None


@dataclass
class CanonicalFact:
    fact_id: str
    review_id: str
    subject_iri: str
    predicate: str
    object_iri: str | None = None
    object_value_text: str | None = None
    object_value_num: float | None = None
    object_ref_kind: str = ObjectRefKind.CONCEPT
    subject_ref_kind: str = ""  # CONCEPT|ENTITY — for reverse transform dst_ref_kind
    subject_type: str = ""
    object_type: str = ""
    polarity: str | None = None
    confidence: float | None = None
    source_modalities: list[str] = field(default_factory=list)
    extraction_version: str | None = None
    registry_version: str | None = None
    provenance: list[FactProvenance] = field(default_factory=list)
    qualifiers: list[FactQualifier] = field(default_factory=list)
    negated: bool | None = None
    intensity: float | None = None
    evidence_kind: str | None = None
    fact_status: str = "CANONICAL_PROMOTED"  # EVIDENCE_ONLY|CANONICAL_PROMOTED|REJECTED
    # BEE target attribution (set by adapter, used by signal emitter as defense-in-depth)
    target_linked: bool | None = None
    attribution_source: str | None = None


class CanonicalFactBuilder:
    """Builds Layer 2 canonical facts from normalized extraction results.

    Accumulates entities and facts for a review, then outputs them.
    Handles dedup: same semantic fact from multiple modalities → one fact + multiple provenance.
    """

    def __init__(self, predicate_contracts: dict[str, dict] | None = None) -> None:
        self._entities: dict[str, CanonicalEntity] = {}
        self._facts: dict[str, CanonicalFact] = {}
        self._invalid_facts: list[dict] = []
        self._contracts = predicate_contracts  # predicate → {allowed_subject_types, allowed_object_types}

    def reset(self) -> None:
        self._entities.clear()
        self._facts.clear()
        self._invalid_facts.clear()

    @property
    def invalid_facts(self) -> list[dict]:
        return list(self._invalid_facts)

    # --- Entity registration ---

    def register_entity(self, entity: CanonicalEntity) -> None:
        """Register or update a canonical entity."""
        existing = self._entities.get(entity.entity_iri)
        if existing:
            if entity.match_confidence and (not existing.match_confidence or entity.match_confidence > existing.match_confidence):
                self._entities[entity.entity_iri] = entity
        else:
            self._entities[entity.entity_iri] = entity

    # --- Fact building ---

    def add_fact(
        self,
        review_id: str,
        subject_iri: str,
        predicate: str,
        object_iri: str | None = None,
        object_value_text: str | None = None,
        object_value_num: float | None = None,
        object_ref_kind: str = ObjectRefKind.CONCEPT,
        subject_type: str = "",
        object_type: str = "",
        polarity: str | None = None,
        confidence: float | None = None,
        source_modality: str = "",
        provenance: FactProvenance | None = None,
        qualifiers: list[FactQualifier] | None = None,
        *,
        negated: bool | None = None,
        intensity: float | None = None,
        evidence_kind: str | None = None,
        fact_status: str = "CANONICAL_PROMOTED",
    ) -> str | None:
        """Add a canonical fact. Returns fact_id, or None if contract violation.

        If the same semantic fact already exists (same fact_id),
        the modality is added to source_modalities and provenance is appended.
        Contract violation → fact rejected, added to _invalid_facts.
        """
        # Predicate contract validation
        if self._contracts and predicate in self._contracts:
            contract = self._contracts[predicate]
            allowed_subj = contract.get("allowed_subject_types", "")
            allowed_obj = contract.get("allowed_object_types", "")
            if allowed_subj and subject_type and subject_type not in allowed_subj.split("|"):
                self._invalid_facts.append({
                    "predicate": predicate, "subject_type": subject_type,
                    "object_type": object_type, "reason": f"subject_type '{subject_type}' not in allowed '{allowed_subj}'",
                })
                return None
            if allowed_obj and object_type and object_type not in allowed_obj.split("|"):
                self._invalid_facts.append({
                    "predicate": predicate, "subject_type": subject_type,
                    "object_type": object_type, "reason": f"object_type '{object_type}' not in allowed '{allowed_obj}'",
                })
                return None

        # Auto-generate qualifiers for negation/intensity
        all_qualifiers = list(qualifiers or [])
        if negated:
            all_qualifiers.append(FactQualifier(
                qualifier_key="negated", qualifier_type="boolean",
                qualifier_value_text="true",
            ))
        if intensity is not None and intensity != 1.0:
            all_qualifiers.append(FactQualifier(
                qualifier_key="intensity", qualifier_type="float",
                qualifier_value_num=intensity,
            ))

        object_ref = object_iri or object_value_text or ""
        qualifier_pairs = [(q.qualifier_key, q.qualifier_iri or q.qualifier_value_text or str(q.qualifier_value_num or ""))
                          for q in all_qualifiers]
        qfp = make_qualifier_fingerprint(qualifier_pairs) if qualifier_pairs else ""

        fact_id = make_fact_id(
            review_id=review_id,
            subject_iri=subject_iri,
            predicate=predicate,
            object_ref=object_ref,
            polarity=polarity or "",
            qualifier_fingerprint=qfp,
        )

        existing = self._facts.get(fact_id)
        if existing:
            # Merge: add modality and provenance
            if source_modality and source_modality not in existing.source_modalities:
                existing.source_modalities.append(source_modality)
            if provenance:
                existing.provenance.append(provenance)
            if confidence and (not existing.confidence or confidence > existing.confidence):
                existing.confidence = confidence
        else:
            # Derive subject_ref_kind from subject_type
            _CONCEPT_TYPES = {
                "BEEAttr", "Keyword", "Brand", "Category", "Ingredient",
                "TemporalContext", "Concern", "Goal", "Tool", "UserSegment",
                "SkinType", "SkinTone", "Fragrance", "PriceBand", "Country", "AgeBand",
            }
            subj_ref_kind = ObjectRefKind.CONCEPT if subject_type in _CONCEPT_TYPES else ObjectRefKind.ENTITY

            fact = CanonicalFact(
                fact_id=fact_id,
                review_id=review_id,
                subject_iri=subject_iri,
                predicate=predicate,
                object_iri=object_iri,
                object_value_text=object_value_text,
                object_value_num=object_value_num,
                object_ref_kind=object_ref_kind,
                subject_ref_kind=subj_ref_kind,
                subject_type=subject_type,
                object_type=object_type,
                polarity=polarity,
                confidence=confidence,
                source_modalities=[source_modality] if source_modality else [],
                provenance=[provenance] if provenance else [],
                qualifiers=all_qualifiers,
                negated=negated,
                intensity=intensity,
                evidence_kind=evidence_kind,
                fact_status=fact_status,
            )
            self._facts[fact_id] = fact

        return fact_id

    # --- BEE-specific helpers ---

    def add_bee_facts(
        self,
        review_id: str,
        product_iri: str,
        bee_attr_id: str,
        bee_attr_label: str,
        keyword_ids: list[str],
        keyword_labels: list[str],
        polarity: str | None = None,
        provenance: FactProvenance | None = None,
        *,
        negated: bool | None = None,
        intensity: float | None = None,
        evidence_kind: str | None = None,
        base_confidence: float | None = None,
    ) -> list[str]:
        """Add BEE-derived facts: Product→BEEAttr + BEEAttr→Keyword(s).

        Returns list of generated fact_ids.
        """
        fact_ids = []

        # Ensure BEEAttr entity
        attr_iri = make_concept_iri("BEEAttr", bee_attr_id)
        self.register_entity(CanonicalEntity(
            entity_iri=attr_iri,
            entity_type="BEEAttr",
            canonical_name=bee_attr_label,
            canonical_name_norm=bee_attr_label.lower(),
        ))

        # Product HAS_ATTRIBUTE BEEAttr
        fid = self.add_fact(
            review_id=review_id,
            subject_iri=product_iri,
            predicate="has_attribute",
            object_iri=attr_iri,
            object_ref_kind=ObjectRefKind.CONCEPT,
            subject_type="Product",
            object_type="BEEAttr",
            polarity=polarity,
            confidence=base_confidence,
            source_modality="BEE",
            provenance=provenance,
            negated=negated,
            intensity=intensity,
            evidence_kind=evidence_kind,
        )
        if fid is not None:
            fact_ids.append(fid)

        # BEEAttr HAS_KEYWORD Keyword (for each keyword)
        for kid, klabel in zip(keyword_ids, keyword_labels):
            kw_iri = make_concept_iri("Keyword", kid)
            self.register_entity(CanonicalEntity(
                entity_iri=kw_iri,
                entity_type="Keyword",
                canonical_name=klabel,
                canonical_name_norm=klabel.lower(),
            ))
            fid = self.add_fact(
                review_id=review_id,
                subject_iri=attr_iri,
                predicate="HAS_KEYWORD",
                object_iri=kw_iri,
                object_ref_kind=ObjectRefKind.CONCEPT,
                subject_type="BEEAttr",
                object_type="Keyword",
                polarity=polarity,
                confidence=base_confidence,
                source_modality="BEE",
                provenance=provenance,
                negated=negated,
                intensity=intensity,
                evidence_kind=evidence_kind,
            )
            if fid is not None:
                fact_ids.append(fid)

        return fact_ids

    # --- Output ---

    @property
    def entities(self) -> list[CanonicalEntity]:
        return list(self._entities.values())

    @property
    def facts(self) -> list[CanonicalFact]:
        return list(self._facts.values())

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def fact_count(self) -> int:
        return len(self._facts)
