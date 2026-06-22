"""Derive purchase behavior from personal-agent purchase summary fields.

Only high-confidence exact matches are converted to product/family/brand
behavior. Fuzzy name matches are intentionally excluded from this runtime path.
"""

from __future__ import annotations

from typing import Any

from src.common.text_normalize import normalize_text


_FEATURE_LIST_FIELDS = (
    "owned_product_ids",
    "owned_family_ids",
    "repurchased_family_ids",
    "repurchased_brand_ids",
    "recently_purchased_brand_ids",
)


def derive_purchase_summary_features(
    profile: dict[str, Any],
    product_masters: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Resolve personal-agent purchase summaries into purchase feature dicts.

    Supported summary fields:
    - purchase_analysis.use_expected_product_summary
    - purchase_analysis.preferred_repurchase_product_summary
    - purchase_analysis.seasonal_product_summary
    """
    if not product_masters:
        return None

    purchase = profile.get("purchase_analysis") or {}
    if not isinstance(purchase, dict):
        return None

    index = _build_product_index(product_masters)
    features = _empty_feature_sets()
    matched_any = False

    for kind, item in _iter_summary_items(purchase):
        product_id = _resolve_summary_item(item, index)
        if not product_id:
            continue
        master = product_masters.get(product_id)
        if not master:
            continue
        matched_any = True
        _add_product(master, features, repurchase=(kind == "repurchase"))
        _update_last_seen(features, item.get("recent_purchase_date") or item.get("purchase_date"))

    if not matched_any:
        return None

    return _finalize_features(features)


def merge_purchase_feature_dicts(*items: dict[str, Any] | None) -> dict[str, Any] | None:
    """Union purchase feature dicts produced by events and profile summaries."""
    merged = _empty_feature_sets()
    seen = False
    for item in items:
        if not item:
            continue
        seen = True
        for field in _FEATURE_LIST_FIELDS:
            merged[field].update(str(v) for v in item.get(field, []) if v)
        _update_last_seen(merged, item.get("last_seen_at"))

    return _finalize_features(merged) if seen else None


def _empty_feature_sets() -> dict[str, Any]:
    return {
        "owned_product_ids": set(),
        "owned_family_ids": set(),
        "repurchased_family_ids": set(),
        "repurchased_brand_ids": set(),
        "recently_purchased_brand_ids": set(),
        "last_seen_at": None,
    }


def _finalize_features(features: dict[str, Any]) -> dict[str, Any]:
    return {
        field: sorted(features[field])
        for field in _FEATURE_LIST_FIELDS
    } | {"last_seen_at": features.get("last_seen_at")}


def _build_product_index(product_masters: dict[str, dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for product_id, master in product_masters.items():
        _add_key(index, product_id, product_id)
        _add_key(index, master.get("source_product_id"), product_id)
        _add_key(index, master.get("product_name"), product_id, normalize=True)
        _add_key(index, master.get("representative_product_name"), product_id, normalize=True)
        es_meta = master.get("_es_meta") or {}
        _add_key(index, es_meta.get("ONLINE_PROD_CODE"), product_id)
        _add_key(index, es_meta.get("SAP_CODE"), product_id)
    return index


def _add_key(index: dict[str, str], value: Any, product_id: str, *, normalize: bool = False) -> None:
    if value is None:
        return
    key = str(value).strip()
    if not key:
        return
    if normalize:
        key = normalize_text(key)
    index.setdefault(key, product_id)


def _iter_summary_items(purchase: dict[str, Any]):
    for item in _walk_product_summary(purchase.get("use_expected_product_summary")):
        yield "current", item
    for item in _walk_product_summary(purchase.get("preferred_repurchase_product_summary")):
        yield "repurchase", item
    for item in _walk_product_summary(purchase.get("seasonal_product_summary")):
        yield "seasonal", item


def _walk_product_summary(value: Any):
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_product_summary(child)


def _resolve_summary_item(item: dict[str, Any], index: dict[str, str]) -> str | None:
    for key_name in ("rprs_prd_cd", "prd_cd", "source_product_id", "product_id"):
        value = item.get(key_name)
        if value is not None:
            product_id = index.get(str(value).strip())
            if product_id:
                return product_id

    for key_name in ("rprs_prd_nm", "prd_nm"):
        value = item.get(key_name)
        if value is not None:
            product_id = index.get(normalize_text(str(value)))
            if product_id:
                return product_id
    return None


def _add_product(master: dict[str, Any], features: dict[str, Any], *, repurchase: bool) -> None:
    product_id = master.get("product_id")
    if product_id:
        features["owned_product_ids"].add(str(product_id))

    family_id = master.get("variant_family_id")
    if family_id:
        features["owned_family_ids"].add(str(family_id))
        if repurchase:
            features["repurchased_family_ids"].add(str(family_id))

    brand_id = master.get("brand_id")
    if brand_id:
        features["recently_purchased_brand_ids"].add(str(brand_id))
        if repurchase:
            features["repurchased_brand_ids"].add(str(brand_id))


def _update_last_seen(features: dict[str, Any], value: Any) -> None:
    if not value:
        return
    text = str(value)
    current = features.get("last_seen_at")
    if current is None or text > current:
        features["last_seen_at"] = text

