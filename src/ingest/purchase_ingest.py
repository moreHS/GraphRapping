"""
Purchase event ingest.

Loads purchase events for brand confidence weighting,
loyalty/repurchase analysis, and hard exclusion/availability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PurchaseEvent:
    purchase_event_id: str
    user_id: str
    product_id: str
    purchased_at: str | None = None
    price: float | None = None
    quantity: int = 1
    channel: str | None = None


def ingest_purchase(event: PurchaseEvent) -> dict[str, Any]:
    """Transform a purchase event into a purchase_event_raw row."""
    return {
        "purchase_event_id": event.purchase_event_id,
        "user_id": event.user_id,
        "product_id": event.product_id,
        "purchased_at": event.purchased_at,
        "price": event.price,
        "quantity": event.quantity,
        "channel": event.channel,
    }


def derive_brand_confidence(
    purchases: list[PurchaseEvent],
    brand_lookup: dict[str, str],  # product_id → brand_id
) -> dict[str, float]:
    """Derive brand confidence weights from purchase history.

    Returns: brand_id → confidence (0.0~1.0)
    Purchase-based brands get higher confidence than chat-based.
    """
    brand_counts: dict[str, int] = {}
    for p in purchases:
        brand_id = brand_lookup.get(p.product_id)
        if brand_id:
            brand_counts[brand_id] = brand_counts.get(brand_id, 0) + p.quantity

    if not brand_counts:
        return {}

    max_count = max(brand_counts.values())
    return {
        brand_id: min(count / max_count, 1.0)
        for brand_id, count in brand_counts.items()
    }


@dataclass
class PurchaseFeatures:
    """Derived purchase features for recommendation."""
    owned_product_ids: set[str]
    owned_family_ids: set[str]
    recently_purchased_brand_ids: set[str]
    repurchased_brand_ids: set[str]
    repurchased_category_ids: set[str]


def derive_purchase_features(
    purchases: list[PurchaseEvent],
    brand_lookup: dict[str, str] | None = None,      # product_id → brand_id
    category_lookup: dict[str, str] | None = None,    # product_id → category_id
    family_lookup: dict[str, str] | None = None,      # product_id → variant_family_id
) -> PurchaseFeatures:
    """Derive purchase-based recommendation features.

    Args:
        purchases: User's purchase events
        brand_lookup: product_id → brand_id mapping
        category_lookup: product_id → category_id mapping
        family_lookup: product_id → variant_family_id mapping
    """
    brand_lookup = brand_lookup or {}
    category_lookup = category_lookup or {}
    family_lookup = family_lookup or {}

    owned_product_ids: set[str] = set()
    owned_family_ids: set[str] = set()
    brand_purchase_count: dict[str, int] = {}
    category_purchase_count: dict[str, int] = {}

    for p in purchases:
        owned_product_ids.add(p.product_id)

        fam = family_lookup.get(p.product_id)
        if fam:
            owned_family_ids.add(fam)

        brand = brand_lookup.get(p.product_id)
        if brand:
            brand_purchase_count[brand] = brand_purchase_count.get(brand, 0) + 1

        cat = category_lookup.get(p.product_id)
        if cat:
            category_purchase_count[cat] = category_purchase_count.get(cat, 0) + 1

    # Repurchase: 2+ distinct purchase events for same brand/category
    repurchased_brand_ids = {b for b, c in brand_purchase_count.items() if c >= 2}
    repurchased_category_ids = {c for c, cnt in category_purchase_count.items() if cnt >= 2}

    # Recently purchased = all purchased brands (recency filtering deferred to caller)
    recently_purchased_brand_ids = set(brand_purchase_count.keys())

    return PurchaseFeatures(
        owned_product_ids=owned_product_ids,
        owned_family_ids=owned_family_ids,
        recently_purchased_brand_ids=recently_purchased_brand_ids,
        repurchased_brand_ids=repurchased_brand_ids,
        repurchased_category_ids=repurchased_category_ids,
    )
