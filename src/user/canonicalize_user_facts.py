"""
User fact canonicalization: user raw/summary → canonical_user_fact.

Flow: user raw → adapted facts → canonical_fact (subject_type='User')
      → agg_user_preference → serving_user_profile

Fact families:
  - State: skin_type, skin_tone, age_band
  - Concern: HAS_CONCERN
  - Goal: WANTS_GOAL, WANTS_EFFECT
  - Context: PREFERS_CONTEXT
  - Behavior: brand/category/ingredient prefs, purchase, avoidance
"""

from __future__ import annotations

from typing import Any

from src.common.ids import make_fact_id
from src.common.enums import (
    ObjectRefKind,
    USER_STATE_EDGE_TYPES, USER_CONCERN_EDGE_TYPES,
    USER_GOAL_EDGE_TYPES, USER_CONTEXT_EDGE_TYPES,
    USER_BEHAVIOR_EDGE_TYPES,
)

_SOURCE_KIND_MAP: dict[str, str] = {
    "purchase": "derived",
    "chat": "summary",
    "basic": "master",
    "user_profile": "master",
}


def _build_facts_for_family(
    user_iri: str,
    adapted_facts: list[dict[str, Any]],
    allowed_predicates: frozenset[str],
) -> list[dict[str, Any]]:
    """Build canonical facts for a specific fact family."""
    facts = []
    for af in adapted_facts:
        predicate = af["predicate"]
        if predicate not in allowed_predicates:
            continue

        concept_id = af.get("concept_id", "")
        fact_id = make_fact_id(
            review_id="",
            subject_iri=user_iri,
            predicate=predicate,
            object_ref=concept_id,
        )

        source = af.get("source", "user_profile")
        facts.append({
            "fact_id": fact_id,
            "review_id": None,
            "subject_iri": user_iri,
            "predicate": predicate,
            "object_iri": concept_id,
            "object_value_text": af.get("concept_value"),
            "object_ref_kind": ObjectRefKind.CONCEPT,
            "subject_type": "User",
            "object_type": af.get("concept_type", ""),
            "polarity": None,
            "confidence": af.get("confidence"),
            "source_modalities": [source],
            "last_seen_at": af.get("last_seen_at"),
            "provenance": {
                "source_domain": "user",
                "source_kind": _SOURCE_KIND_MAP.get(source, "derived"),
            },
        })

    return facts


def build_state_facts(user_id: str, adapted_facts: list[dict]) -> list[dict]:
    """State facts: skin_type, skin_tone, age_band."""
    return _build_facts_for_family(f"user:{user_id}", adapted_facts, USER_STATE_EDGE_TYPES)


def build_concern_facts(user_id: str, adapted_facts: list[dict]) -> list[dict]:
    """Concern facts: HAS_CONCERN."""
    return _build_facts_for_family(f"user:{user_id}", adapted_facts, USER_CONCERN_EDGE_TYPES)


def build_goal_facts(user_id: str, adapted_facts: list[dict]) -> list[dict]:
    """Goal facts: WANTS_GOAL, WANTS_EFFECT."""
    return _build_facts_for_family(f"user:{user_id}", adapted_facts, USER_GOAL_EDGE_TYPES)


def build_context_facts(user_id: str, adapted_facts: list[dict]) -> list[dict]:
    """Context facts: PREFERS_CONTEXT."""
    return _build_facts_for_family(f"user:{user_id}", adapted_facts, USER_CONTEXT_EDGE_TYPES)


def build_behavior_facts(user_id: str, adapted_facts: list[dict]) -> list[dict]:
    """Behavior facts: brand/category/ingredient/keyword prefs, purchase, avoidance."""
    return _build_facts_for_family(f"user:{user_id}", adapted_facts, USER_BEHAVIOR_EDGE_TYPES)


def canonicalize_user_facts(
    user_id: str,
    adapted_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert adapted user preference facts into canonical_fact rows.

    Delegates to 5 family builders, maintaining backward compatibility.
    """
    facts = []
    facts.extend(build_state_facts(user_id, adapted_facts))
    facts.extend(build_concern_facts(user_id, adapted_facts))
    facts.extend(build_goal_facts(user_id, adapted_facts))
    facts.extend(build_context_facts(user_id, adapted_facts))
    facts.extend(build_behavior_facts(user_id, adapted_facts))
    return facts


def build_user_preference_rows(
    user_id: str,
    canonical_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert canonical user facts to agg_user_preference rows."""
    rows = []
    seen = set()

    for fact in canonical_facts:
        predicate = fact["predicate"]
        dst_id = fact.get("object_iri", "")
        dedup_key = (user_id, predicate, dst_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        rows.append({
            "user_id": user_id,
            "preference_edge_type": predicate,
            "dst_node_type": fact.get("object_type", ""),
            "dst_node_id": dst_id,
            "weight": fact.get("confidence", 1.0) or 1.0,
            "source_mix": {"sources": fact.get("source_modalities", [])},
        })

    return rows
