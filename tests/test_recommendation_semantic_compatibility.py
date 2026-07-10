from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.explainer import explain
from src.rec.recommendation_evidence_index import build_candidate_eligibility
from src.rec.scorer import Scorer
from src.rec.semantic_compatibility import find_semantic_matches


def _user(**overrides):
    base = {
        "user_id": "u1",
        "preferred_brand_ids": [],
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
        "review_count_all": 100,
        "source_review_count_6m": 5000,
        "source_avg_rating_6m": 4.9,
    }
    base.update(overrides)
    return base


def test_axis_only_texture_or_formulation_preference_does_not_score():
    user = _user(
        preferred_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_formulation", "weight": 1.0},
        ]
    )
    product = _product(
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_texture_feel", "score": 0.9, "review_cnt": 8},
        ]
    )

    assert find_semantic_matches(user, product) == []

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_exact_generic_formulation_axis_does_not_qualify_candidate():
    user = _user(
        preferred_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_formulation", "weight": 1.0},
        ]
    )
    product = _product(
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_formulation", "score": 0.9, "review_cnt": 8},
        ]
    )

    assert find_semantic_matches(user, product) == []
    assert generate_candidates(user, [product], mode=RecommendationMode.EXPLORE) == []


def test_goal_intent_can_match_review_graph_keyword_semantically():
    user = _user(goal_ids=[{"id": "concept:Goal:보습", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:kw_moist", "score": 0.9, "review_cnt": 8},
        ]
    )

    matches = find_semantic_matches(user, product)
    assert [m.product_id for m in matches] == ["concept:Keyword:kw_moist"]

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    assert any(c.startswith("semantic_keyword:moisture:moist") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.review_graph_paths


def test_lasting_goal_can_match_review_graph_lasting_attr_semantically():
    # Phase 3.1: the long_lasting rule is category_scope: [makeup, fragrance], so
    # the product must classify into an in-scope group for the match to fire.
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    product = _product(
        category_name="메이크업 쿠션",
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("semantic_bee_attr:performance:long_lasting") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.review_graph_paths


def test_lasting_goal_scoped_out_of_skincare_product():
    """Phase 3.1 leak regression: the observed Broad Semantic case
    (docs/architecture/recommendation_signal_flow_2026_06_23.md). A skincare
    product whose only user-aligned signal is 지속력 -> bee_attr_lasting_power must
    no longer become a candidate, because the long_lasting rule is scoped to
    makeup/fragrance. This is the intended evidence-first outcome, not a
    regression (DECISIONS/2026-06-19).
    """
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    product = _product(
        category_name="스킨케어 수분 크림",
        product_name="수분 진정 크림",
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8},
        ],
    )

    assert find_semantic_matches(user, product) == []
    assert generate_candidates(user, [product], mode=RecommendationMode.EXPLORE) == []


def test_lasting_goal_matches_lasting_attr_for_fragrance_product():
    """The long_lasting scope also covers fragrance (scent longevity), so a
    fragrance product keeps the semantic match.
    """
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    product = _product(
        category_name="향수 오드퍼퓸",
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("semantic_bee_attr:performance:long_lasting") for c in candidates[0].overlap_concepts)


def test_global_moisture_rule_still_fires_for_skincare_product():
    """Control: category_scope gating is per-rule. A global rule (moisture) must
    keep firing for a skincare product even though long_lasting is now scoped —
    proving the gate did not blanket-disable semantic matching for skincare.
    """
    user = _user(goal_ids=[{"id": "concept:Goal:보습", "weight": 1.0}])
    product = _product(
        category_name="스킨케어 수분 크림",
        top_keyword_ids=[
            {"id": "concept:Keyword:kw_moist", "score": 0.9, "review_cnt": 8},
        ],
    )

    matches = find_semantic_matches(user, product)
    assert [m.product_id for m in matches] == ["concept:Keyword:kw_moist"]


def test_semantic_explanation_preserves_triggering_user_edge():
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    product = _product(
        category_name="메이크업 쿠션",
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8},
        ],
    )
    candidate = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)[0]

    scorer = Scorer()
    scorer.load_from_dict({"residual_bee_attr_match": 1.0}, shrinkage_k=0)
    scored = scorer.score(user, product, candidate.overlap_concepts)
    explanation = explain(scored, candidate.overlap_concepts)

    assert explanation.paths
    assert explanation.paths[0].concept_type == "semantic_bee_attr"
    assert explanation.paths[0].user_edge == "WANTS_GOAL"


def test_moist_preference_does_not_match_matte_or_oil_control_evidence():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:촉촉", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:매트", "score": 0.9, "review_cnt": 8},
            {"id": "concept:Keyword:오일컨트롤", "score": 0.8, "review_cnt": 6},
        ],
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_oil_control", "score": 0.9, "review_cnt": 7},
        ],
    )

    assert find_semantic_matches(user, product) == []
    assert generate_candidates(user, [product], mode=RecommendationMode.EXPLORE) == []


def test_matte_preference_does_not_get_bonus_from_moist_or_glow_evidence():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:매트", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:촉촉", "score": 0.9, "review_cnt": 8},
            {"id": "concept:Keyword:글로우", "score": 0.8, "review_cnt": 6},
        ],
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 7},
        ],
    )

    assert find_semantic_matches(user, product) == []
    assert generate_candidates(user, [product], mode=RecommendationMode.EXPLORE) == []


def test_compatible_moist_value_evidence_contributes_to_review_graph_score():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:촉촉", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:보습", "score": 0.9, "review_cnt": 8},
        ],
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.85, "review_cnt": 7},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("semantic_keyword:") for c in candidates[0].overlap_concepts)
    assert any(c.startswith("semantic_bee_attr:") for c in candidates[0].overlap_concepts)
    assert "REVIEW_GRAPH_RELATION" in candidates[0].eligibility.evidence_families

    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 1.0, "residual_bee_attr_match": 1.0}, shrinkage_k=10)
    scored = scorer.score(user, product, candidates[0].overlap_concepts)

    assert scored.score_layers["review_graph_score"] > 0
    assert scored.score_layers["review_graph_weak_evidence_score"] == 0


def test_semantic_strength_changes_review_graph_score():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:산뜻", "weight": 1.0}])
    strong_product = _product(
        "strong",
        top_keyword_ids=[
            {"id": "concept:Keyword:흡수", "score": 0.9, "review_cnt": 8},
        ],
    )
    weak_product = _product(
        "weak",
        top_keyword_ids=[
            {"id": "concept:Keyword:끈적임 없음", "score": 0.9, "review_cnt": 8},
        ],
    )

    strong_candidate = generate_candidates(user, [strong_product], mode=RecommendationMode.EXPLORE)[0]
    weak_candidate = generate_candidates(user, [weak_product], mode=RecommendationMode.EXPLORE)[0]

    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 1.0}, shrinkage_k=0)

    strong = scorer.score(user, strong_product, strong_candidate.overlap_concepts)
    weak = scorer.score(user, weak_product, weak_candidate.overlap_concepts)

    concepts = strong_candidate.overlap_concepts + weak_candidate.overlap_concepts
    assert any("|strength=" in concept for concept in concepts)
    assert strong.final_score > weak.final_score


def test_semantic_review_graph_match_is_explainable():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:촉촉", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:보습", "score": 0.9, "review_cnt": 8},
        ],
    )
    candidate = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)[0]

    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 1.0}, shrinkage_k=0)
    scored = scorer.score(user, product, candidate.overlap_concepts)

    explanation = explain(scored, candidate.overlap_concepts)

    assert explanation.paths
    assert explanation.paths[0].concept_type == "semantic_keyword"


def test_fresh_light_value_evidence_can_match_absorption_and_non_sticky_language():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:산뜻", "weight": 1.0}])
    product = _product(
        top_keyword_ids=[
            {"id": "concept:Keyword:끈적임 없음", "score": 0.9, "review_cnt": 8},
        ],
        top_bee_attr_ids=[
            {"id": "concept:BEEAttr:bee_attr_absorption", "score": 0.85, "review_cnt": 7},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("semantic_keyword:") for c in candidates[0].overlap_concepts)
    assert any(c.startswith("semantic_bee_attr:") for c in candidates[0].overlap_concepts)


def test_weak_semantic_relation_is_separated_from_promoted_review_graph_score():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:산뜻", "weight": 1.0}])
    product = _product(
        weak_keyword_ids=[
            {"id": "concept:Keyword:흡수", "score": 0.5, "review_cnt": 1},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.eligibility.review_graph_paths == []
    assert candidate.eligibility.weak_review_graph_paths
    assert "REVIEW_GRAPH_WEAK_RELATION" in candidate.eligibility.evidence_families

    scorer = Scorer()
    scorer.load_from_dict({"review_graph_weak_relation_match": 1.0}, shrinkage_k=10)
    scored = scorer.score(user, product, candidate.overlap_concepts)

    assert scored.score_layers["review_graph_score"] == 0
    assert scored.score_layers["review_graph_weak_evidence_score"] > 0


def test_source_review_stats_are_not_eligibility_evidence():
    eligibility = build_candidate_eligibility(["source_review_stats:source_review_count_6m"])

    assert eligibility.eligible is False
    assert eligibility.evidence_families == []


def test_scoped_makeup_keyword_does_not_qualify_skincare_product():
    user = _user(
        preferred_keyword_ids=[{"id": "concept:Keyword:매트", "weight": 1.0}],
        scoped_preference_ids=[
            {
                "edge_type": "PREFERS_KEYWORD",
                "id": "concept:Keyword:매트",
                "weight": 1.0,
                "scope_group": "makeup",
            }
        ],
    )
    product = _product(
        category_name="스킨케어 크림",
        top_keyword_ids=[
            {"id": "concept:Keyword:매트", "score": 0.9, "review_cnt": 8},
        ],
    )

    assert find_semantic_matches(user, product) == []
    assert generate_candidates(user, [product], mode=RecommendationMode.EXPLORE) == []


def test_scoped_makeup_keyword_matches_makeup_product():
    user = _user(
        preferred_keyword_ids=[{"id": "concept:Keyword:매트", "weight": 1.0}],
        scoped_preference_ids=[
            {
                "edge_type": "PREFERS_KEYWORD",
                "id": "concept:Keyword:매트",
                "weight": 1.0,
                "scope_group": "makeup",
            }
        ],
    )
    product = _product(
        category_name="메이크업 쿠션",
        top_keyword_ids=[
            {"id": "concept:Keyword:매트", "score": 0.9, "review_cnt": 8},
        ],
    )

    candidates = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(concept.startswith("keyword:") for concept in candidates[0].overlap_concepts)
