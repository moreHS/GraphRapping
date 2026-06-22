from src.loaders.user_loader import load_users_from_profiles
from src.user.profile_purchase_summary import derive_purchase_summary_features


def _masters():
    return {
        "P1": {
            "product_id": "P1",
            "source_product_id": "111972405",
            "product_name": "라네즈 립글로이밤",
            "representative_product_name": "라네즈 립글로이밤",
            "brand_id": "laneige",
            "category_id": "lipcare",
            "variant_family_id": "FAM_LIP",
        },
        "P2": {
            "product_id": "P2",
            "source_product_id": "110652331",
            "product_name": "마몽드 카밍샷아줄렌앰플",
            "representative_product_name": "마몽드 카밍샷아줄렌앰플",
            "brand_id": "mamonde",
            "category_id": "essence",
            "variant_family_id": "FAM_AMP",
        },
    }


def test_purchase_summary_resolves_exact_code_and_name_to_purchase_features():
    profile = {
        "basic": {},
        "purchase_analysis": {
            "preferred_repurchase_product_summary": {
                "메이크업": {
                    "립밤": [
                        {
                            "rprs_prd_cd": "111972405",
                            "rprs_prd_nm": "라네즈 립글로이밤",
                            "recent_purchase_date": "2026-01-02",
                        }
                    ]
                }
            },
            "use_expected_product_summary": {
                "스킨케어": {
                    "에센스": [
                        {
                            "rprs_prd_nm": "마몽드 카밍샷아줄렌앰플",
                            "purchase_date": "2026-01-01",
                        }
                    ]
                }
            },
        },
        "chat": None,
    }

    features = derive_purchase_summary_features(profile, _masters())

    assert features is not None
    assert features["owned_product_ids"] == ["P1", "P2"]
    assert features["owned_family_ids"] == ["FAM_AMP", "FAM_LIP"]
    assert features["repurchased_family_ids"] == ["FAM_LIP"]
    assert features["repurchased_brand_ids"] == ["laneige"]
    assert features["recently_purchased_brand_ids"] == ["laneige", "mamonde"]
    assert features["last_seen_at"] == "2026-01-02"


def test_user_loader_forwards_resolved_summary_features_to_adapter():
    profiles = {
        "u1": {
            "basic": {},
            "purchase_analysis": {
                "preferred_repurchase_product_summary": {
                    "메이크업": {
                        "립밤": [{"rprs_prd_cd": "111972405", "recent_purchase_date": "2026-01-02"}]
                    }
                }
            },
            "chat": None,
        }
    }

    result = load_users_from_profiles(profiles, product_masters=_masters())
    facts = result.user_adapted_facts["u1"]

    by_predicate = {}
    for fact in facts:
        by_predicate.setdefault(fact["predicate"], set()).add(fact["concept_id"])

    assert "product:P1" in by_predicate["OWNS_PRODUCT"]
    assert "product:FAM_LIP" in by_predicate["REPURCHASES_FAMILY"]
    assert "concept:Brand:laneige" in by_predicate["REPURCHASES_BRAND"]
    assert "concept:Brand:laneige" in by_predicate["RECENTLY_PURCHASED"]


def test_unresolved_summary_product_does_not_create_silent_purchase_fact():
    profile = {
        "basic": {},
        "purchase_analysis": {
            "use_expected_product_summary": {
                "스킨케어": {"크림": [{"rprs_prd_nm": "없는 제품"}]}
            }
        },
        "chat": None,
    }

    assert derive_purchase_summary_features(profile, _masters()) is None

