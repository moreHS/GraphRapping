"""Tests: cross-source mock data integrity.

Cross-reference checks use shared_entities.json as the source-product/user
anchor. Brand checks are source-grounded only; missing source brands must not be
filled from product-name tokens.
"""
import json
from pathlib import Path

MISSING_SOURCE_BRAND = "MISSING_SOURCE_BRAND"


def _load(name):
    return json.loads(Path(f"mockdata/{name}").read_text(encoding="utf-8"))


def _source_grounded_catalog_brands(catalog):
    return {
        p["BRAND_NAME"]
        for p in catalog
        if p.get("BRAND_NAME")
        and p.get("SOURCE_TRUTH_QUALITY") != MISSING_SOURCE_BRAND
    }


def _catalog_has_only_missing_source_brands(catalog):
    return all(
        p.get("BRAND_NAME") is None
        and p.get("SOURCE_TRUTH_QUALITY") == MISSING_SOURCE_BRAND
        for p in catalog
    )


def _shared_has_no_brand_truth(shared):
    return not shared["brands"] and all(
        p.get("brand_name") in (None, "") for p in shared["products"]
    )


def test_shared_product_ids_in_catalog():
    """All shared_entities product IDs must exist in product_catalog_es."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_ids = {p["ONLINE_PROD_SERIAL_NUMBER"] for p in catalog}
    for p in shared["products"]:
        assert p["product_id"] in catalog_ids, f"{p['product_id']} not in catalog"
        assert p.get("source_product_id", p["product_id"]) == p["product_id"]

def test_shared_brands_in_catalog():
    """Source-grounded catalog brands must appear in shared_entities."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = _source_grounded_catalog_brands(catalog)
    if not catalog_brands:
        assert _catalog_has_only_missing_source_brands(catalog)
        assert _shared_has_no_brand_truth(shared)
        return
    shared_brands = {b["brand_name"] for b in shared["brands"]}
    for brand in catalog_brands:
        assert brand in shared_brands, f"{brand} not in shared_entities"

def test_review_brands_in_catalog():
    """Present review source brand names must exist in source-grounded catalog brands."""
    reviews = _load("review_triples_raw.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = _source_grounded_catalog_brands(catalog)
    for r in reviews:
        brand = r.get("brnd_nm")
        if not brand:
            continue
        assert brand in catalog_brands, f"Review brand '{brand}' not in source-grounded catalog"

def test_shared_users_in_profiles():
    """All shared_entities users must exist in user_profiles_normalized."""
    shared = _load("shared_entities.json")
    users = _load("user_profiles_normalized.json")
    for u in shared["users"]:
        assert u["user_id"] in users, f"Shared user {u['user_id']} not in profiles"

def test_catalog_brands_have_user_overlap():
    """Source-grounded catalog brands should overlap user brand preferences when present."""
    catalog = _load("product_catalog_es.json")
    users = _load("user_profiles_normalized.json")
    catalog_brands = _source_grounded_catalog_brands(catalog)
    if not catalog_brands:
        assert _catalog_has_only_missing_source_brands(catalog)
        return
    user_brands = set()
    for profile in users.values():
        pa = profile.get("purchase_analysis", {})
        for key in ("preferred_skincare_brand", "preferred_makeup_brand"):
            user_brands.update(pa.get(key, []))
    overlap = catalog_brands & user_brands
    assert overlap, f"No catalog brand is preferred by any user. Catalog: {catalog_brands}"
