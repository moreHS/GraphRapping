from src.loaders.product_truth_merge import is_placeholder_brand, merge_product_truth


def test_source_brand_overrides_promo_prefix_brand_for_61289():
    catalog_master = {
        "product_id": "61289",
        "product_name": "【LIVE/2종 증정+6,000P】블랙쿠션 듀오 SPF34/PA++ (모든컬러)",
        "brand_name": "【LIVE/2종",
        "brand_id": "live2",
    }
    stats = {
        "product_id": "61289",
        "brand_id": "11107",
        "brand_name": "헤라",
        "representative_product_name": "블랙쿠션 듀오 SPF34/PA++",
        "source": "snowflake:f_prd_rv_hist",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=stats)

    assert merged["product_id"] == "61289"
    assert merged["source_product_id"] == "61289"
    assert merged["brand_name"] == "헤라"
    assert merged["brand_id"] == "11107"
    assert merged["representative_product_name"] == "블랙쿠션 듀오 SPF34/PA++"
    assert merged["source_truth_source"] == "snowflake:f_prd_rv_hist"
    assert merged["source_truth_quality"] == "SOURCE_GROUNDED"


def test_missing_brand_stays_unknown_not_first_product_token():
    catalog_master = {
        "product_id": "P1",
        "product_name": "[기획] 그린티 히알루론산 세럼",
        "brand_name": None,
    }

    merged = merge_product_truth(catalog_master, source_review_stats=None)

    assert merged["brand_name"] is None
    assert merged["brand_id"] is None
    assert merged["source_product_id"] == "P1"
    assert merged["source_truth_quality"] == "MISSING_SOURCE_BRAND"


def test_valid_catalog_brand_is_kept_before_stats_fallback():
    catalog_master = {
        "product_id": "P2",
        "product_name": "라네즈 워터뱅크 세럼",
        "brand_name": "라네즈",
        "brand_id": "laneige",
    }
    stats = {
        "product_id": "P2",
        "brand_name": "다른브랜드",
        "brand_id": "other",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=stats)

    assert merged["brand_name"] == "라네즈"
    assert merged["brand_id"] == "laneige"
    assert merged["source_truth_quality"] == "SOURCE_GROUNDED"


def test_explicit_synthetic_quality_is_not_promoted_to_source_grounded():
    catalog_master = {
        "product_id": "P3",
        "product_name": "라네즈 대표 에센스",
        "brand_name": "라네즈",
        "brand_id": "laneige",
        "source_truth_source": "personal_agent_brand_enum",
        "source_truth_quality": "SYNTHETIC_CATALOG_TEMPLATE",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=None)

    assert merged["brand_name"] == "라네즈"
    assert merged["source_truth_source"] == "personal_agent_brand_enum"
    assert merged["source_truth_quality"] == "SYNTHETIC_CATALOG_TEMPLATE"


def test_explicit_missing_brand_quality_wins_over_catalog_token():
    catalog_master = {
        "product_id": "P4",
        "product_name": "그린티 히알루론산 세럼",
        "brand_name": "그린티",
        "source_truth_quality": "MISSING_SOURCE_BRAND",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=None)

    assert merged["brand_name"] is None
    assert merged["brand_id"] is None
    assert merged["source_truth_quality"] == "MISSING_SOURCE_BRAND"


def test_explicit_source_key_collision_quality_is_preserved_without_brand():
    catalog_master = {
        "product_id": "35119",
        "product_name": (
            "SOURCE_KEY_COLLISION: 031 세라마이드 아토 버블워시 앤 샴푸"
            " | 036 스페셜 케어 마스크 [풋]"
        ),
        "brand_name": None,
        "source_truth_quality": "SOURCE_KEY_COLLISION",
        "source_truth_source": "source_identity_merge:2026-06-16",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=None)

    assert merged["brand_name"] is None
    assert merged["brand_id"] is None
    assert merged["source_truth_source"] == "source_identity_merge:2026-06-16"
    assert merged["source_truth_quality"] == "SOURCE_KEY_COLLISION"


def test_source_stats_brand_overrides_explicit_missing_catalog_brand():
    catalog_master = {
        "product_id": "P5",
        "product_name": "그린티 히알루론산 세럼",
        "brand_name": "그린티",
        "representative_product_name": "Synthetic Green Tea Serum",
        "source_truth_quality": "MISSING_SOURCE_BRAND",
        "source_truth_source": "mock_synthesis",
    }
    stats = {
        "product_id": "P5",
        "brand_id": "innisfree",
        "brand_name": "이니스프리",
        "representative_product_name": "Source Green Tea Hyaluronic Serum",
        "source": "snowflake:f_prd_rv_hist",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=stats)

    assert merged["brand_name"] == "이니스프리"
    assert merged["brand_id"] == "innisfree"
    assert merged["representative_product_name"] == "Source Green Tea Hyaluronic Serum"
    assert merged["source_truth_source"] == "snowflake:f_prd_rv_hist"
    assert merged["source_truth_quality"] == "SOURCE_GROUNDED"


def test_source_stats_brand_overrides_synthetic_catalog_brand():
    catalog_master = {
        "product_id": "P6",
        "product_name": "라네즈 대표 에센스",
        "brand_name": "라네즈",
        "brand_id": "laneige",
        "representative_product_name": "Synthetic Template Essence",
        "source_truth_source": "personal_agent_brand_enum",
        "source_truth_quality": "SYNTHETIC_CATALOG_TEMPLATE",
    }
    stats = {
        "product_id": "P6",
        "brand_id": "src-laneige",
        "brand_name": "라네즈소스",
        "representative_product_name": "Source Laneige Essence",
        "source": "snowflake:f_prd_rv_hist",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=stats)

    assert merged["brand_name"] == "라네즈소스"
    assert merged["brand_id"] == "src-laneige"
    assert merged["representative_product_name"] == "Source Laneige Essence"
    assert merged["source_truth_source"] == "snowflake:f_prd_rv_hist"
    assert merged["source_truth_quality"] == "SOURCE_GROUNDED"


def test_placeholder_brand_detection_rejects_promo_prefixes():
    assert is_placeholder_brand("【LIVE/2종")
    assert is_placeholder_brand("★【LIVE")
    assert is_placeholder_brand("[기획]")
    assert is_placeholder_brand("Unknown")
    assert not is_placeholder_brand("헤라")
