"""Evidence-first candidate gate tests."""

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates


def _user(**overrides):
    base = {
        "user_id": "u1",
        "preferred_brand_ids": [],
        "active_category_ids": [],
        "preferred_category_ids": [],
        "preferred_ingredient_ids": [],
        "avoided_ingredient_ids": [],
        "concern_ids": [],
        "goal_ids": [],
        "preferred_bee_attr_ids": [],
        "preferred_keyword_ids": [],
        "preferred_context_ids": [],
        "owned_product_ids": [],
        "owned_family_ids": [],
        "repurchased_family_ids": [],
        "recent_purchase_brand_ids": [],
        "repurchase_brand_ids": [],
        "repurchase_category_ids": [],
    }
    base.update(overrides)
    return base


def _product(pid="P1", **overrides):
    base = {
        "product_id": pid,
        "brand_id": "brand_a",
        "category_id": "cat_a",
        "ingredient_ids": [],
        "main_benefit_ids": [],
        "brand_concept_ids": [],
        "category_concept_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [],
        "top_keyword_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_comparison_product_ids": [],
        "top_coused_product_ids": [],
        "review_count_all": 0,
        "source_review_count_6m": 5000,
        "source_avg_rating_6m": 4.9,
    }
    base.update(overrides)
    return base


def test_source_trust_only_product_is_not_candidate():
    user = _user()
    products = [_product()]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_product_master_brand_truth_is_first_class_evidence():
    user = _user(preferred_brand_ids=[{"id": "concept:Brand:brand_a", "weight": 1.0}])
    products = [_product(brand_concept_ids=["concept:Brand:brand_a"])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert any(c.startswith("brand:") for c in candidate.overlap_concepts)
    assert candidate.eligibility.eligible is True
    assert candidate.eligibility.master_truth_paths


def test_product_master_ingredient_truth_is_first_class_evidence():
    user = _user(preferred_ingredient_ids=[{"id": "concept:Ingredient:ceramide", "weight": 1.0}])
    products = [_product(ingredient_concept_ids=["concept:Ingredient:ceramide"])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("ingredient:") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.master_truth_paths


def test_product_master_category_group_alias_is_first_class_evidence():
    user = _user(preferred_category_ids=[{"id": "concept:Category:perfume", "weight": 1.0}])
    products = [
        _product(
            category_id="핸드보습",
            category_name="핸드보습",
            product_name="마이 퍼퓸드 핸드크림",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert "category:concept:Category:fragrance" in candidates[0].overlap_concepts
    assert candidates[0].eligibility.master_truth_paths


def test_catalog_keyword_from_product_master_text_is_first_class_evidence():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:틴트", "weight": 1.0}])
    products = [
        _product(
            category_name="립 틴트",
            product_name="주스팝 립틴트",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert "catalog_keyword:concept:Keyword:틴트" in candidates[0].overlap_concepts
    assert candidates[0].eligibility.master_truth_paths


def test_repurchase_category_matches_product_master_text_as_purchase_evidence():
    user = _user(repurchase_category_ids=[{"id": "concept:Category:틴트", "weight": 1.0}])
    products = [
        _product(
            category_name="립 틴트",
            product_name="주스팝 립틴트",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert "repurchase_category:concept:Category:틴트" in candidates[0].overlap_concepts
    assert candidates[0].eligibility.purchase_paths


def test_active_category_context_alone_is_not_first_class_evidence():
    user = _user(active_category_ids=[{"id": "concept:Category:skincare", "weight": 1.0}])
    products = [
        _product(
            category_id="스킨케어",
            category_name="스킨케어",
            product_name="수분 크림",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_review_relation_evidence_is_first_class_evidence():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:dewy", "weight": 1.0}])
    products = [_product(top_keyword_ids=[{"id": "concept:Keyword:dewy", "score": 0.9, "review_cnt": 4}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("keyword:") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.review_graph_paths


def test_purchase_behavior_is_first_class_evidence():
    user = _user(repurchased_family_ids=[{"id": "product:FAM001", "weight": 1.0}])
    products = [_product(variant_family_id="FAM001")]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("repurchased_family:") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.purchase_paths


def test_hard_filter_only_checks_can_opt_out_of_evidence_gate():
    user = _user(avoided_ingredient_ids=[{"id": "concept:Ingredient:bad"}])
    products = [
        _product("safe", ingredient_concept_ids=["concept:Ingredient:safe"]),
        _product("bad", ingredient_concept_ids=["concept:Ingredient:bad"]),
    ]

    candidates = generate_candidates(
        user,
        products,
        mode=RecommendationMode.EXPLORE,
        require_evidence=False,
    )

    assert {c.product_id for c in candidates} == {"safe"}
