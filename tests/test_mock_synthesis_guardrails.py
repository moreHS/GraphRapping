import json
from pathlib import Path

from scripts.synthesize_mock_from_v260605 import (
    MISSING_SOURCE_BRAND,
    MOCK_SOURCE_TRUTH_SOURCE,
    build_catalog_record,
    extract_source_brand,
)


MOCKDATA = Path("mockdata")


def _load_mock(name):
    return json.loads((MOCKDATA / name).read_text(encoding="utf-8"))


def test_mock_synthesis_does_not_invent_brand_from_61289_promo_name():
    prd_nm = "【LIVE/2종 증정+6,000P】블랙쿠션 듀오 SPF34/PA++ (모든컬러)"
    meta = {
        "product_id": "61289",
        "prd_nm": prd_nm,
    }

    brand_name = extract_source_brand(meta)
    catalog_row = build_catalog_record("61289", prd_nm, brand_name, "베이스메이크업")

    assert brand_name is None
    assert catalog_row["BRAND_NAME"] is None
    assert catalog_row["SOURCE_TRUTH_SOURCE"] == MOCK_SOURCE_TRUTH_SOURCE
    assert catalog_row["SOURCE_TRUTH_QUALITY"] == MISSING_SOURCE_BRAND


def test_mock_synthesis_preserves_missing_review_stats_as_null():
    catalog_row = build_catalog_record("P1", "테스트 상품", None, "기타")

    assert catalog_row["REVIEW_COUNT"] is None
    assert catalog_row["REVIEW_SCORE"] is None


def test_checked_in_mock_catalog_uses_current_source_grounded_truth():
    catalog = _load_mock("product_catalog_es.json")

    quality_counts = {}
    for row in catalog:
        quality = row.get("SOURCE_TRUTH_QUALITY")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1

    assert quality_counts == {"SOURCE_GROUNDED": 516, "SOURCE_KEY_COLLISION": 1}
    assert sum(1 for row in catalog if row.get("BRAND_NAME")) == 516
    assert not [
        row.get("ONLINE_PROD_SERIAL_NUMBER")
        for row in catalog
        if row.get("SOURCE_TRUTH_QUALITY") == MISSING_SOURCE_BRAND
        and row.get("BRAND_NAME") is not None
    ]


def test_checked_in_mock_catalog_preserves_real_review_stats_only_for_clean_source_truth():
    catalog = _load_mock("product_catalog_es.json")

    bad_rows = [
        {
            "id": row.get("ONLINE_PROD_SERIAL_NUMBER"),
            "quality": row.get("SOURCE_TRUTH_QUALITY"),
            "review_count": row.get("REVIEW_COUNT"),
            "review_score": row.get("REVIEW_SCORE"),
        }
        for row in catalog
        if row.get("SOURCE_TRUTH_QUALITY") == "SOURCE_GROUNDED"
        and (row.get("REVIEW_COUNT") is None or row.get("REVIEW_SCORE") is None)
    ]

    assert not bad_rows, bad_rows[:5]
    assert sum(1 for row in catalog if row.get("REVIEW_COUNT") is not None) == 516


def test_checked_in_mock_reviews_do_not_fabricate_source_brand_truth():
    reviews = _load_mock("review_triples_raw.json")

    bad_reviews = [
        {
            "source_review_key": row.get("source_review_key"),
            "source_product_id": row.get("source_product_id"),
            "brnd_nm": row.get("brnd_nm"),
            "prod_nm": row.get("prod_nm"),
        }
        for row in reviews
        if row.get("brnd_nm") not in (None, "")
    ]

    assert not bad_reviews, bad_reviews[:5]


def test_legacy_catalog_sync_script_removed():
    assert not Path("scripts/sync_product_catalog.py").exists()


def test_synthesis_script_does_not_overwrite_catalog_by_default():
    script = Path("scripts/synthesize_mock_from_v260605.py").read_text(encoding="utf-8")

    assert "--write-derived-catalog" in script
    assert "if args.write_derived_catalog:" in script
    assert "kept product_catalog_es.json unchanged" in script
