from __future__ import annotations

import json

import pytest

from src.web.state import load_demo_data


def _product(product_id: str = "61289") -> dict:
    return {
        "ONLINE_PROD_SERIAL_NUMBER": product_id,
        "prd_nm": "블랙 쿠션",
        "BRAND_NAME": "헤라",
        "SOURCE_CHANNEL": "031",
        "SOURCE_KEY_TYPE": "ecp_onln_prd_srno",
        "SOURCE_TRUTH_QUALITY": "SOURCE_GROUNDED",
        "REPRESENTATIVE_PROD_NAME": "헤라 블랙 쿠션",
    }


def _write_empty_reviews(tmp_path) -> str:
    review_path = tmp_path / "reviews.json"
    review_path.write_text("[]", encoding="utf-8")
    return str(review_path)


def _write_source_stats_snapshot(tmp_path) -> str:
    stats_path = tmp_path / "source_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "product_id": "61289",
                        "source_product_id": "61289",
                        "source_channel": "031",
                        "source_key_type": "ecp_onln_prd_srno",
                        "source_review_count_6m": 862,
                        "source_review_score_count_6m": 862,
                        "source_avg_rating_6m": 4.941,
                        "source_review_min_date_6m": "2025-12-18",
                        "source_review_max_date_6m": "2026-06-17",
                        "source_review_count_all": 4965,
                        "source_review_score_count_all": 4965,
                        "source_avg_rating_all": 4.945,
                        "source_review_min_date_all": "2024-09-26",
                        "source_review_max_date_all": "2026-06-17",
                        "source": "snowflake:f_prd_rv_hist:test",
                    },
                    {
                        "product_id": "NOT_LOADED",
                        "source_product_id": "NOT_LOADED",
                        "source_channel": "031",
                        "source_key_type": "ecp_onln_prd_srno",
                        "source_review_count_6m": 99,
                        "source_review_score_count_6m": 99,
                        "source_avg_rating_6m": 4.8,
                        "source_review_min_date_6m": "2025-12-18",
                        "source_review_max_date_6m": "2026-06-17",
                        "source_review_count_all": 999,
                        "source_review_score_count_all": 999,
                        "source_avg_rating_all": 4.8,
                        "source_review_min_date_all": "2024-09-26",
                        "source_review_max_date_all": "2026-06-17",
                        "source": "snowflake:f_prd_rv_hist:test",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(stats_path)


def test_load_demo_data_applies_source_review_stats_snapshot(tmp_path):
    state = load_demo_data(
        review_json_path=_write_empty_reviews(tmp_path),
        product_es_records=[_product()],
        user_profiles={},
        max_reviews=0,
        source="test",
        review_format="relation",
        kg_mode="off",
        source_review_stats_json_path=_write_source_stats_snapshot(tmp_path),
    )

    assert state.serving_products[0]["source_review_count_6m"] == 862
    assert state.serving_products[0]["source_avg_rating_6m"] == 4.941
    assert state.serving_products[0]["source_review_count_all"] == 4965
    assert state.batch_result["source_review_stats_by_product"]["61289"][
        "source_review_count_6m"
    ] == 862
    assert "NOT_LOADED" not in state.batch_result["source_review_stats_by_product"]


def test_load_demo_data_filters_explicit_source_review_stats_by_loaded_products(tmp_path):
    state = load_demo_data(
        review_json_path=_write_empty_reviews(tmp_path),
        product_es_records=[_product()],
        user_profiles={},
        max_reviews=0,
        source="test",
        review_format="relation",
        kg_mode="off",
        source_review_stats_by_product={
            "61289": {"source_review_count_6m": 7, "source_avg_rating_6m": 4.5},
            "NOT_LOADED": {"source_review_count_6m": 99, "source_avg_rating_6m": 4.9},
        },
    )

    assert state.serving_products[0]["source_review_count_6m"] == 7
    assert state.batch_result["source_review_stats_by_product"] == {
        "61289": {"source_review_count_6m": 7, "source_avg_rating_6m": 4.5}
    }


def test_load_demo_data_missing_default_source_stats_snapshot_fails_closed(
    monkeypatch,
    tmp_path,
):
    missing_default = tmp_path / "missing_default.json"
    monkeypatch.setattr("src.web.state._DEFAULT_SOURCE_REVIEW_STATS_PATH", missing_default)

    state = load_demo_data(
        review_json_path=_write_empty_reviews(tmp_path),
        product_es_records=[_product()],
        user_profiles={},
        max_reviews=0,
        source="test",
        review_format="relation",
        kg_mode="off",
        source_review_stats_json_path=str(missing_default),
    )

    assert state.batch_result["source_review_stats_by_product"] == {}
    assert state.serving_products[0]["source_review_count_6m"] is None


def test_load_demo_data_missing_explicit_source_stats_snapshot_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="source review stats snapshot not found"):
        load_demo_data(
            review_json_path=_write_empty_reviews(tmp_path),
            product_es_records=[_product()],
            user_profiles={},
            max_reviews=0,
            source="test",
            review_format="relation",
            kg_mode="off",
            source_review_stats_json_path=str(tmp_path / "missing_explicit.json"),
        )
