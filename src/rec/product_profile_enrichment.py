"""Recommendation-facing product profile enrichment."""

from __future__ import annotations

from typing import Any


_MASTER_TRUTH_FIELDS = (
    "product_name",
    "brand_id",
    "brand_name",
    "category_id",
    "category_name",
    "country_of_origin",
    "price",
    "price_band",
    "variant_family_id",
    "representative_product_name",
    "main_benefits",
    "ingredients",
    "_es_meta",
)


def enrich_product_profile_with_master(
    serving_product: dict[str, Any],
    product_master: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overlay product-master truth onto a serving product profile.

    `serving_product_profile` intentionally carries only the mart contract.
    Local recommendation/audit/UI paths still need the richer product master
    surface for category tabs, labels, and master-truth feature inspection.
    """
    enriched = dict(serving_product)
    if not product_master:
        return enriched

    for field in _MASTER_TRUTH_FIELDS:
        value = product_master.get(field)
        if value is not None:
            enriched[field] = value

    return enriched


def enrich_product_profiles_by_master(
    serving_products: list[dict[str, Any]],
    product_masters: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    masters = product_masters or {}
    return [
        enrich_product_profile_with_master(product, masters.get(str(product.get("product_id"))))
        for product in serving_products
    ]
