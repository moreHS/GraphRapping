"""Tests: product_loader maps mock schema fields correctly."""
import json
from pathlib import Path
from src.loaders.product_loader import load_products_from_es_records


def _load_mock():
    data = json.loads(Path("mockdata/product_catalog_es.json").read_text(encoding="utf-8"))
    return data


def test_price_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    # P001 has SALE_PRICE=39000
    master = result.product_masters.get("P001")
    assert master is not None
    assert master["price"] == 39000


def test_main_benefits_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    master = result.product_masters.get("P001")
    assert master is not None
    assert "보습" in master["main_benefits"]


def test_ingredients_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    master = result.product_masters.get("P001")
    assert master is not None
    assert "히알루론산" in master["ingredients"]
    assert "세라마이드" in master["ingredients"]


def test_variant_family_id_mapped():
    records = _load_mock()
    result = load_products_from_es_records(records)
    master = result.product_masters.get("P001")
    assert master is not None
    assert master["variant_family_id"] is not None
    assert master["variant_family_id"] == "10001001"  # REPRESENTATIVE_PROD_CODE


def test_sale_status_filter():
    records = _load_mock()
    result = load_products_from_es_records(records)
    # P011 and P012 are 판매중지 — should be excluded
    assert "P011" not in result.product_masters
    assert "P012" not in result.product_masters
    assert result.product_count == 10
