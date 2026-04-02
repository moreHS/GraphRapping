"""Tests: cross-source mock data integrity."""
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

def test_user_categories_in_catalog():
    """User preferred categories must exist in product catalog categories."""
    users = _load("user_profiles_normalized.json")
    catalog = _load("product_catalog_es.json")
    catalog_cats = {p["CTGR_SS_NAME"] for p in catalog}
    for uid, profile in users.items():
        for cat in profile.get("purchase_analysis", {}).get("active_product_category", []):
            assert cat in catalog_cats, f"User {uid} category '{cat}' not in catalog"

def test_user_brands_in_catalog():
    """User preferred brands must exist in product catalog."""
    users = _load("user_profiles_normalized.json")
    catalog = _load("product_catalog_es.json")
    catalog_brands = {p["BRAND_NAME"] for p in catalog}
    for uid, profile in users.items():
        for brand in profile.get("purchase_analysis", {}).get("preferred_skincare_brand", []):
            assert brand in catalog_brands, f"User {uid} brand '{brand}' not in catalog"
