"""Tests: family ID normalization across adapter→serving→comparison pipeline.

Verifies that family IDs produced by the adapter (with product: IRI prefix)
correctly match against raw variant_family_id values in product profiles.
"""
from src.user.adapters.personal_agent_adapter import adapt_user_profile
from src.rec.candidate_generator import generate_candidates
from src.rec.scorer import Scorer
from src.common.enums import RecommendationMode


def _build_serving_user(user_id, owned_family_ids=None, repurchased_family_ids=None):
    """Build a serving-like user profile via the adapter pipeline.

    This exercises the real adapter code path that produces product: prefixed IRIs.
    """
    profile = {
        "basic": {"gender": "female", "age": "30", "skin_type": None, "skin_concerns": []},
        "purchase_analysis": {
            "preferred_skincare_brand": [],
            "repurchase_brand": [],
            "recently_purchased_brand": [],
        },
        "chat": None,
    }
    purchase_features = {
        "owned_product_ids": ["P001"],
        "owned_family_ids": owned_family_ids or [],
        "repurchased_family_ids": repurchased_family_ids or [],
        "repurchased_brand_ids": [],
        "recently_purchased_brand_ids": [],
    }
    facts = adapt_user_profile(user_id, profile, purchase_features=purchase_features)

    # Convert adapted facts to a serving-user-profile-like dict
    # Extract family IDs from the adapter output (they will have product: prefix)
    owned_fam = []
    repurchased_fam = []
    owned_prods = []
    for f in facts:
        pred = f.get("predicate", "")
        cid = f.get("concept_id", "")
        if pred == "OWNS_FAMILY":
            owned_fam.append({"id": cid, "weight": f.get("confidence", 1.0)})
        elif pred == "REPURCHASES_FAMILY":
            repurchased_fam.append({"id": cid, "weight": f.get("confidence", 1.0)})
        elif pred == "OWNS_PRODUCT":
            owned_prods.append({"id": cid, "weight": f.get("confidence", 1.0)})

    return {
        "user_id": user_id,
        "skin_type": None,
        "owned_product_ids": owned_prods,
        "owned_family_ids": owned_fam,
        "repurchased_family_ids": repurchased_fam,
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
        "brand_id": "b1", "brand_name": "B",
        "category_id": "c1", "category_name": "C",
        "variant_family_id": family_id,
        "price": 10000,
        "ingredient_concept_ids": [], "category_concept_ids": [],
        "brand_concept_ids": [], "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [], "top_keyword_ids": [],
        "top_context_ids": [], "top_concern_pos_ids": [],
        "top_concern_neg_ids": [], "top_tool_ids": [],
        "top_comparison_product_ids": [], "top_coused_product_ids": [],
        "review_count_30d": 5, "review_count_90d": 20, "review_count_all": 50,
        "last_signal_at": None,
    }


def test_owned_family_matches_through_adapter():
    """OWNS_FAMILY from adapter (product:FAM001) must match raw variant_family_id (FAM001)."""
    user = _build_serving_user("u1", owned_family_ids=["FAM001"])
    # Verify the adapter produced IRI-prefixed IDs
    fam_ids = [f["id"] for f in user["owned_family_ids"]]
    assert any("product:" in fid for fid in fam_ids), \
        f"Adapter should produce product: prefixed family IDs, got {fam_ids}"

    products = [_product("P002", "FAM001"), _product("P003", "FAM999")]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p002 = next((c for c in candidates if c.product_id == "P002"), None)
    assert p002 is not None
    assert p002.owned_family_match is True, \
        f"owned_family_match should be True. User fam IDs: {fam_ids}, product family: FAM001"


def test_repurchased_family_matches_through_adapter():
    """REPURCHASES_FAMILY from adapter must match raw variant_family_id."""
    user = _build_serving_user("u1", repurchased_family_ids=["FAM001"])
    fam_ids = [f["id"] for f in user["repurchased_family_ids"]]
    assert any("product:" in fid for fid in fam_ids), \
        f"Adapter should produce product: prefixed family IDs, got {fam_ids}"

    products = [_product("P002", "FAM001")]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p002 = next((c for c in candidates if c.product_id == "P002"), None)
    assert p002 is not None
    assert p002.repurchased_family_match is True, \
        f"repurchased_family_match should be True. User fam IDs: {fam_ids}, product family: FAM001"


def test_scorer_family_features_fire_with_adapter_ids():
    """Scorer family features must produce non-zero contributions with adapter-generated IDs."""
    user = _build_serving_user("u1", owned_family_ids=["FAM001"], repurchased_family_ids=["FAM002"])
    scorer = Scorer()
    scorer.load_from_dict({
        "owned_family_penalty": 1.0,
        "repurchase_family_affinity": 1.0,
        "same_family_explore_bonus": 1.0,
    })

    # Product in owned family — penalty should fire
    result_owned = scorer.score(user, _product("P002", "FAM001"))
    penalty = result_owned.feature_contributions.get("owned_family_penalty", 0)
    assert penalty < 0, \
        f"owned_family_penalty should be retained. Got contributions: {result_owned.feature_contributions}"
    result_no_family = scorer.score(user, _product("P099", "FAM999"))
    assert result_owned.raw_score != result_no_family.raw_score, \
        "Family features should affect raw_score"

    # Product in repurchased family — affinity should fire
    result_repurchased = scorer.score(user, _product("P003", "FAM002"))
    affinity = result_repurchased.feature_contributions.get("repurchase_family_affinity", 0)
    assert affinity > 0, \
        f"repurchase_family_affinity should fire. Got contributions: {result_repurchased.feature_contributions}"
