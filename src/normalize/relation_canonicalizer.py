"""
Relation canonicalizer: raw relation → 65 canonical predicates.

Idempotent: if input is already canonical, pass through.
Unknown predicates → quarantine.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.config_loader import load_json


# The 65 canonical predicates + special handling
CANONICAL_PREDICATES = frozenset({
    "used_by", "applied_to", "used_for", "used_on", "used_with",
    "uses", "applied_by", "time_of_use", "duration_of_use", "frequency_of_use",
    "not_used_by", "experienced_by", "experiences",
    "affects", "affected_by", "benefits", "benefits_user",
    "causes", "caused_by", "treats", "addressed_by_treatment",
    "has_attribute", "attribute_of", "has_ingredient", "ingredient_of",
    "has_part", "part_of", "has_instance", "instance_of",
    "variant_of", "belongs_to",
    "describes", "described_by", "perceives", "perceived_by",
    "related_to", "addresses",
    "comparison_with", "recommended_by", "recommended_to",
    "targeted_at", "targeted_by", "addressed_to", "available_to",
    "purchases", "purchased_by", "sells", "sold_by",
    "provided_to", "provided_by", "gifted_by", "gifted_to",
    "owns", "owned_by", "price_of", "available_in",
    "requires", "required_by",
    "produces", "produced_by", "brand_of",
    "information_from", "information_to",
    "same_entity", "no_relationship",
    "child_of", "parent_of", "family_member_of",
})

# Predicates that are not stored as edges
PREPROCESS_ONLY = frozenset({"same_entity"})
DROP_PREDICATES = frozenset({"no_relationship"})


@dataclass
class CanonicalizeResult:
    canonical_predicate: str | None
    action: str  # KEEP|REVERSE_KEEP|PREPROCESS_ONLY|DROP|QUARANTINE
    direction_reversed: bool = False
    is_helper: bool = False


class RelationCanonicalizer:
    """Maps raw relation labels to 65 canonical predicates.

    Idempotent: already-canonical inputs pass through unchanged.
    """

    def __init__(self) -> None:
        # raw_label_lower → canonical_predicate
        self._mapping: dict[str, str] = {}

    def load(self, filename: str = "relation_canonical_map.json") -> None:
        """Load 633→65 mapping from Relation project's canonical mapping."""
        data = load_json(filename)
        self._mapping.clear()
        label_to_canonical = data.get("label_to_canonical", {})
        for raw_label, canonical in label_to_canonical.items():
            self._mapping[raw_label.lower().strip()] = canonical

    def load_from_dict(self, mapping: dict[str, str]) -> None:
        self._mapping.clear()
        for raw, canonical in mapping.items():
            self._mapping[raw.lower().strip()] = canonical

    def canonicalize(self, relation_raw: str) -> CanonicalizeResult:
        """Canonicalize a raw relation label.

        Idempotent: if already canonical, returns as-is.
        """
        norm = relation_raw.lower().strip()

        # Already canonical?
        if norm in CANONICAL_PREDICATES:
            if norm in PREPROCESS_ONLY:
                return CanonicalizeResult(
                    canonical_predicate=norm,
                    action="PREPROCESS_ONLY",
                )
            if norm in DROP_PREDICATES:
                return CanonicalizeResult(
                    canonical_predicate=norm,
                    action="DROP",
                )
            return CanonicalizeResult(
                canonical_predicate=norm,
                action="KEEP",
            )

        # Try mapping
        canonical = self._mapping.get(norm)
        if canonical:
            canonical_lower = canonical.lower().strip()
            if canonical_lower in PREPROCESS_ONLY:
                return CanonicalizeResult(
                    canonical_predicate=canonical_lower,
                    action="PREPROCESS_ONLY",
                )
            if canonical_lower in DROP_PREDICATES:
                return CanonicalizeResult(
                    canonical_predicate=canonical_lower,
                    action="DROP",
                )
            return CanonicalizeResult(
                canonical_predicate=canonical_lower,
                action="KEEP",
            )

        # Unknown → quarantine
        return CanonicalizeResult(
            canonical_predicate=None,
            action="QUARANTINE",
        )

    @property
    def mapping_size(self) -> int:
        return len(self._mapping)
