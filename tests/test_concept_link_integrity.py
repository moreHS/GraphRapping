"""Tests for concept IRI join integrity: user-product shared concept matching."""

import pytest
from src.ingest.product_ingest import ProductRecord, ingest_product
from src.user.adapters.personal_agent_adapter import adapt_user_profile
from src.rec.candidate_generator import generate_candidates, _extract_ids
from src.common.enums import RecommendationMode


class TestConceptIRIJoin:
    def test_user_product_brand_join(self):
        """User and product brand concept must use same key basis for join."""
        # When brand_id is None, product_ingest falls back to brand_name as concept key
        product = ProductRecord(
            product_id="P1", product_name="Test", brand_name="라네즈",
            category_name="쿠션", main_benefits=["보습"], ingredients=["세라마이드"],
        )
        result = ingest_product(product)
        brand_concepts = [l.concept_id for l in result["links"] if l.link_type == "HAS_BRAND"]

        # User side uses brand_name directly
        user_profile = {
            "basic": {"skin_type": "건성"},
            "purchase_analysis": {"preferred_skincare_brand": ["라네즈"]},
            "chat": None,
        }
        user_facts = adapt_user_profile("u1", user_profile)
        user_brand_ids = {f["concept_id"] for f in user_facts if f["predicate"] == "PREFERS_BRAND"}

        # Join: both should produce same concept IRI when key basis matches
        assert user_brand_ids & set(brand_concepts), \
            f"User brands {user_brand_ids} should intersect with product brands {brand_concepts}"

    def test_user_product_goal_join(self):
        """User WANTS_GOAL concept must match product main_benefit_concept_ids."""
        product = ProductRecord(
            product_id="P2", product_name="Test", main_benefits=["진정", "보습"],
        )
        result = ingest_product(product)
        benefit_concepts = [l.concept_id for l in result["links"] if l.link_type == "HAS_MAIN_BENEFIT"]

        user_profile = {
            "basic": {},
            "purchase_analysis": {},
            "chat": {"face": {"skincare_goals": ["진정"]}},
        }
        user_facts = adapt_user_profile("u2", user_profile)
        user_goal_ids = {f["concept_id"] for f in user_facts if f["predicate"] == "WANTS_GOAL"}

        assert user_goal_ids & set(benefit_concepts), \
            f"User goals {user_goal_ids} should intersect with product benefits {benefit_concepts}"

    def test_candidate_generator_uses_concept_ids(self):
        """Candidate generator should match via concept IRI, not raw ID."""
        user_profile = {
            "user_id": "u1",
            "preferred_brand_ids": [{"id": "concept:Brand:라네즈", "weight": 1.0}],
            "preferred_category_ids": [],
            "preferred_ingredient_ids": [],
            "avoided_ingredient_ids": [],
            "concern_ids": [],
            "goal_ids": [],
            "preferred_bee_attr_ids": [],
            "preferred_keyword_ids": [],
            "preferred_context_ids": [],
        }
        product_profile = {
            "product_id": "P1",
            "brand_concept_ids": ["concept:Brand:라네즈"],
            "category_concept_ids": [],
            "ingredient_concept_ids": [],
            "main_benefit_concept_ids": [],
            "top_bee_attr_ids": [], "top_keyword_ids": [], "top_context_ids": [],
            "top_concern_pos_ids": [], "top_concern_neg_ids": [],
            "top_tool_ids": [], "top_comparison_product_ids": [], "top_coused_product_ids": [],
            "review_count_all": 50,
        }
        candidates = generate_candidates(user_profile, [product_profile])
        assert len(candidates) == 1
        assert any("brand:" in c for c in candidates[0].overlap_concepts)

    def test_reviewer_proxy_not_in_user_concepts(self):
        """Reviewer proxy IRI must never appear in user preference concept IDs."""
        user_facts = adapt_user_profile("u1", {
            "basic": {"skin_type": "건성"},
            "purchase_analysis": {},
            "chat": None,
        })
        for f in user_facts:
            assert not f["concept_id"].startswith("reviewer_proxy:"), \
                f"Reviewer proxy IRI leaked into user facts: {f}"
