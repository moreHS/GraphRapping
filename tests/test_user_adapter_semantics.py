"""Tests: user adapter concept mapping correctness."""
from src.user.adapters.personal_agent_adapter import adapt_user_profile


def _make_profile(chat_textures=None, purchase_features=None):
    profile = {
        "basic": {"skin_type": "건성", "skin_tone": "웜톤"},
        "purchase_analysis": {
            "preferred_skincare_brand": ["라네즈"],
            "preferred_makeup_brand": [],
            "active_product_category": ["에센스"],
            "preferred_repurchase_category": ["에센스", "크림"],
        },
        "chat": {
            "face": {
                "skin_concerns": ["건조함"],
                "skincare_goals": ["보습강화"],
                "preferred_texture": chat_textures or [],
            },
            "hair": {},
            "scent": {"preferences": []},
            "ingredients": {"preferred": [], "avoid": [], "allergy": []},
        },
    }
    return profile


def test_texture_generates_attr_and_keyword():
    profile = _make_profile(chat_textures=["젤", "가벼운 로션"])
    facts = adapt_user_profile("u1", profile)
    predicates = [(f["predicate"], f["concept_type"], f["concept_value"]) for f in facts]
    # Should have Texture axis BEE_ATTR
    assert any(p == "PREFERS_BEE_ATTR" and v == "bee_attr_formulation" for p, t, v in predicates)
    # Should have specific keywords
    assert any(p == "PREFERS_KEYWORD" and "GelLike" in v for p, t, v in predicates)
    assert any(p == "PREFERS_KEYWORD" and "LightLotionLike" in v for p, t, v in predicates)


def test_owns_product_is_entity_ref():
    profile = _make_profile()
    pf = {"owned_product_ids": ["P001", "P002"], "repurchased_brand_ids": [], "recently_purchased_brand_ids": []}
    facts = adapt_user_profile("u1", profile, purchase_features=pf)
    owns = [f for f in facts if f["predicate"] == "OWNS_PRODUCT"]
    assert len(owns) == 2
    for f in owns:
        assert f["concept_type"] == "Product"


def test_repurchase_brand_category_split():
    profile = _make_profile()
    pf = {"owned_product_ids": [], "repurchased_brand_ids": ["brand_laneige"], "recently_purchased_brand_ids": []}
    facts = adapt_user_profile("u1", profile, purchase_features=pf)
    # Category repurchase from profile
    cat_repurchase = [f for f in facts if f["predicate"] == "REPURCHASES_CATEGORY"]
    assert len(cat_repurchase) >= 1
    assert any(f["concept_value"] in ("에센스", "크림") for f in cat_repurchase)
    # Brand repurchase from purchase_features
    brand_repurchase = [f for f in facts if f["predicate"] == "REPURCHASES_BRAND"]
    assert len(brand_repurchase) == 1
    assert brand_repurchase[0]["concept_value"] == "brand_laneige"
    # Old mixed predicate should NOT be generated
    old_mixed = [f for f in facts if f["predicate"] == "REPURCHASES_PRODUCT_OR_FAMILY"]
    assert len(old_mixed) == 0
