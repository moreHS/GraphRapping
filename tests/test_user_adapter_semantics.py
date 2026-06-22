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


def test_adapter_preserves_all_purchase_brand_domains_and_basic_concerns():
    profile = {
        "basic": {
            "skin_type": "건성",
            "skin_concerns": ["건조함"],
        },
        "purchase_analysis": {
            "preferred_skincare_brand": ["라네즈"],
            "preferred_makeup_brand": ["헤라"],
            "preferred_bodycare_brand": ["일리윤"],
            "preferred_hair_brand": ["려"],
            "preferred_perfume_brand": ["구딸"],
        },
        "chat": None,
    }

    facts = adapt_user_profile("u1", profile)
    brand_values = {
        f["concept_value"]
        for f in facts
        if f["predicate"] == "PREFERS_BRAND"
    }
    concerns = {
        f["concept_value"]
        for f in facts
        if f["predicate"] == "HAS_CONCERN"
    }

    assert {"라네즈", "헤라", "일리윤", "려", "구딸"} <= brand_values
    assert concerns


def test_adapter_reads_more_chat_domains_and_scent_shape():
    profile = {
        "basic": {},
        "purchase_analysis": {},
        "chat": {
            "body": {
                "body_concerns": ["건조함"],
                "bodycare_goals": ["보습"],
            },
            "scalp": {
                "scalp_concerns": ["민감"],
                "scalpcare_goals": ["진정"],
            },
            "makeup": {
                "makeup_concerns": ["무너짐"],
                "makeup_goals": ["지속력"],
                "preferred_texture": ["매트"],
            },
            "scent": {"preferred_scent": ["시트러스"]},
            "ingredients": {"preferred": [], "avoid": [], "allergy": []},
        },
    }

    facts = adapt_user_profile("u1", profile)
    predicates = [f["predicate"] for f in facts]
    keyword_values = {
        f["concept_value"]
        for f in facts
        if f["predicate"] == "PREFERS_KEYWORD"
    }

    assert predicates.count("HAS_CONCERN") >= 3
    assert predicates.count("WANTS_GOAL") >= 3
    assert keyword_values
