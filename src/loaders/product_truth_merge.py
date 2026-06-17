"""Pure merge rules for source-grounded product truth.

This module intentionally never derives product truth from product-name tokens.
It only accepts explicit source/catalog fields and marks missing truth as such.
"""

from __future__ import annotations

from typing import Any


PROMO_PREFIX_CHARS = ("【", "[", "(", "★", "☆", "💥", "🔔", "⏰")
_MISSING_BRAND_VALUES = {"unknown", "none", "null", "n/a", "na", "-", ""}
_SYNTHETIC_QUALITY_PREFIXES = ("SYNTHETIC", "MOCK")
_EXPLICIT_MISSING_TRUTH_QUALITIES = {
    "MISSING_SOURCE_MASTER",
    "SOURCE_KEY_COLLISION",
}


def is_placeholder_brand(value: str | None) -> bool:
    """Return True when a brand value is missing, generic, or promo text."""
    text = _clean_text(value)
    if text is None:
        return True
    if text.lower() in _MISSING_BRAND_VALUES or text in {"기타", "미상"}:
        return True
    return _starts_with_promo_prefix(text)


def merge_product_truth(
    product_master: dict[str, Any],
    source_review_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge trusted product truth into a product master dict.

    Priority is intentionally conservative:
    - `product_id` remains the source product id.
    - `source_product_id` defaults to `product_id`.
    - A non-placeholder catalog brand wins.
    - A trusted source stats brand can replace a placeholder/promo catalog brand.
    - Missing brand stays missing with `source_truth_quality`.
    """
    stats = source_review_stats or {}
    merged = dict(product_master)

    product_id = _first_text(merged, "product_id", "ONLINE_PROD_SERIAL_NUMBER") or _first_text(
        stats, "product_id", "source_product_id"
    )
    if product_id is not None:
        merged["product_id"] = product_id

    merged["source_product_id"] = (
        _first_text(merged, "source_product_id")
        or _first_text(stats, "source_product_id", "product_id")
        or product_id
    )
    merged["source_channel"] = _first_text(merged, "source_channel", "channel", "CHANNEL") or _first_text(
        stats, "source_channel", "channel", "CHANNEL"
    )
    merged["source_key_type"] = _first_text(merged, "source_key_type", "SOURCE_KEY_TYPE") or _first_text(
        stats, "source_key_type", "SOURCE_KEY_TYPE"
    )

    catalog_brand = _first_text(merged, "brand_name", "BRAND_NAME")
    stats_brand = _first_text(stats, "brand_name", "BRAND_NAME", "brnd_nm")
    catalog_brand_id = _first_text(merged, "brand_id", "BRAND_ID")
    stats_brand_id = _first_text(stats, "brand_id", "BRAND_ID", "brnd_cd")
    catalog_quality = _first_text(merged, "source_truth_quality", "SOURCE_TRUTH_QUALITY")
    stats_quality = _first_text(stats, "source_truth_quality", "SOURCE_TRUTH_QUALITY")
    catalog_quality_upper = catalog_quality.upper() if catalog_quality else None
    stats_quality_upper = stats_quality.upper() if stats_quality else None
    catalog_declares_missing = catalog_quality_upper == "MISSING_SOURCE_BRAND"
    stats_declares_missing = stats_quality_upper == "MISSING_SOURCE_BRAND"
    catalog_is_synthetic = bool(
        catalog_quality_upper
        and catalog_quality_upper.startswith(_SYNTHETIC_QUALITY_PREFIXES)
    )
    stats_is_synthetic = bool(
        stats_quality_upper
        and stats_quality_upper.startswith(_SYNTHETIC_QUALITY_PREFIXES)
    )

    brand_source = "missing"
    catalog_brand_valid = (
        catalog_brand is not None
        and not is_placeholder_brand(catalog_brand)
        and not catalog_declares_missing
    )
    stats_brand_valid = (
        stats_brand is not None
        and not is_placeholder_brand(stats_brand)
        and not stats_declares_missing
    )
    if catalog_brand_valid and not catalog_is_synthetic:
        merged["brand_name"] = catalog_brand
        merged["brand_id"] = catalog_brand_id or stats_brand_id
        brand_source = "catalog"
    elif stats_brand_valid:
        merged["brand_name"] = stats_brand
        merged["brand_id"] = stats_brand_id
        brand_source = "source_review_stats"
    elif catalog_brand_valid:
        merged["brand_name"] = catalog_brand
        merged["brand_id"] = catalog_brand_id or stats_brand_id
        brand_source = "catalog"
    else:
        merged["brand_name"] = None
        merged["brand_id"] = None

    catalog_representative_name = (
        _first_text(merged, "representative_product_name", "REPRESENTATIVE_PROD_NAME")
        or _nested_es_meta_text(merged, "REPRESENTATIVE_PROD_NAME")
    )
    stats_representative_name = (
        _first_text(stats, "representative_product_name", "REPRESENTATIVE_PROD_NAME", "rprs_prd_nm")
        or _first_text(stats, "product_name", "prd_nm", "ecp_onln_prd_nm")
    )
    product_name = _first_text(merged, "product_name", "prd_nm")
    representative_name: str | None
    if stats_representative_name and (
        brand_source == "source_review_stats"
        or catalog_declares_missing
        or catalog_is_synthetic
    ):
        representative_name = stats_representative_name
    else:
        representative_name = (
            catalog_representative_name
            or stats_representative_name
            or product_name
        )
    merged["representative_product_name"] = representative_name
    if _first_text(merged, "product_name") is None and representative_name is not None:
        merged["product_name"] = representative_name

    if (
        merged["brand_name"] is None
        and catalog_quality_upper in _EXPLICIT_MISSING_TRUTH_QUALITIES
    ):
        quality = catalog_quality
    elif merged["brand_name"] is None:
        quality = "MISSING_SOURCE_BRAND"
    elif brand_source == "catalog" and catalog_is_synthetic and catalog_quality is not None:
        quality = catalog_quality
    elif brand_source == "source_review_stats" and stats_is_synthetic and stats_quality is not None:
        quality = stats_quality
    elif representative_name is None:
        quality = "PARTIAL_SOURCE"
    else:
        quality = "SOURCE_GROUNDED"
    merged["source_truth_quality"] = quality

    if brand_source == "source_review_stats":
        default_source = _first_text(stats, "source_truth_source", "source") or "source_review_stats"
    else:
        default_source = _first_text(merged, "source_truth_source", "source") or "product_catalog_es"
    merged["source_truth_source"] = default_source

    return merged


def _starts_with_promo_prefix(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith(PROMO_PREFIX_CHARS):
        return True
    stripped = stripped.lstrip("!·ㆍ ")
    stripped = stripped.lstrip("★☆")
    return stripped.startswith(("【", "[", "("))


def _first_text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _clean_text(source.get(key))
        if text is not None:
            return text
    return None


def _nested_es_meta_text(source: dict[str, Any], key: str) -> str | None:
    meta = source.get("_es_meta")
    if not isinstance(meta, dict):
        return None
    return _clean_text(meta.get(key))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "None":
        return None
    return text
