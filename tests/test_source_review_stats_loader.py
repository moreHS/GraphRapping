from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.loaders.source_review_stats_loader import (
    SNOWFLAKE_SOURCE,
    build_031_source_review_stats_sql,
    build_source_review_stats_sql,
    parse_source_review_stats_row,
    product_review_stats_rows,
    sql_literal,
)


def test_sql_literal_escapes_single_quotes() -> None:
    assert sql_literal("12'34") == "'12''34'"


def test_031_sql_uses_ecp_key_and_snowflake_case_aggregates() -> None:
    sql = build_031_source_review_stats_sql(["61289", "12'34"])

    assert "TO_VARCHAR(dcpm.ecp_onln_prd_srno) AS product_id" in sql
    assert "'ecp_onln_prd_srno' AS source_key_type" in sql
    assert "WHERE fprh.chn_cd = '031'" in sql
    assert "TO_VARCHAR(dcpm.ecp_onln_prd_srno) IN ('61289', '12''34')" in sql
    assert "COUNT(*) AS review_count_all" in sql
    assert "COUNT(fprh.prd_apal_scr) AS score_count_all" in sql
    assert "AVG(fprh.prd_apal_scr) AS avg_rating_all" in sql
    assert "COUNT(CASE" in sql
    assert "AVG(CASE" in sql
    assert "MIN(CASE" in sql
    assert "MAX(CASE" in sql
    assert "FILTER (" not in sql
    assert "MAX(dpam.brnd_nm) AS brand_name" in sql
    assert "MAX(dpam.brnd_cd) AS brand_id" in sql
    assert "MAX(t4.ecp_onln_prd_nm) AS product_name" in sql
    assert "MAX(dpam.rprs_prd_nm) AS representative_product_name" in sql


@pytest.mark.parametrize("channel", ["036", "039", "048"])
def test_non_031_sql_uses_channel_product_code(channel: str) -> None:
    sql = build_source_review_stats_sql(["P-001"], source_channel=channel)

    assert "TO_VARCHAR(fprh.chn_prd_cd) AS product_id" in sql
    assert "'chn_prd_cd' AS source_key_type" in sql
    assert f"WHERE fprh.chn_cd = '{channel}'" in sql
    assert "TO_VARCHAR(fprh.chn_prd_cd) IN ('P-001')" in sql
    assert "FILTER (" not in sql


def test_source_review_stats_sql_rejects_empty_product_ids() -> None:
    with pytest.raises(ValueError, match="product_ids"):
        build_031_source_review_stats_sql([])


def test_source_review_stats_sql_rejects_unsupported_channel() -> None:
    with pytest.raises(ValueError, match="Unsupported source_channel"):
        build_source_review_stats_sql(["61289"], source_channel="999")


def test_parse_source_review_stats_row_keeps_null_average_when_score_count_is_zero() -> None:
    row = {
        "PRODUCT_ID": "61289",
        "SOURCE_CHANNEL": "031",
        "SOURCE_KEY_TYPE": "ecp_onln_prd_srno",
        "PRODUCT_NAME": "Black Cushion",
        "REPRESENTATIVE_PRODUCT_NAME": "Black Cushion Duo",
        "BRAND_ID": "11107",
        "BRAND_NAME": "HERA",
        "REVIEW_COUNT_6M": 2,
        "SCORE_COUNT_6M": 0,
        "AVG_RATING_6M": Decimal("0.000"),
        "REVIEW_MIN_DATE_6M": "20260102",
        "REVIEW_MAX_DATE_6M": datetime(2026, 6, 1, 1, 2, 3),
        "REVIEW_COUNT_ALL": 10,
        "SCORE_COUNT_ALL": 0,
        "AVG_RATING_ALL": Decimal("4.500"),
        "REVIEW_MIN_DATE_ALL": "2025-12-01",
        "REVIEW_MAX_DATE_ALL": date(2026, 6, 1),
    }

    stats = parse_source_review_stats_row(row)

    assert stats.product_id == "61289"
    assert stats.source_channel == "031"
    assert stats.source_key_type == "ecp_onln_prd_srno"
    assert stats.brand_name == "HERA"
    assert stats.review_count_6m == 2
    assert stats.score_count_6m == 0
    assert stats.avg_rating_6m is None
    assert stats.review_min_date_6m == date(2026, 1, 2)
    assert stats.review_max_date_6m == date(2026, 6, 1)
    assert stats.review_count_all == 10
    assert stats.score_count_all == 0
    assert stats.avg_rating_all is None
    assert stats.review_min_date_all == date(2025, 12, 1)
    assert stats.review_max_date_all == date(2026, 6, 1)
    assert stats.source == SNOWFLAKE_SOURCE


def test_product_review_stats_rows_maps_loader_fields_to_persistence_contract() -> None:
    rows = product_review_stats_rows([
        {
            "product_id": "P1",
            "source_channel": "036",
            "review_count_6m": 3,
            "score_count_6m": 2,
            "avg_rating_6m": Decimal("4.250"),
            "review_count_all": 5,
            "score_count_all": 4,
            "avg_rating_all": Decimal("4.000"),
        }
    ])

    assert rows == [{
        "product_id": "P1",
        "source_channel": "036",
        "source_key_type": "chn_prd_cd",
        "source_review_count_6m": 3,
        "source_review_score_count_6m": 2,
        "source_avg_rating_6m": 4.25,
        "source_review_min_date_6m": None,
        "source_review_max_date_6m": None,
        "source_review_count_all": 5,
        "source_review_score_count_all": 4,
        "source_avg_rating_all": 4.0,
        "source_review_min_date_all": None,
        "source_review_max_date_all": None,
        "source": SNOWFLAKE_SOURCE,
    }]
