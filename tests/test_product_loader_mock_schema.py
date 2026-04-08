"""Tests: product_loader maps mock schema fields correctly."""
import json
from pathlib import Path
from src.loaders.product_loader import load_products_from_es_records


def _load_mock():
    return json.loads(Path("mockdata/product_catalog_es.json").read_text(encoding="utf-8"))


def _find_record_with(records, **criteria):
    """Find first record matching all criteria (non-empty values)."""
    for r in records:
        if all(r.get(k) and (r.get(k) == v if not callable(v) else v(r.get(k))) for k, v in criteria.items()):
            return r
    return None


def test_price_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    # Find any product with a price
    rec = _find_record_with(records, SALE_PRICE=lambda v: v and v > 0)
    assert rec, "No product with SALE_PRICE in catalog"
    pid = rec["ONLINE_PROD_SERIAL_NUMBER"]
    master = result.product_masters.get(pid)
    assert master is not None
    assert master["price"] == rec["SALE_PRICE"]


def test_main_benefits_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    rec = _find_record_with(records, MAIN_EFFECT=lambda v: v and len(v) > 0)
    assert rec, "No product with MAIN_EFFECT in catalog"
    pid = rec["ONLINE_PROD_SERIAL_NUMBER"]
    master = result.product_masters.get(pid)
    assert master is not None
    assert len(master["main_benefits"]) > 0


def test_ingredients_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    rec = _find_record_with(records, MAIN_INGREDIENT=lambda v: v and "," in str(v))
    assert rec, "No product with multi-ingredient in catalog"
    pid = rec["ONLINE_PROD_SERIAL_NUMBER"]
    master = result.product_masters.get(pid)
    assert master is not None
    assert len(master["ingredients"]) >= 2


def test_variant_family_id_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    rec = _find_record_with(records, REPRESENTATIVE_PROD_CODE=lambda v: v is not None)
    assert rec, "No product with REPRESENTATIVE_PROD_CODE"
    pid = rec["ONLINE_PROD_SERIAL_NUMBER"]
    master = result.product_masters.get(pid)
    assert master is not None
    assert master["variant_family_id"] == rec["REPRESENTATIVE_PROD_CODE"]


def test_sale_status_filter():
    records = _load_mock()
    result = load_products_from_es_records(records)
    active_count = sum(1 for r in records if r.get("SALE_STATUS") == "판매중")
    stopped_pids = [r["ONLINE_PROD_SERIAL_NUMBER"] for r in records if r.get("SALE_STATUS") != "판매중"]
    for pid in stopped_pids:
        assert pid not in result.product_masters, f"Stopped product {pid} should be excluded"
    assert result.product_count == active_count
