"""Tests: family-level identity in recommendation pipeline."""
from src.rec.candidate_generator import generate_candidates, CandidateProduct
from src.rec.scorer import Scorer
from src.common.enums import RecommendationMode

def _user(owned_family_ids=None, repurchased_family_ids=None):
    return {
        "user_id": "u1",
        "skin_type": None,
        "owned_product_ids": [{"id": "product:P001", "weight": 1.0}],
        "owned_family_ids": [{"id": fid, "weight": 1.0} for fid in (owned_family_ids or [])],
        "repurchased_family_ids": [{"id": fid, "weight": 1.0} for fid in (repurchased_family_ids or [])],
        "preferred_brand_ids": [],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "goal_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_keyword_ids": [],
        "preferred_context_ids": [],
        "recent_purchase_brand_ids": [],
        "repurchase_brand_ids": [],
    }

def _product(pid, family_id=None):
    return {
        "product_id": pid,
        "brand_id": "brand_a",
        "brand_name": "BrandA",
        "category_id": "cat_a",
        "category_name": "CategoryA",
        "variant_family_id": family_id,
        "price": 30000,
        "ingredient_concept_ids": [],
        "category_concept_ids": ["concept:Category:cat_a"],
        "brand_concept_ids": ["concept:Brand:brand_a"],
        "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [],
        "top_keyword_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_comparison_product_ids": [],
        "top_coused_product_ids": [],
        "review_count_30d": 5,
        "review_count_90d": 20,
        "review_count_all": 50,
        "last_signal_at": None,
    }

def test_same_family_detected():
    user = _user(owned_family_ids=["FAM001"])
    products = [_product("P002", "FAM001"), _product("P003", "FAM002")]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    fam_match = [c for c in candidates if c.product_id == "P002"]
    assert len(fam_match) == 1
    assert fam_match[0].owned_family_match is True

def test_no_family_graceful():
    user = _user()
    products = [_product("P002", None)]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    assert candidates[0].owned_family_match is False

def test_family_penalty_in_scorer():
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 0.0, "residual_bee_attr_match": 0.0,
        "context_match": 0.0, "concern_fit": 0.0, "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0, "goal_fit_master": 0.0,
        "goal_fit_review_signal": 0.0, "category_affinity": 0.0,
        "freshness_boost": 0.0, "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0, "novelty_bonus": 1.0,
        "owned_family_penalty": 1.0, "repurchase_family_affinity": 1.0,
    })
    user = _user(owned_family_ids=["FAM001"])
    product_same_fam = _product("P002", "FAM001")
    product_diff_fam = _product("P003", "FAM002")

    s1 = scorer.score(user, product_same_fam)
    s2 = scorer.score(user, product_diff_fam)
    # Same family should score lower (penalty)
    assert s1.raw_score < s2.raw_score

def test_repurchase_family_boost():
    scorer = Scorer()
    scorer.load_from_dict({
        "keyword_match": 0.0, "residual_bee_attr_match": 0.0,
        "context_match": 0.0, "concern_fit": 0.0, "ingredient_match": 0.0,
        "brand_match_conf_weighted": 0.0, "goal_fit_master": 0.0,
        "goal_fit_review_signal": 0.0, "category_affinity": 0.0,
        "freshness_boost": 0.0, "skin_type_fit": 0.0,
        "purchase_loyalty_score": 0.0, "novelty_bonus": 0.0,
        "owned_family_penalty": 0.0, "repurchase_family_affinity": 1.0,
    })
    user = _user(repurchased_family_ids=["FAM001"])
    product_repurchased_fam = _product("P002", "FAM001")
    product_other = _product("P003", "FAM002")

    s1 = scorer.score(user, product_repurchased_fam)
    s2 = scorer.score(user, product_other)
    assert s1.raw_score > s2.raw_score
