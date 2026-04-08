"""Tests: cross-source mock data integrity.

Cross-reference checks use shared_entities.json as the anchor.
User profiles may contain brands/categories beyond the product catalog
(from personal-agent sync), so user→catalog checks are scoped to shared_entities.
"""
import json
from pathlib import Path

def _load(name):
    return json.loads(Path(f"mockdata/{name}").read_text(encoding="utf-8"))

def test_shared_product_ids_in_catalog():
    """All shared_entities product IDs must exist in product_catalog_es."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_ids = {p["ONLINE_PROD_SERIAL_NUMBER"] for p in catalog}
    for p in shared["products"]:
        assert p["product_id"] in catalog_ids, f"{p['product_id']} not in catalog"

def test_shared_brands_in_catalog():
    """All shared brands must appear in catalog."""
    shared = _load("shared_entities.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    for b in shared["brands"]:
        assert b["brand_name"] in catalog_brands, f"{b['brand_name']} not in catalog"

def test_review_brands_in_catalog():
    """All review brand names must exist in product catalog."""
    reviews = _load("review_triples_raw.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    for r in reviews:
        assert r["brnd_nm"] in catalog_brands, f"Review brand '{r['brnd_nm']}' not in catalog"

def test_shared_users_in_profiles():
    """All shared_entities users must exist in user_profiles_normalized."""
    shared = _load("shared_entities.json")
    users = _load("user_profiles_normalized.json")
    for u in shared["users"]:
        assert u["user_id"] in users, f"Shared user {u['user_id']} not in profiles"

def test_catalog_brands_have_user_overlap():
    """At least one user across all profiles should prefer a catalog brand."""
    catalog = _load("product_catalog_es.json")
    users = _load("user_profiles_normalized.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    user_brands = set()
    for profile in users.values():
        pa = profile.get("purchase_analysis", {})
        for key in ("preferred_skincare_brand", "preferred_makeup_brand"):
            user_brands.update(pa.get(key, []))
    overlap = catalog_brands & user_brands
    assert overlap, f"No catalog brand is preferred by any user. Catalog: {catalog_brands}"
