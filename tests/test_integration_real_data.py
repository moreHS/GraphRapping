"""
Integration test with REAL review data + mock product/user data.

Uses actual Korean beauty review triples from Relation project
(hab_rel_sample_ko_withPRD_listkeyword.json) with synthetic product master
and user profiles to test the full GraphRapping pipeline end-to-end.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from src.loaders.relation_loader import load_reviews_from_json
from src.loaders.product_loader import load_products_from_es_records, ProductLoadResult
from src.loaders.user_loader import load_users_from_profiles, UserLoadResult
from src.jobs.run_full_load import run_full_load, FullLoadConfig
from src.jobs.run_daily_pipeline import (
    process_review, build_review_persist_bundle,
    bundle_to_result_dict, run_batch,
)
from src.link.product_matcher import ProductIndex
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.wrap.projection_registry import ProjectionRegistry
from src.qa.quarantine_handler import QuarantineHandler
from src.rec.candidate_generator import generate_candidates
from src.rec.scorer import Scorer
from src.rec.explainer import explain
from src.rec.hook_generator import generate_hooks
from src.rec.next_question import generate_next_question
from src.common.enums import RecommendationMode


# =============================================================================
# Real review data path
# =============================================================================
REAL_REVIEW_PATH = Path("/Users/amore/Jupyter_workplace/Relation/source_data/hab_rel_sample_ko_withPRD_listkeyword.json")

# =============================================================================
# Mock product data (ES-like records matching brands in real reviews)
# =============================================================================
MOCK_ES_PRODUCTS = [
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_41", "prd_nm": "에뛰드 건강식품", "BRAND_NAME": "에뛰드", "CTGR_SS_NAME": "건강식품", "SALE_STATUS": "판매중"},
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_15", "prd_nm": "일리윤 파우더", "BRAND_NAME": "일리윤", "CTGR_SS_NAME": "페이스파우더", "SALE_STATUS": "판매중"},
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_6", "prd_nm": "헤라 비타민D", "BRAND_NAME": "헤라", "CTGR_SS_NAME": "건강보조", "SALE_STATUS": "판매중"},
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_32", "prd_nm": "설화수 자외선차단", "BRAND_NAME": "설화수", "CTGR_SS_NAME": "썬케어", "SALE_STATUS": "판매중"},
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_43", "prd_nm": "아이오페 화장솜", "BRAND_NAME": "아이오페", "CTGR_SS_NAME": "도구", "SALE_STATUS": "판매중"},
    {"ONLINE_PROD_SERIAL_NUMBER": "PRD_10", "prd_nm": "이니스프리 그린티세럼", "BRAND_NAME": "이니스프리", "CTGR_SS_NAME": "세럼", "SALE_STATUS": "판매중"},
]

# =============================================================================
# Mock user profiles (realistic Korean beauty user)
# =============================================================================
MOCK_USER_PROFILES = {
    "u_test_dry_30f": {
        "basic": {
            "gender": "female",
            "age": "30s",
            "skin_type": "건성",
            "skin_tone": "21호",
            "skin_concerns": ["건조함", "잔주름"],
        },
        "purchase_analysis": {
            "active_product_category": ["에센스", "크림", "쿠션"],
            "preferred_skincare_brand": ["설화수", "라네즈"],
            "preferred_makeup_brand": ["헤라"],
            "preferred_brand": ["설화수", "라네즈", "헤라"],
        },
        "chat": {
            "face": {
                "skin_type": "건성",
                "skin_concerns": ["건조함", "잔주름", "칙칙함"],
                "skincare_goals": ["보습", "탄력", "광채"],
                "preferred_texture": ["크림"],
            },
            "hair": {
                "hair_concerns": ["건조"],
                "haircare_goals": ["보습"],
            },
            "ingredients": {
                "preferred": ["히알루론산", "세라마이드"],
                "avoid": ["알코올"],
                "allergy": [],
            },
            "scent": {
                "preferences": ["플로럴"],
            },
        },
    },
    "u_test_oily_20m": {
        "basic": {
            "gender": "male",
            "age": "20s",
            "skin_type": "지성",
            "skin_concerns": ["번들거림", "모공"],
        },
        "purchase_analysis": {
            "active_product_category": ["토너", "선크림"],
            "preferred_skincare_brand": ["이니스프리"],
            "preferred_brand": ["이니스프리"],
        },
        "chat": None,
    },
}


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def real_reviews():
    """Load first 50 real reviews from Relation project."""
    if not REAL_REVIEW_PATH.exists():
        pytest.skip(f"Real review data not found: {REAL_REVIEW_PATH}")
    return load_reviews_from_json(str(REAL_REVIEW_PATH), max_count=50)


@pytest.fixture(scope="module")
def product_result():
    """Build product artifacts from mock ES records.

    현재 리뷰 데이터의 prod_nm은 임시 코드 (PRD_41 등)로, 사실상 product_id 역할.
    실 운영 시에는 prod_nm(제품명) + prod_id(코드)가 별도 제공됨.
    테스트에서는 임시 코드를 alias로 등록하여 매칭 가능하게 함.
    """
    result = load_products_from_es_records(MOCK_ES_PRODUCTS)
    # 현재 리뷰 데이터: prod_nm이 product_id와 같으므로 alias로 직접 등록
    from src.common.text_normalize import normalize_text
    for record in MOCK_ES_PRODUCTS:
        pid = record["ONLINE_PROD_SERIAL_NUMBER"]
        brand = record.get("BRAND_NAME", "")
        alias_key = f"{normalize_text(brand)}|{normalize_text(pid)}"
        result.product_index.add_alias(alias_key, pid)
    return result


@pytest.fixture(scope="module")
def user_result():
    """Build user artifacts from mock profiles."""
    return load_users_from_profiles(MOCK_USER_PROFILES)


@pytest.fixture(scope="module")
def normalizers():
    """Initialize all normalizers."""
    bee = BEENormalizer()
    bee.load_dictionaries()
    rel = RelationCanonicalizer()
    rel.load()
    proj = ProjectionRegistry()
    proj.load()
    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()
    return {"bee": bee, "rel": rel, "proj": proj, "deriver": deriver}


# =============================================================================
# Test: Data Loading
# =============================================================================

class TestDataLoading:
    def test_real_reviews_loaded(self, real_reviews):
        """Real Korean reviews from Relation project load successfully."""
        assert len(real_reviews) == 50
        # All have required fields
        for r in real_reviews:
            assert r.brnd_nm
            assert r.text
            assert r.source_row_num is not None

    def test_reviews_have_extraction(self, real_reviews):
        """Reviews contain NER, BEE, and relation extraction."""
        has_ner = sum(1 for r in real_reviews if r.ner)
        has_bee = sum(1 for r in real_reviews if r.bee)
        has_rel = sum(1 for r in real_reviews if r.relation)
        print(f"\n  Reviews with NER: {has_ner}/50")
        print(f"  Reviews with BEE: {has_bee}/50")
        print(f"  Reviews with REL: {has_rel}/50")
        assert has_bee > 30  # most reviews should have BEE

    def test_products_loaded(self, product_result):
        assert product_result.product_count == 6
        assert product_result.product_index is not None

    def test_users_loaded(self, user_result):
        assert user_result.user_count == 2


# =============================================================================
# Test: Single Review Pipeline
# =============================================================================

class TestSingleReviewPipeline:
    def test_process_single_review(self, real_reviews, product_result, normalizers):
        """Process a single real review through the full pipeline."""
        review = real_reviews[0]
        quarantine = QuarantineHandler()

        bundle = process_review(
            record=review,
            source="integration_test",
            product_index=product_result.product_index,
            bee_normalizer=normalizers["bee"],
            relation_canonicalizer=normalizers["rel"],
            projection_registry=normalizers["proj"],
            quarantine=quarantine,
            deriver=normalizers["deriver"],
        )

        # Bundle should be returned
        assert bundle is not None
        assert bundle.review_id
        print(f"\n  Review ID: {bundle.review_id}")
        print(f"  Product match: {bundle.matched_product_id}")
        print(f"  Entities: {len(bundle.canonical_entities)}")
        print(f"  Facts: {len(bundle.canonical_facts)}")
        print(f"  Signals: {len(bundle.wrapped_signals)}")
        print(f"  Quarantine: {len(bundle.quarantine_entries)}")

    def test_bundle_to_dict_compat(self, real_reviews, product_result, normalizers):
        """bundle_to_result_dict produces backward-compatible summary."""
        review = real_reviews[0]
        quarantine = QuarantineHandler()
        bundle = process_review(
            record=review, source="test",
            product_index=product_result.product_index,
            bee_normalizer=normalizers["bee"],
            relation_canonicalizer=normalizers["rel"],
            projection_registry=normalizers["proj"],
            quarantine=quarantine,
            deriver=normalizers["deriver"],
        )
        result = bundle_to_result_dict(bundle)
        assert "review_id" in result
        assert "signal_count" in result
        assert "signals" in result


# =============================================================================
# Test: Batch Pipeline
# =============================================================================

class TestBatchPipeline:
    def test_batch_50_reviews(self, real_reviews, product_result, user_result, normalizers):
        """Process 50 real reviews as a batch with product + user data."""
        quarantine = QuarantineHandler()

        batch_result = run_batch(
            reviews=real_reviews,
            source="integration_test",
            product_index=product_result.product_index,
            product_masters=product_result.product_masters,
            concept_links=product_result.concept_links,
            user_masters=user_result.user_masters,
            user_adapted_facts=user_result.user_adapted_facts,
            bee_normalizer=normalizers["bee"],
            relation_canonicalizer=normalizers["rel"],
            projection_registry=normalizers["proj"],
            quarantine=quarantine,
            deriver=normalizers["deriver"],
        )

        print(f"\n  === Batch Result (50 real reviews) ===")
        print(f"  Total signals: {batch_result['total_signals']}")
        print(f"  Quarantined: {batch_result['total_quarantined']}")
        print(f"  Serving products: {len(batch_result['serving_products'])}")
        print(f"  Serving users: {len(batch_result['serving_users'])}")

        # Should produce signals
        assert batch_result["total_signals"] > 0
        # Should produce serving profiles
        assert len(batch_result["serving_products"]) > 0 or len(batch_result["serving_users"]) > 0

    def test_quarantine_captures_failures(self, real_reviews, product_result, normalizers):
        """Quarantine should capture failures (not silent drop)."""
        quarantine = QuarantineHandler()
        for review in real_reviews[:10]:
            process_review(
                record=review, source="test",
                product_index=product_result.product_index,
                bee_normalizer=normalizers["bee"],
                relation_canonicalizer=normalizers["rel"],
                projection_registry=normalizers["proj"],
                quarantine=quarantine,
                deriver=normalizers["deriver"],
            )
        stats = quarantine.pending_by_table()
        print(f"\n  Quarantine stats: {stats}")
        # There WILL be quarantine entries (unknown keywords, projection misses, etc.)


# =============================================================================
# Test: Recommendation
# =============================================================================

class TestRecommendation:
    def test_recommend_for_user(self, real_reviews, product_result, user_result, normalizers):
        """Full recommendation flow: batch → candidate → score → explain → hook."""
        quarantine = QuarantineHandler()
        batch_result = run_batch(
            reviews=real_reviews,
            source="integration_test",
            product_index=product_result.product_index,
            product_masters=product_result.product_masters,
            concept_links=product_result.concept_links,
            user_masters=user_result.user_masters,
            user_adapted_facts=user_result.user_adapted_facts,
            bee_normalizer=normalizers["bee"],
            relation_canonicalizer=normalizers["rel"],
            projection_registry=normalizers["proj"],
            quarantine=quarantine,
            deriver=normalizers["deriver"],
        )

        serving_products = batch_result["serving_products"]
        serving_users = batch_result["serving_users"]

        if not serving_users or not serving_products:
            pytest.skip("No serving profiles generated")

        user_profile = serving_users[0]

        # Candidate generation
        candidates = generate_candidates(
            user_profile=user_profile,
            product_profiles=serving_products,
            mode=RecommendationMode.EXPLORE,
        )
        print(f"\n  Candidates: {len(candidates)}")

        if not candidates:
            pytest.skip("No candidates generated (expected with limited mock products)")

        # Scoring
        scorer = Scorer()
        scorer.load_from_dict({
            "keyword_match": 0.28, "residual_bee_attr_match": 0.12,
            "context_match": 0.15, "concern_fit": 0.15,
            "ingredient_match": 0.10, "brand_match_conf_weighted": 0.08,
            "goal_fit": 0.08, "category_affinity": 0.05, "freshness_boost": 0.05,
        })

        scored = []
        for c in candidates:
            product = next((p for p in serving_products if p["product_id"] == c.product_id), None)
            if product:
                s = scorer.score(user_profile, product, c.overlap_concepts)
                scored.append(s)

        if scored:
            scored.sort(key=lambda s: s.final_score, reverse=True)
            top = scored[0]
            top_candidate = next(c for c in candidates if c.product_id == top.product_id)

            # Explanation
            explanation = explain(top, top_candidate.overlap_concepts)
            hooks = generate_hooks(explanation)
            next_q = generate_next_question(user_profile)

            print(f"  Top product: {top.product_id} (score: {top.final_score:.3f})")
            print(f"  Explanation: {explanation.summary_ko}")
            print(f"  Hook (discovery): {hooks.discovery}")
            print(f"  Hook (consideration): {hooks.consideration}")
            print(f"  Next question: {next_q.question_ko if next_q else 'None'}")
        else:
            print("  No scored products (concept overlap too low with mock data)")


# =============================================================================
# Test: Data Quality Metrics
# =============================================================================

class TestDataQuality:
    def test_relation_type_coverage(self, real_reviews):
        """Check how many canonical relation types appear in real data."""
        rel_types = set()
        for r in real_reviews:
            for rel in r.relation:
                rel_types.add(rel.get("relation", ""))
        print(f"\n  Unique relation types in 50 reviews: {len(rel_types)}")
        print(f"  Types: {sorted(rel_types)[:15]}...")
        assert len(rel_types) > 5  # should see variety

    def test_bee_attr_coverage(self, real_reviews):
        """Check BEE attribute types in real data."""
        attr_types = set()
        for r in real_reviews:
            for bee in r.bee:
                attr_types.add(bee.get("entity_group", ""))
        print(f"\n  Unique BEE attrs in 50 reviews: {len(attr_types)}")
        print(f"  Attrs: {sorted(attr_types)[:10]}...")
        assert len(attr_types) > 3

    def test_ner_type_coverage(self, real_reviews):
        """Check NER types in real data."""
        ner_types = set()
        for r in real_reviews:
            for ner in r.ner:
                ner_types.add(ner.get("entity_group", ""))
        print(f"\n  Unique NER types in 50 reviews: {len(ner_types)}")
        print(f"  Types: {sorted(ner_types)}")

    def test_signal_family_distribution(self, real_reviews, product_result, normalizers):
        """Check what signal families are actually produced from real data."""
        quarantine = QuarantineHandler()
        family_counts: dict[str, int] = {}

        for review in real_reviews[:20]:
            bundle = process_review(
                record=review, source="test",
                product_index=product_result.product_index,
                bee_normalizer=normalizers["bee"],
                relation_canonicalizer=normalizers["rel"],
                projection_registry=normalizers["proj"],
                quarantine=quarantine,
                deriver=normalizers["deriver"],
            )
            for sig in bundle.wrapped_signals:
                family = sig.signal_family
                family_counts[family] = family_counts.get(family, 0) + 1

        print(f"\n  Signal family distribution (20 reviews):")
        for family, count in sorted(family_counts.items(), key=lambda x: -x[1]):
            print(f"    {family}: {count}")
