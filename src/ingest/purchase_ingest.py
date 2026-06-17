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
    repurchased_family_ids: set[str]
    last_seen_at: str | None = None


def derive_purchase_features(
    purchases: list[PurchaseEvent],
    brand_lookup: dict[str, str] | None = None,      # product_id → brand_id (raw normalized, e.g. "b1")
    category_lookup: dict[str, str] | None = None,    # product_id → category_id
    family_lookup: dict[str, str] | None = None,      # product_id → variant_family_id
) -> PurchaseFeatures:
    """Derive purchase-based recommendation features.

    Lookup ID domain: raw normalized id from product_master (e.g. "b1"), not concept IRI.
    The downstream adapt_user_profile() builds concept IRIs via make_concept_iri().

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
    family_purchase_count: dict[str, int] = {}

    for p in purchases:
        owned_product_ids.add(p.product_id)

        fam = family_lookup.get(p.product_id)
        if fam:
            owned_family_ids.add(fam)
            family_purchase_count[fam] = family_purchase_count.get(fam, 0) + 1

        brand = brand_lookup.get(p.product_id)
        if brand:
            brand_purchase_count[brand] = brand_purchase_count.get(brand, 0) + 1

        cat = category_lookup.get(p.product_id)
        if cat:
            category_purchase_count[cat] = category_purchase_count.get(cat, 0) + 1

    # Repurchase: 2+ distinct purchase events for same brand/category/family
    repurchased_brand_ids = {b for b, c in brand_purchase_count.items() if c >= 2}
    repurchased_category_ids = {c for c, cnt in category_purchase_count.items() if cnt >= 2}
    repurchased_family_ids = {f for f, cnt in family_purchase_count.items() if cnt >= 2}

    # Recently purchased = all purchased brands (recency filtering deferred to caller)
    recently_purchased_brand_ids = set(brand_purchase_count.keys())

    # last_seen_at: max purchased_at across events (None if no purchased_at provided)
    purchased_ats = [p.purchased_at for p in purchases if p.purchased_at]
    last_seen_at = max(purchased_ats) if purchased_ats else None

    return PurchaseFeatures(
        owned_product_ids=owned_product_ids,
        owned_family_ids=owned_family_ids,
        recently_purchased_brand_ids=recently_purchased_brand_ids,
        repurchased_brand_ids=repurchased_brand_ids,
        repurchased_category_ids=repurchased_category_ids,
        repurchased_family_ids=repurchased_family_ids,
        last_seen_at=last_seen_at,
    )


def build_product_lookups_from_masters(
    product_masters: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build (brand_lookup, category_lookup, family_lookup) from product_master rows.

    Returns raw normalized IDs (e.g. brand_id="b1"), not concept IRIs.
    Used by entry points (run_full_load, load_demo_data) to feed
    load_users_from_profiles() and derive_purchase_features().
    """
    brand_lookup = {
        pid: m["brand_id"]
        for pid, m in product_masters.items()
        if m.get("brand_id")
    }
    category_lookup = {
        pid: m["category_id"]
        for pid, m in product_masters.items()
        if m.get("category_id")
    }
    family_lookup = {
        pid: m["variant_family_id"]
        for pid, m in product_masters.items()
        if m.get("variant_family_id")
    }
    return brand_lookup, category_lookup, family_lookup


def purchase_features_to_adapter_dict(pf: PurchaseFeatures) -> dict[str, Any]:
    """Convert PurchaseFeatures dataclass to dict shape expected by adapt_user_profile().

    adapt_user_profile() reads via purchase_features.get(key, default) which requires
    a dict interface. Keep this conversion in one place to make the contract explicit.

    Returns deterministic sorted lists for set fields (test stability).
    """
    return {
        "owned_product_ids": sorted(pf.owned_product_ids),
        "owned_family_ids": sorted(pf.owned_family_ids),
        "repurchased_family_ids": sorted(pf.repurchased_family_ids),
        "repurchased_brand_ids": sorted(pf.repurchased_brand_ids),
        "recently_purchased_brand_ids": sorted(pf.recently_purchased_brand_ids),
        "last_seen_at": pf.last_seen_at,
    }
