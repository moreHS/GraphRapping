"""Tests: cross-source consistency across mock data assets."""
import json
from pathlib import Path

_MOCK = Path("mockdata")


def _load(name):
    return json.loads((_MOCK / name).read_text(encoding="utf-8"))


def test_shared_product_ids_subset_of_catalog():
    """Every product in shared_entities must exist in product_catalog_es."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_ids = {p["ONLINE_PROD_SERIAL_NUMBER"] for p in catalog}
    for p in shared["products"]:
        assert p["product_id"] in catalog_ids, f"Shared product {p['product_id']} not in catalog"


def test_shared_brands_subset_of_catalog():
    """Every brand in shared_entities must appear in at least one catalog product."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    for b in shared["brands"]:
        assert b["brand_name"] in catalog_brands, f"Shared brand {b['brand_name']} not in catalog"


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


def test_catalog_brand_user_triangle():
    """For each user's preferred brand, at least one catalog product should carry that brand."""
    profiles = _load("user_profiles_normalized.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    for uid, profile in profiles.items():
        pa = profile.get("purchase_analysis", {})
        for brand in pa.get("preferred_skincare_brand", []):
            assert brand in catalog_brands, \
                f"User {uid} prefers brand '{brand}' but no catalog product has it"
