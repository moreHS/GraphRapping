from __future__ import annotations

from pathlib import Path

import pytest

from src.jobs.run_full_load import FullLoadConfig
from src.jobs.run_full_load_db import _config_with_default_source_review_stats


def test_full_load_db_loads_source_review_stats_snapshot_for_matching_products(tmp_path: Path) -> None:
    path = tmp_path / "stats.json"
    path.write_text(
        """
        {
          "records": [
            {
              "product_id": "61289",
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
              "source_review_max_date_all": "2026-06-17"
            },
            {
              "product_id": "NOT_IN_LOAD",
              "source_channel": "031",
              "source_key_type": "ecp_onln_prd_srno",
              "source_review_count_6m": 1,
              "source_review_score_count_6m": 1,
              "source_avg_rating_6m": 5.0,
              "source_review_min_date_6m": "2026-06-17",
              "source_review_max_date_6m": "2026-06-17",
              "source_review_count_all": 1,
              "source_review_score_count_all": 1,
              "source_avg_rating_all": 5.0,
              "source_review_min_date_all": "2026-06-17",
              "source_review_max_date_all": "2026-06-17"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    config = FullLoadConfig(
        review_json_path="mockdata/review_triples_raw.json",
        product_es_records=[
            {"ONLINE_PROD_SERIAL_NUMBER": "61289"},
            {"ONLINE_PROD_SERIAL_NUMBER": "OTHER_PRODUCT"},
        ],
        source_review_stats_json_path=str(path),
    )

    loaded = _config_with_default_source_review_stats(config)

    assert loaded.source_review_stats_by_product is not None
    assert set(loaded.source_review_stats_by_product) == {"61289"}
    assert loaded.source_review_stats_by_product["61289"]["source_review_count_6m"] == 862


def test_full_load_db_keeps_explicit_source_review_stats_dict() -> None:
    stats = {
        "P1": {
            "product_id": "P1",
            "source_review_count_6m": 3,
        }
    }
    config = FullLoadConfig(
        review_json_path="mockdata/review_triples_raw.json",
        product_es_records=[],
        source_review_stats_by_product=stats,
        source_review_stats_json_path=None,
    )

    loaded = _config_with_default_source_review_stats(config)

    assert loaded is config
    assert loaded.source_review_stats_by_product is stats


def test_full_load_db_fails_when_configured_source_stats_snapshot_is_missing(tmp_path: Path) -> None:
    config = FullLoadConfig(
        review_json_path="mockdata/review_triples_raw.json",
        product_es_records=[{"ONLINE_PROD_SERIAL_NUMBER": "61289"}],
        source_review_stats_json_path=str(tmp_path / "missing.json"),
    )

    with pytest.raises(FileNotFoundError, match="source review stats snapshot not found"):
        _config_with_default_source_review_stats(config)


def test_full_load_db_fails_when_source_stats_snapshot_has_no_matching_products(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stats.json"
    path.write_text(
        """
        {
          "records": [
            {
              "product_id": "NOT_IN_LOAD",
              "source_channel": "031",
              "source_key_type": "ecp_onln_prd_srno",
              "source_review_count_6m": 1,
              "source_review_score_count_6m": 1,
              "source_avg_rating_6m": 5.0,
              "source_review_min_date_6m": "2026-06-17",
              "source_review_max_date_6m": "2026-06-17",
              "source_review_count_all": 1,
              "source_review_score_count_all": 1,
              "source_avg_rating_all": 5.0,
              "source_review_min_date_all": "2026-06-17",
              "source_review_max_date_all": "2026-06-17"
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    config = FullLoadConfig(
        review_json_path="mockdata/review_triples_raw.json",
        product_es_records=[{"ONLINE_PROD_SERIAL_NUMBER": "61289"}],
        source_review_stats_json_path=str(path),
    )

    with pytest.raises(ValueError, match="loaded zero rows"):
        _config_with_default_source_review_stats(config)
