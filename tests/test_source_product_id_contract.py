"""Source product id and raw review provenance contract tests."""

import inspect
import json

from src.common.enums import MatchStatus
from src.db.repos import review_repo
from src.ingest.review_ingest import RawReviewRecord, ingest_review
from src.jobs.run_daily_pipeline import process_review
from src.jobs.run_full_load import _merge_source_review_stats
from src.link.product_matcher import ProductIndex
from src.loaders.relation_loader import load_reviews_from_json
from src.loaders.rs_jsonl_loader import load_reviews_from_rs_jsonl
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.qa.quarantine_handler import QuarantineHandler
from src.wrap.projection_registry import ProjectionRegistry


def test_source_product_id_exact_match_beats_incorrect_brand_name_match() -> None:
    product_index = ProductIndex.build([
        {"product_id": "61289", "product_name": "블랙쿠션 듀오", "brand_name": "헤라"},
        {"product_id": "P045", "product_name": "블랙 쿠션", "brand_name": "Fixture"},
    ])
    record = RawReviewRecord(
        brnd_nm="Fixture",
        prod_nm="블랙 쿠션",
        text="좋아요",
        source_product_id="61289",
        source_channel="031",
        source_key_type="ecp_onln_prd_srno",
    )

    bundle = process_review(
        record=record,
        source="fixture",
        product_index=product_index,
        bee_normalizer=BEENormalizer(),
        relation_canonicalizer=RelationCanonicalizer(),
        projection_registry=ProjectionRegistry(),
        quarantine=QuarantineHandler(),
        predicate_contracts={},
        kg_mode="off",
    )

    assert bundle.matched_product_id == "61289"
    assert bundle.review_catalog_link["matched_product_id"] == "61289"
    assert bundle.review_catalog_link["match_status"] == MatchStatus.EXACT.value
    assert bundle.review_catalog_link["match_method"] == "source_product_id"
    assert bundle.review_catalog_link["source_product_id"] == "61289"
    assert bundle.review_catalog_link["source_channel"] == "031"
    assert bundle.review_catalog_link["source_key_type"] == "ecp_onln_prd_srno"


def test_ingest_review_preserves_source_identity_and_rating() -> None:
    record = RawReviewRecord(
        brnd_nm="헤라",
        clct_site_nm="아모레퍼시픽",
        prod_nm="블랙쿠션",
        text="밀착력이 좋아요",
        source_review_key="review-1",
        source_product_id="P0000000000000063981",
        source_channel="031",
        source_key_type="ecp_onln_prd_srno",
        source_rating=4.5,
    )

    ingested = ingest_review(record, source="fixture")

    assert ingested.review_raw["source_product_id"] == "P0000000000000063981"
    assert ingested.review_raw["source_channel"] == "031"
    assert ingested.review_raw["source_key_type"] == "ecp_onln_prd_srno"
    assert ingested.review_raw["source_rating"] == 4.5
    assert ingested.review_raw["raw_payload"]["source_product_id"] == "P0000000000000063981"
    assert ingested.review_raw["raw_payload"]["source_channel"] == "031"
    assert ingested.review_raw["raw_payload"]["source_key_type"] == "ecp_onln_prd_srno"
    assert ingested.review_raw["raw_payload"]["source_rating"] == 4.5


def test_rs_jsonl_maps_product_id_channel_key_type_and_rating(tmp_path) -> None:
    data = [{
        "id": "RV1",
        "text": "좋아요",
        "date": "2026-06-01",
        "product_id": "P0001",
        "prd_nm": "상품명",
        "channel": "036",
        "brnd_nm": "브랜드",
        "prd_apal_scr": "3.75",
        "ner_spans": [],
        "bee_spans": [],
        "relation": [],
    }]
    path = tmp_path / "rs.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    review = load_reviews_from_rs_jsonl(path)[0]

    assert review.source_product_id == "P0001"
    assert review.source_channel == "036"
    assert review.source_key_type == "chn_prd_cd"
    assert review.source_rating == 3.75


def test_relation_loader_preserves_source_fields(tmp_path) -> None:
    data = [{
        "brnd_nm": "헤라",
        "clct_site_nm": "아모레퍼시픽",
        "prod_nm": "블랙쿠션",
        "text": "좋아요",
        "drup_dt": "2026-06-01",
        "source_product_id": "61289",
        "source_channel": "031",
        "source_key_type": "ecp_onln_prd_srno",
        "source_rating": 4,
        "ner": [],
        "bee": [],
        "relation": [],
    }]
    path = tmp_path / "relation.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    review = load_reviews_from_json(path)[0]

    assert review.source_product_id == "61289"
    assert review.source_channel == "031"
    assert review.source_key_type == "ecp_onln_prd_srno"
    assert review.source_rating == 4.0


def test_review_repo_persists_source_identity_columns() -> None:
    raw_src = inspect.getsource(review_repo.upsert_review_raw)
    link_src = inspect.getsource(review_repo.upsert_review_catalog_link)
    history_src = inspect.getsource(review_repo._append_history)

    for col in ("source_product_id", "source_channel", "source_key_type", "source_rating"):
        assert col in raw_src
        assert col in history_src
    for col in ("source_product_id", "source_channel", "source_key_type"):
        assert col in link_src


def test_catalog_review_stats_are_preserved_as_source_fallback() -> None:
    stats = _merge_source_review_stats(
        {
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_channel": "031",
                "source_key_type": "ecp_onln_prd_srno",
                "source_review_count": 42,
                "source_review_score": 4.5,
                "source_truth_source": "product_catalog_es",
            }
        },
        configured_stats=None,
    )

    assert stats["61289"]["source_product_id"] == "61289"
    assert stats["61289"]["source_channel"] == "031"
    assert stats["61289"]["source_key_type"] == "ecp_onln_prd_srno"
    assert "source_review_count_6m" not in stats["61289"]
    assert "source_review_score_count_6m" not in stats["61289"]
    assert "source_avg_rating_6m" not in stats["61289"]
    assert stats["61289"]["source_review_count_all"] == 42
    assert stats["61289"]["source_review_score_count_all"] == 42
    assert stats["61289"]["source_avg_rating_all"] == 4.5
    assert stats["61289"]["source"] == "product_catalog_es"


def test_mock_catalog_review_stats_are_not_promoted_as_source_fallback() -> None:
    stats = _merge_source_review_stats(
        {
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_review_count": 0,
                "source_review_score": None,
                "source_truth_source": "mock_synthesis",
                "source_truth_quality": "MISSING_SOURCE_BRAND",
            }
        },
        configured_stats=None,
    )

    assert stats == {}


def test_configured_mock_review_stats_are_not_promoted() -> None:
    stats = _merge_source_review_stats(
        {
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_truth_source": "mock_synthesis",
            }
        },
        configured_stats={
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_review_count_all": 99,
                "source_avg_rating_all": 4.9,
                "source": "mock_synthesis",
            }
        },
    )

    assert stats == {}


def test_configured_source_review_stats_win_over_mock_master() -> None:
    stats = _merge_source_review_stats(
        {
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_truth_source": "mock_synthesis",
            }
        },
        configured_stats={
            "61289": {
                "product_id": "61289",
                "source_product_id": "61289",
                "source_review_count_6m": 874,
                "source_review_score_count_6m": 874,
                "source_avg_rating_6m": 4.939,
                "source_review_count_all": 99,
                "source_avg_rating_all": 4.9,
                "source": "snowflake:f_prd_rv_hist",
            }
        },
    )

    assert stats["61289"]["source_review_count_6m"] == 874
    assert stats["61289"]["source_avg_rating_6m"] == 4.939
    assert stats["61289"]["source_review_count_all"] == 99
    assert stats["61289"]["source_avg_rating_all"] == 4.9
    assert stats["61289"]["source"] == "snowflake:f_prd_rv_hist"
