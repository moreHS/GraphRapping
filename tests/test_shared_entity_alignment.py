"""Tests: cross-source consistency across mock data assets."""
import json
from pathlib import Path

_MOCK = Path("mockdata")
MISSING_SOURCE_BRAND = "MISSING_SOURCE_BRAND"


def _load(name):
    return json.loads((_MOCK / name).read_text(encoding="utf-8"))


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


def test_shared_product_ids_align_with_catalog_source_ids():
    """Every shared product is an id-only anchor for a source catalog product."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_ids = {p["ONLINE_PROD_SERIAL_NUMBER"] for p in catalog}
    shared_ids = {p["product_id"] for p in shared["products"]}
    assert shared_ids == catalog_ids
    for p in shared["products"]:
        assert p.get("source_product_id", p["product_id"]) == p["product_id"]


def test_shared_brands_subset_of_catalog():
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
        assert brand in shared_brands, f"Catalog brand {brand} not in shared_entities"


def test_shared_users_subset_of_profiles():
    """Every user in shared_entities must exist in user_profiles_normalized."""
    shared = _load("shared_entities.json")
    profiles = _load("user_profiles_normalized.json")
    for u in shared["users"]:
        assert u["user_id"] in profiles, f"Shared user {u['user_id']} not in profiles"


def test_kg_output_prd_entities_reference_shared():
    """PRD entities in review_kg_output with scope_key should reference shared products."""
    kg = _load("review_kg_output.json")
    shared = _load("shared_entities.json")
    shared_pids = {p["product_id"] for p in shared["products"]}
    prd_entities = [e for e in kg["entities"] if e["entity_type"] == "PRD" and e.get("scope_key")]
    if not prd_entities:
        return  # no scoped PRD entities — skip
    for e in prd_entities:
        assert e["scope_key"] in shared_pids, \
            f"PRD entity {e['entity_id']} scope_key {e['scope_key']} not in shared products"


def test_kg_output_has_no_official_brand_claims_without_source_brands():
    """Missing catalog source brands must not become KG official-brand claims."""
    kg = _load("review_kg_output.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = _source_grounded_catalog_brands(catalog)
    if catalog_brands:
        return

    assert _catalog_has_only_missing_source_brands(catalog)
    assert not [e for e in kg["entities"] if e["entity_type"] == "BRD"]
    assert not [e for e in kg["edges"] if e["relation_type"] == "OFFICIAL_BRAND"]


def test_catalog_brand_user_triangle():
    """Source-grounded catalog brands should overlap user preferences when present."""
    profiles = _load("user_profiles_normalized.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = _source_grounded_catalog_brands(catalog)
    if not catalog_brands:
        assert _catalog_has_only_missing_source_brands(catalog)
        return
    all_user_brands = set()
    for profile in profiles.values():
        pa = profile.get("purchase_analysis", {})
        for key in ("preferred_skincare_brand", "preferred_makeup_brand"):
            all_user_brands.update(pa.get(key, []))
    overlap = catalog_brands & all_user_brands
    assert overlap, \
        f"No catalog brand preferred by any user. Catalog: {catalog_brands}, User brands sample: {list(all_user_brands)[:10]}"
