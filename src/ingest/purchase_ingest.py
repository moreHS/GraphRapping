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
