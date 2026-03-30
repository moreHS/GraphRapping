"""
User-side aggregate: canonical_user_fact → agg_user_preference.

Refreshes user preference summary from canonical facts + purchase history.
"""

from __future__ import annotations

from typing import Any


def refresh_user_preferences(
    user_id: str,
    canonical_user_facts: list[dict[str, Any]],
    purchase_brand_confidence: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build agg_user_preference rows from canonical user facts.

    Merges confidence from multiple sources (purchase > chat).
    """
    # Group by (predicate, dst_id)
    grouped: dict[tuple[str, str], dict] = {}

    for fact in canonical_user_facts:
        predicate = fact.get("predicate", "")
        dst_id = fact.get("object_iri", "")
        key = (predicate, dst_id)

        if key not in grouped:
            grouped[key] = {
                "user_id": user_id,
                "preference_edge_type": predicate,
                "dst_node_type": fact.get("object_type", ""),
                "dst_node_id": dst_id,
                "weight": fact.get("confidence", 1.0) or 1.0,
                "sources": set(),
            }

        existing = grouped[key]
        new_conf = fact.get("confidence", 1.0) or 1.0
        if new_conf > existing["weight"]:
            existing["weight"] = new_conf

        for mod in fact.get("source_modalities", []):
            existing["sources"].add(mod)

    # Boost brand preferences if purchase data exists
    if purchase_brand_confidence:
        for key, row in grouped.items():
            predicate, dst_id = key
            if predicate == "PREFERS_BRAND":
                brand_conf = purchase_brand_confidence.get(dst_id)
                if brand_conf:
                    row["weight"] = max(row["weight"], brand_conf)
                    row["sources"].add("purchase")

    # Convert to output rows
    results = []
    for row in grouped.values():
        sources = row.pop("sources")
        row["source_mix"] = {"sources": sorted(sources)}
        results.append(row)

    return results
