"""Tests: SQL-first candidate prefilter path."""
from src.rec.candidate_generator import generate_candidates_prefiltered, CandidateProduct
from src.common.enums import RecommendationMode


def test_prefiltered_uses_only_given_ids():
    """Only products in prefiltered_product_ids should be candidates."""
    user_profile = {
        "preferred_brand_ids": [{"id": "brand_a"}],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "preferred_keyword_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_context_ids": [],
        "goal_ids": [],
        "owned_product_ids": [],
    }
    profiles_by_id = {
        "p1": {"product_id": "p1", "brand_concept_ids": ["brand_a"]},
        "p2": {"product_id": "p2", "brand_concept_ids": ["brand_b"]},
        "p3": {"product_id": "p3", "brand_concept_ids": ["brand_a"]},
    }
    # Only p1 and p3 prefiltered
    results = generate_candidates_prefiltered(
        user_profile, ["p1", "p3"], profiles_by_id,
    )
    pids = {c.product_id for c in results}
    assert "p1" in pids
    assert "p3" in pids
    assert "p2" not in pids


def test_prefiltered_missing_id_skipped():
    """If prefiltered ID is not in profiles_by_id, it should be skipped."""
    user_profile = {
        "preferred_brand_ids": [],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "preferred_keyword_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_context_ids": [],
        "goal_ids": [],
        "owned_product_ids": [],
    }
    profiles_by_id = {"p1": {"product_id": "p1"}}
    results = generate_candidates_prefiltered(
        user_profile, ["p1", "p_missing"], profiles_by_id,
    )
    pids = {c.product_id for c in results}
    assert "p1" in pids
    assert "p_missing" not in pids
