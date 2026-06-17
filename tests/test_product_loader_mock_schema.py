"""Tests: product_loader maps ES schema fields correctly.

2026-06-16 fix: catalog 가 오늘자 source-grounded product master 로 교체되면서
브랜드/가격/리뷰통계가 실제 값으로 채워진다. 매핑 로직 자체 검증은
mockdata 가 아닌 in-memory dummy record 로 수행하여 catalog 풍부도와
무관하게 보장.

`test_sale_status_filter` 와 `test_variant_family_id_mapped` 는 mockdata 가
들고 있는 필드라 그대로 유지.
"""
import json
from pathlib import Path
from src.loaders.product_loader import load_products_from_es_records


_DUMMY_PRODUCT = {
    "ONLINE_PROD_SERIAL_NUMBER": "TEST_001",
    "REPRESENTATIVE_PROD_CODE": "10000000",
    "prd_nm": "테스트 세럼",
    "REPRESENTATIVE_PROD_NAME": "테스트 세럼",
    "BRAND_NAME": "테스트브랜드",
    "CTGR_SS_NAME": "세럼",
    "MAIN_EFFECT": "보습, 진정",
    "MAIN_INGREDIENT": "히알루론산, 글리세린, 나이아신아마이드",
    "SALE_PRICE": 12345,
    "SALE_STATUS": "판매중",
    "COUNTRY_OF_ORIGIN": "한국",
    "REVIEW_COUNT": 12,
    "REVIEW_SCORE": 4.5,
}


def _load_mock():
    return json.loads(Path("mockdata/product_catalog_es.json").read_text(encoding="utf-8"))


def test_price_mapped():
    result = load_products_from_es_records([_DUMMY_PRODUCT])
    master = result.product_masters["TEST_001"]
    assert master["price"] == 12345


def test_main_benefits_mapped():
    result = load_products_from_es_records([_DUMMY_PRODUCT])
    master = result.product_masters["TEST_001"]
    assert master["main_benefits"] == ["보습", "진정"]


def test_ingredients_mapped():
    result = load_products_from_es_records([_DUMMY_PRODUCT])
    master = result.product_masters["TEST_001"]
    assert master["ingredients"] == ["히알루론산", "글리세린", "나이아신아마이드"]


def test_variant_family_id_mapped():
    # mockdata 의 실 record 로도 검증 (REPRESENTATIVE_PROD_CODE 는 모든 product 가 보유).
    records = _load_mock()
    result = load_products_from_es_records(records)
    rec = next(
        r
        for r in records
        if r.get("SALE_STATUS") == "판매중" and r.get("REPRESENTATIVE_PROD_CODE")
    )
    pid = rec["ONLINE_PROD_SERIAL_NUMBER"]
    master = result.product_masters.get(pid)
    assert master is not None
    assert master["variant_family_id"] == rec["REPRESENTATIVE_PROD_CODE"]


def test_source_truth_fields_mapped():
    result = load_products_from_es_records([_DUMMY_PRODUCT])
    master = result.product_masters["TEST_001"]
    assert master["source_product_id"] == "TEST_001"
    assert master["source_key_type"] == "ecp_onln_prd_srno"
    assert master["representative_product_name"] == "테스트 세럼"
    assert master["source_truth_source"] == "product_catalog_es"
    assert master["source_truth_quality"] == "SOURCE_GROUNDED"


def test_source_review_stats_preserved_on_master():
    result = load_products_from_es_records([_DUMMY_PRODUCT])
    master = result.product_masters["TEST_001"]
    assert master["source_review_count"] == 12
    assert master["source_review_score"] == 4.5
    assert master["_es_meta"]["REVIEW_COUNT"] == 12
    assert master["_es_meta"]["REVIEW_SCORE"] == 4.5


def test_missing_source_review_score_is_none_not_zero():
    record = dict(_DUMMY_PRODUCT)
    record["ONLINE_PROD_SERIAL_NUMBER"] = "TEST_ZERO_SCORE"
    record["REVIEW_COUNT"] = 0
    record["REVIEW_SCORE"] = 0.0

    result = load_products_from_es_records([record])
    master = result.product_masters["TEST_ZERO_SCORE"]
    assert master["source_review_count"] == 0
    assert master["source_review_score"] is None


def test_promo_prefix_brand_is_not_inferred_from_product_name():
    record = dict(_DUMMY_PRODUCT)
    record["ONLINE_PROD_SERIAL_NUMBER"] = "PROMO_001"
    record["prd_nm"] = "【LIVE/2종 증정+6,000P】블랙쿠션 듀오 SPF34/PA++"
    record["BRAND_NAME"] = None
    record["SOURCE_TRUTH_QUALITY"] = "MISSING_SOURCE_BRAND"

    result = load_products_from_es_records([record])
    master = result.product_masters["PROMO_001"]

    assert master["brand_name"] is None
    assert master["brand_id"] is None
    assert master["source_truth_quality"] == "MISSING_SOURCE_BRAND"
    assert "PROMO_001" not in result.product_index.brands


def test_checked_in_61289_uses_source_grounded_brand_truth():
    records = _load_mock()
    result = load_products_from_es_records(records, sale_status_filter="")
    master = result.product_masters["61289"]
    assert master["brand_name"] == "헤라"
    assert master["source_truth_quality"] == "SOURCE_GROUNDED"
    assert master["source_review_count"] == 4919
    assert master["source_review_score"] == 4.945314
    assert "61289" in result.product_index.brands


def test_loader_preserves_explicit_synthetic_source_truth_quality():
    record = dict(_DUMMY_PRODUCT)
    record["SOURCE_TRUTH_SOURCE"] = "personal_agent_brand_enum"
    record["SOURCE_TRUTH_QUALITY"] = "SYNTHETIC_CATALOG_TEMPLATE"
    record["REVIEW_COUNT"] = None
    record["REVIEW_SCORE"] = None

    result = load_products_from_es_records([record])
    master = result.product_masters["TEST_001"]

    assert master["brand_name"] == "테스트브랜드"
    assert master["source_truth_source"] == "personal_agent_brand_enum"
    assert master["source_truth_quality"] == "SYNTHETIC_CATALOG_TEMPLATE"
    assert master["source_review_count"] is None
    assert master["source_review_score"] is None


def test_sale_status_filter():
    records = _load_mock()
    result = load_products_from_es_records(records, sale_status_filter="판매중")
    active_count = sum(1 for r in records if r.get("SALE_STATUS") == "판매중")
    stopped_pids = [r["ONLINE_PROD_SERIAL_NUMBER"] for r in records if r.get("SALE_STATUS") != "판매중"]
    for pid in stopped_pids:
        assert pid not in result.product_masters, f"Stopped product {pid} should be excluded"
    assert result.product_count == active_count


def test_default_loads_all_source_products():
    records = _load_mock()
    result = load_products_from_es_records(records)
    assert result.product_count == len(records)
