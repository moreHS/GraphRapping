"""
BEE_ATTR → Concern bridge: discounted concern matching from BEE attributes.

When a product has strong positive BEE_ATTR signals (e.g., 보습력 POS),
and a mapping exists in concern_bee_attr_map.yaml (보습력 → concern_dryness),
we can infer that the product likely addresses that concern — but with
lower confidence than an explicit concern signal from reviews.

This bridge runs at candidate overlap time, NOT at canonical fact/signal level.
It does NOT create new ADDRESSES_CONCERN_SIGNAL signals.
"""

from __future__ import annotations

from src.common.config_loader import load_concern_bee_attr_map


def compute_bridged_concerns(top_bee_attr_ids: list[dict]) -> dict[str, dict]:
    """Compute concern IDs bridged from BEE_ATTR signals.

    Args:
        top_bee_attr_ids: Product serving profile's top_bee_attr_ids
            (list of {id, score, review_cnt})

    Returns:
        Dict of concern_id → {score, source_attr_id, label_ko}
        Only includes concerns where attr score > 0 and mapping exists.
    """
    bridge_map = load_concern_bee_attr_map()
    if not bridge_map:
        return {}

    result: dict[str, dict] = {}

    for attr_item in top_bee_attr_ids:
        if not isinstance(attr_item, dict):
            continue

        attr_id = attr_item.get("id", "")
        attr_score = attr_item.get("score", 0)

        # Only bridge positive signals
        if not attr_score or attr_score <= 0:
            continue

        # Strip concept:BEEAttr: prefix to match config keys
        attr_key = attr_id
        if attr_key.startswith("concept:BEEAttr:"):
            attr_key = attr_key[len("concept:BEEAttr:"):]

        mapping = bridge_map.get(attr_key)
        if not mapping or not isinstance(mapping, dict):
            continue

        concern_id = mapping.get("concern_id", "")
        weight = mapping.get("weight", 0.5)
        label = mapping.get("label_ko", "")

        if not concern_id:
            continue

        bridge_score = attr_score * weight

        # Keep max score per concern
        if concern_id not in result or bridge_score > result[concern_id]["score"]:
            result[concern_id] = {
                "score": bridge_score,
                "source_attr_id": attr_key,
                "label_ko": label,
            }

    return result
