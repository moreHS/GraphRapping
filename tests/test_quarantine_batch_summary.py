"""Tests for batch and web-state quarantine summaries."""

import json

from src.ingest.review_ingest import RawReviewRecord
from src.jobs.run_daily_pipeline import run_batch
from src.loaders.product_loader import load_products_from_json
from src.loaders.user_loader import load_users_from_profiles
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.qa.quarantine_handler import QuarantineHandler
from src.web.state import load_demo_data
from src.wrap.projection_registry import ProjectionRegistry


def _pipeline_deps():
    product_result = load_products_from_json("mockdata/product_catalog_es.json")
    users = json.load(open("mockdata/user_profiles_normalized.json", encoding="utf-8"))
    user_result = load_users_from_profiles(users)

    bee_norm = BEENormalizer()
    bee_norm.load_dictionaries()

    rel_canon = RelationCanonicalizer()
    rel_canon.load()

    proj_registry = ProjectionRegistry()
    proj_registry.load()

    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()

    return product_result, user_result, bee_norm, rel_canon, proj_registry, deriver


def test_run_batch_counts_flushed_bundle_quarantine_entries():
    product_result, user_result, bee_norm, rel_canon, proj_registry, deriver = _pipeline_deps()
    reviews = [
        RawReviewRecord(
            brnd_nm="없는브랜드",
            clct_site_nm="test",
            prod_nm="없는상품",
            text="상품 매칭 실패를 의도한 테스트 리뷰",
            source_review_key="quarantine-batch-1",
        )
    ]

    result = run_batch(
        reviews=reviews,
        source="test",
        product_index=product_result.product_index,
        product_masters=product_result.product_masters,
        concept_links=product_result.concept_links,
        user_masters=user_result.user_masters,
        user_adapted_facts=user_result.user_adapted_facts,
        bee_normalizer=bee_norm,
        relation_canonicalizer=rel_canon,
        projection_registry=proj_registry,
        quarantine=QuarantineHandler(),
        deriver=deriver,
    )

    assert result["total_quarantined"] > 0
    assert result["quarantine_by_table"]["quarantine_product_match"] > 0
    assert len(result["quarantine_entries"]) == result["total_quarantined"]


def test_load_demo_data_exposes_quarantine_entries_in_state(tmp_path):
    products = json.load(open("mockdata/product_catalog_es.json", encoding="utf-8"))
    users = json.load(open("mockdata/user_profiles_normalized.json", encoding="utf-8"))
    review_path = tmp_path / "unknown_product_reviews.json"
    review_path.write_text(
        json.dumps([
            {
                "brnd_nm": "없는브랜드",
                "clct_site_nm": "test",
                "prod_nm": "없는상품",
                "text": "웹 상태 격리함 집계를 검증하는 테스트 리뷰",
                "source_review_key": "quarantine-web-1",
                "ner": [],
                "bee": [],
                "relation": [],
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    state = load_demo_data(
        str(review_path),
        products,
        users,
        max_reviews=1,
        source="test_quarantine_state",
        review_format="relation",
    )

    assert state.quarantine_stats.get("quarantine_product_match", 0) > 0
    assert len(state.quarantine_entries) > 0
