"""
User fact canonicalization: user raw/summary → canonical_user_fact.

Flow: user raw → adapted facts → canonical_fact (subject_type='User')
      → agg_user_preference → serving_user_profile
"""

from __future__ import annotations

from typing import Any

from src.common.ids import make_fact_id
from src.common.enums import ObjectRefKind, USER_PREFERENCE_EDGE_TYPES


def canonicalize_user_facts(
    user_id: str,
    adapted_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert adapted user preference facts into canonical_fact rows.

    Args:
        user_id: Real user_id
        adapted_facts: Output from personal_agent_adapter.adapt_user_profile()

    Returns:
        List of canonical_fact row dicts ready for DB insert
    """
    canonical_facts = []
    user_iri = f"user:{user_id}"

    for af in adapted_facts:
        predicate = af["predicate"]
        if predicate not in USER_PREFERENCE_EDGE_TYPES:
            continue

        concept_id = af.get("concept_id", "")
        fact_id = make_fact_id(
            review_id="",  # user facts have no review
            subject_iri=user_iri,
            predicate=predicate,
            object_ref=concept_id,
        )

        canonical_facts.append({
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
            "source_modalities": [af.get("source", "user_profile")],
        })

    return canonical_facts


def build_user_preference_rows(
    user_id: str,
    canonical_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert canonical user facts to agg_user_preference rows.

    Args:
        user_id: Real user_id
        canonical_facts: Output from canonicalize_user_facts()

    Returns:
        List of agg_user_preference row dicts
    """
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
