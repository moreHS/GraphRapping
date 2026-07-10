"""Personalization contract checklist — automated regression for
docs/architecture/recommendation_signal_flow_2026_06_23.md 검토 체크리스트.

Each test below turns one row of that checklist's "이상 신호" (anomaly) column
into an assertion that the anomaly is impossible. If one of these tests starts
failing, the corresponding contract has silently broken and personalization is
producing wrong evidence/eligibility/scoring.

Enforcement points traced before writing these tests (see final report for the
full trace):

- src/rec/candidate_generator.py — overlap concept generation + hard filters +
  the `require_evidence` eligibility gate.
- src/rec/recommendation_evidence_index.py — overlap-prefix → evidence-family
  classification (MASTER_TRUTH_TYPES deliberately excludes `active_category`).
- src/rec/scorer.py — feature computation and score-layer grouping
  (`_score_layers`).
- src/rec/scoped_preferences.py — scope-gated preference collection used by
  candidate_generator for keyword/category/etc. preference sets.
- src/rec/semantic_compatibility.py — value-and-polarity gated semantic rule
  matching (generic axis exclusion happens in candidate_generator via
  `_exclude_generic_bee_attrs`, not here).

Fixtures follow the same synthetic-dict style as
tests/test_evidence_first_candidate_gate.py and
tests/test_recommendation_semantic_compatibility.py.
"""

from __future__ import annotations

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.recommendation_evidence_index import (
    MASTER_TRUTH_TYPES,
    build_candidate_eligibility,
)
from src.rec.scorer import Scorer


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


def _score(user, product, overlap_concepts, weights):
    scorer = Scorer()
    scorer.load_from_dict(weights, shrinkage_k=0)
    return scorer.score(user, product, overlap_concepts)


# ---------------------------------------------------------------------------
# (1) ACTIVE_IN_CATEGORY -> active_category_affinity: profile context only.
#
# Enforced at:
#   - src/rec/candidate_generator.py:203-212 (active_category:* overlap,
#     generated from a *separate* code path than PREFERS_CATEGORY)
#   - src/rec/recommendation_evidence_index.py MASTER_TRUTH_TYPES (line 13-19)
#     deliberately excludes "active_category" — see the module comment at
#     line 20-22.
#   - src/rec/scorer.py:123,143 (active_category_score_units ->
#     active_category_affinity) and _score_layers (line 212-215) which routes
#     active_category_affinity into profile_fit_score, never master_truth_score.
# ---------------------------------------------------------------------------


def test_active_in_category_alone_does_not_grant_eligibility():
    """(1a) A product matched only via active category must not be a candidate."""
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


def test_active_category_overlap_is_not_classified_as_master_truth():
    """(1b) active_category:* must never resolve to PRODUCT_MASTER_TRUTH."""
    assert "active_category" not in MASTER_TRUTH_TYPES

    eligibility = build_candidate_eligibility(["active_category:concept:Category:skincare"])

    assert eligibility.eligible is False
    assert eligibility.evidence_families == []
    assert eligibility.master_truth_paths == []


def test_active_category_overlap_does_not_contribute_to_master_truth_score():
    """(1c) Even when a candidate is otherwise eligible (brand truth present),
    an accompanying active_category overlap must not leak into
    master_truth_score — it may only feed active_category_affinity under
    profile_fit_score.
    """
    user = _user(
        preferred_brand_ids=[{"id": "concept:Brand:brand_a", "weight": 1.0}],
        active_category_ids=[{"id": "concept:Category:skincare", "weight": 1.0}],
    )
    products = [
        _product(
            brand_concept_ids=["concept:Brand:brand_a"],
            category_id="스킨케어",
            category_name="스킨케어",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert any(c.startswith("brand:") for c in candidate.overlap_concepts)
    assert any(c.startswith("active_category:") for c in candidate.overlap_concepts)

    scored = _score(
        user,
        products[0],
        candidate.overlap_concepts,
        weights={"brand_match_conf_weighted": 1.0, "active_category_affinity": 1.0},
    )

    # active_category_affinity feature fires...
    assert scored.feature_contributions.get("active_category_affinity", 0.0) > 0
    # ...but it lands in profile_fit_score, never master_truth_score.
    assert scored.score_layers["profile_fit_score"] > 0
    assert scored.score_layers["master_truth_score"] == round(
        scored.feature_contributions.get("brand_match_conf_weighted", 0.0), 4
    )


def test_active_category_affinity_feature_lives_under_profile_fit_score_layer():
    """(1d) Direct feature->layer grouping check for active_category_affinity."""
    user = _user()
    product = _product()
    scored = _score(
        user,
        product,
        ["active_category:concept:Category:skincare"],
        weights={"active_category_affinity": 1.0},
    )
    assert scored.feature_contributions["active_category_affinity"] > 0
    assert scored.score_layers["profile_fit_score"] == scored.feature_contributions["active_category_affinity"]
    assert scored.score_layers["master_truth_score"] == 0


# ---------------------------------------------------------------------------
# (2) PREFERS_CATEGORY -> category_affinity: only from explicit category
# preference, never from active_product_category.
#
# Enforced at:
#   - src/rec/candidate_generator.py:128-130 (preferred_categories collected
#     from "preferred_category_ids" / PREFERS_CATEGORY only) vs line 125-127
#     (active_categories collected from a distinct "active_category_ids" /
#     ACTIVE_IN_CATEGORY key). These are separate dict keys end to end; there
#     is no code path that folds active_category_ids into preferred_categories.
#   - src/rec/candidate_generator.py:199-212 emits "category:*" only for
#     preferred_category_matches/groups, and "active_category:*" only for
#     active_category_matches/groups — disjoint overlap prefixes.
# ---------------------------------------------------------------------------


def test_category_affinity_overlap_not_generated_from_active_category_alone():
    """(2a) With only active_category_ids set (no preferred_category_ids), no
    'category:*' overlap concept (PREFERS_CATEGORY family) may appear —
    only 'active_category:*' may appear, and it must not qualify eligibility.
    """
    user = _user(active_category_ids=[{"id": "concept:Category:skincare", "weight": 1.0}])
    products = [_product(category_id="스킨케어", category_name="스킨케어")]

    # No eligible candidates at all (matches contract row 1), and specifically
    # no bare "category:" (as opposed to "active_category:") overlap exists
    # anywhere in the generation path for this profile.
    all_products_incl_filtered = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, require_evidence=False
    )
    assert len(all_products_incl_filtered) == 1
    overlap = all_products_incl_filtered[0].overlap_concepts
    assert not any(c.startswith("category:") for c in overlap)
    assert any(c.startswith("active_category:") for c in overlap)


def test_category_affinity_overlap_requires_explicit_preferred_category():
    """(2b) Explicit preferred_category_ids does produce the 'category:*'
    PRODUCT_MASTER_TRUTH overlap and eligibility — establishing the positive
    control for the negative assertion above.
    """
    user = _user(preferred_category_ids=[{"id": "concept:Category:skincare", "weight": 1.0}])
    products = [_product(category_id="스킨케어", category_name="스킨케어")]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("category:") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.master_truth_paths


# ---------------------------------------------------------------------------
# (3) PREFERS_KEYWORD -> catalog_keyword: fires only when the keyword value is
# textually present in product name/category text, and only within the
# preference's scope (e.g. makeup keyword must not fire for a skincare
# product).
#
# Enforced at:
#   - src/rec/candidate_generator.py:217-218 (_catalog_text_matches call)
#   - src/rec/candidate_generator.py:398-406 (_catalog_text_matches: literal
#     substring containment against product_category_text())
#   - src/rec/scoped_preferences.py collect_preference_ids/scope_allows: when
#     scoped_preference_ids is populated, only entries whose scope_group
#     matches the product's classified category_group are collected.
# ---------------------------------------------------------------------------


def test_catalog_keyword_fires_only_when_keyword_text_present_in_product_text():
    """(3a) Keyword absent from product name/category text -> no catalog_keyword
    overlap and no eligibility, even though the user has the preference.
    """
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:틴트", "weight": 1.0}])
    products = [
        _product(
            category_name="스킨케어 크림",
            product_name="수분 진정 크림",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_catalog_keyword_fires_when_keyword_text_present():
    """(3b) Positive control: keyword text present in product name -> fires."""
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


def test_catalog_keyword_scoped_to_makeup_does_not_fire_for_skincare_product():
    """(3c) A makeup-scoped keyword preference must not fire for a product
    classified as skincare, even if the literal text happens to match.
    """
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
    products = [
        _product(
            category_name="스킨케어 크림",
            product_name="매트 피니시 수분 크림",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_catalog_keyword_scoped_to_makeup_fires_for_makeup_product():
    """(3d) Same scoped preference does fire once the product is in-scope."""
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
    products = [
        _product(
            category_name="메이크업 쿠션",
            product_name="매트 쿠션",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert "catalog_keyword:concept:Keyword:매트" in candidates[0].overlap_concepts


# ---------------------------------------------------------------------------
# (4) REPURCHASES_CATEGORY -> repurchase_category: fires only when the
# repurchase-category value textually reaches product name/category text; must
# not fire from active category.
#
# Enforced at:
#   - src/rec/candidate_generator.py:219-220 (_catalog_text_matches against
#     repurchase_category_ids)
#   - src/rec/candidate_generator.py:70 repurchase_category_ids is read
#     directly from user_profile — a field entirely distinct from
#     active_category_ids (line 125-127); there is no shared code path.
#   - src/rec/recommendation_evidence_index.py PURCHASE_BEHAVIOR_TYPES
#     includes "repurchase_category" (line 46), classified as PURCHASE_BEHAVIOR,
#     not profile-context-only.
# ---------------------------------------------------------------------------


def test_repurchase_category_fires_only_when_text_matches_product():
    """(4a) Repurchase category value absent from product text -> no overlap,
    not eligible.
    """
    user = _user(repurchase_category_ids=[{"id": "concept:Category:틴트", "weight": 1.0}])
    products = [
        _product(
            category_name="스킨케어 크림",
            product_name="수분 진정 크림",
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_repurchase_category_fires_when_text_matches_product():
    """(4b) Positive control matching the flow doc's repurchase category path."""
    user = _user(repurchase_category_ids=[{"id": "concept:Category:틴트", "weight": 1.0}])
    products = [_product(category_name="립 틴트", product_name="주스팝 립틴트")]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert "repurchase_category:concept:Category:틴트" in candidates[0].overlap_concepts
    assert candidates[0].eligibility.purchase_paths


def test_repurchase_category_does_not_fire_from_active_category_field():
    """(4c) A user with an ACTIVE_IN_CATEGORY signal for '틴트' (via
    active_category_ids), but no repurchase_category_ids, must not produce a
    'repurchase_category:*' overlap for a matching product — the two source
    fields are never cross-read.
    """
    user = _user(active_category_ids=[{"id": "concept:Category:틴트", "weight": 1.0}])
    products = [_product(category_name="립 틴트", product_name="주스팝 립틴트")]

    candidates = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, require_evidence=False
    )

    assert len(candidates) == 1
    overlap = candidates[0].overlap_concepts
    assert not any(c.startswith("repurchase_category:") for c in overlap)


# ---------------------------------------------------------------------------
# (5) HAS_CONCERN direct concern match vs concern_bridge: separate overlap
# prefixes and separate score features (concern_fit vs concern_bridge_fit),
# never merged.
#
# Enforced at:
#   - src/rec/candidate_generator.py:263-268 (direct "concern:*" overlap from
#     top_concern_pos_ids intersection)
#   - src/rec/candidate_generator.py:270-275 (separate "concern_bridge:*"
#     overlap from compute_bridged_concerns, explicitly skipped when the same
#     concern already has an explicit "concern:*" overlap — no double count)
#   - src/rec/scorer.py:137 concern_fit vs :138 concern_bridge_fit — distinct
#     dict keys, distinct weight lookups.
#   - src/rec/scorer.py _score_layers (line 197-205): both land in
#     review_graph_score, but as separate named feature contributions.
# ---------------------------------------------------------------------------


def test_direct_concern_match_produces_concern_overlap_and_concern_fit_feature():
    user = _user(concern_ids=[{"id": "concern_dryness", "weight": 1.0}])
    products = [_product(top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9, "review_cnt": 5}])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    assert "concern:concern_dryness" in candidates[0].overlap_concepts
    assert not any(c.startswith("concern_bridge:") for c in candidates[0].overlap_concepts)

    scored = _score(
        user, products[0], candidates[0].overlap_concepts,
        weights={"concern_fit": 1.0, "concern_bridge_fit": 1.0},
    )
    assert scored.feature_contributions.get("concern_fit", 0.0) > 0
    assert "concern_bridge_fit" not in scored.feature_contributions


def test_bee_attr_concern_bridge_produces_separate_bridge_overlap_and_feature():
    """BEE attr with a concern_bee_attr_map.yaml mapping (bee_attr_moisturizing_power
    -> concern_dryness) infers concern_bridge:*, scored via concern_bridge_fit —
    never the direct concern_fit feature.
    """
    user = _user(concern_ids=[{"id": "concern_dryness", "weight": 1.0}])
    products = [
        _product(
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 5}
            ],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    overlap = candidates[0].overlap_concepts
    assert "concern_bridge:concern_dryness" in overlap
    assert not any(c == "concern:concern_dryness" for c in overlap)

    scored = _score(
        user, products[0], overlap,
        weights={"concern_fit": 1.0, "concern_bridge_fit": 1.0},
    )
    assert scored.feature_contributions.get("concern_bridge_fit", 0.0) > 0
    assert "concern_fit" not in scored.feature_contributions


def test_concern_bridge_is_suppressed_when_explicit_concern_already_present():
    """When both explicit concern evidence and a bridge-eligible BEE attr point
    at the same concern, the bridge overlap must not double up (candidate_
    generator.py:272-275 explicit_concerns guard).
    """
    user = _user(concern_ids=[{"id": "concern_dryness", "weight": 1.0}])
    products = [
        _product(
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9, "review_cnt": 5}],
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 5}
            ],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    overlap = candidates[0].overlap_concepts
    assert "concern:concern_dryness" in overlap
    assert "concern_bridge:concern_dryness" not in overlap


# ---------------------------------------------------------------------------
# (6) PREFERS_BEE_ATTR -> bee_attr: generic/exact axis overlap alone
# (formulation, texture_feel) must not grant candidate eligibility.
#
# Enforced at:
#   - src/rec/candidate_generator.py:230-237 (_exclude_generic_bee_attrs
#     applied to both user preferred_bee_attrs and product attrs before the
#     exact bee_attr:* overlap is computed)
#   - src/rec/candidate_generator.py:409-414 (_exclude_generic_bee_attrs:
#     excludes get_texture_axis() and "concept:BEEAttr:bee_attr_texture_feel")
# ---------------------------------------------------------------------------


def test_generic_formulation_bee_attr_exact_match_does_not_qualify_candidate():
    user = _user(
        preferred_bee_attr_ids=[{"id": "concept:BEEAttr:bee_attr_formulation", "weight": 1.0}]
    )
    products = [
        _product(
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_formulation", "score": 0.9, "review_cnt": 8}
            ]
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_generic_texture_feel_bee_attr_exact_match_does_not_qualify_candidate():
    user = _user(
        preferred_bee_attr_ids=[{"id": "concept:BEEAttr:bee_attr_texture_feel", "weight": 1.0}]
    )
    products = [
        _product(
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_texture_feel", "score": 0.9, "review_cnt": 8}
            ]
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_non_generic_bee_attr_exact_match_does_qualify_candidate():
    """Positive control: a non-generic BEE attr axis (e.g. moisturizing_power)
    does grant eligibility through the plain bee_attr:* overlap path.
    """
    user = _user(
        preferred_bee_attr_ids=[{"id": "concept:BEEAttr:bee_attr_moisturizing_power", "weight": 1.0}]
    )
    products = [
        _product(
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 8}
            ]
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("bee_attr:") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.review_graph_paths


# ---------------------------------------------------------------------------
# (7) AVOIDS_INGREDIENT is a hard filter: complete exclusion, not a penalty.
#
# Enforced at:
#   - src/rec/candidate_generator.py:142-149: when avoided_ingredients
#     intersects product ingredients, hard_filtered=True and the candidate is
#     `continue`-d before any overlap/score computation — it never reaches
#     scoring at all.
#   - src/rec/candidate_generator.py:319 `valid = [c for c in candidates if
#     not c.hard_filtered]` removes it from the returned candidate list
#     entirely (this holds regardless of require_evidence, matching
#     tests/test_evidence_first_candidate_gate.py::
#     test_hard_filter_only_checks_can_opt_out_of_evidence_gate).
# ---------------------------------------------------------------------------


def test_avoided_ingredient_product_is_completely_excluded_not_penalized():
    user = _user(
        avoided_ingredient_ids=[{"id": "concept:Ingredient:bad"}],
        concern_ids=[{"id": "concern_dryness", "weight": 1.0}],
    )
    products = [
        _product(
            "bad",
            ingredient_concept_ids=["concept:Ingredient:bad"],
            # Strong positive signals that would otherwise make this the
            # top-scoring candidate if it were merely penalized rather than
            # hard-filtered.
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9, "review_cnt": 20}],
        ),
        _product(
            "safe",
            ingredient_concept_ids=["concept:Ingredient:safe"],
            # Same concern evidence as "bad", so both are otherwise
            # equally eligible -- isolating ingredient avoidance as the only
            # variable that should decide inclusion.
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.9, "review_cnt": 20}],
        ),
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    # The avoided-ingredient product must be entirely absent from the result
    # set -- not merely ranked lower than "safe".
    assert {c.product_id for c in candidates} == {"safe"}


def test_avoided_ingredient_hard_filter_holds_even_with_require_evidence_false():
    """(7b) The hard filter is independent of the require_evidence gate — it
    is a `continue` before eligibility is even computed (candidate_generator.py
    :142-149), so disabling require_evidence must not resurrect the product.
    """
    user = _user(avoided_ingredient_ids=[{"id": "concept:Ingredient:bad"}])
    products = [
        _product("safe", ingredient_concept_ids=["concept:Ingredient:safe"]),
        _product("bad", ingredient_concept_ids=["concept:Ingredient:bad"]),
    ]

    candidates = generate_candidates(
        user, products, mode=RecommendationMode.EXPLORE, require_evidence=False
    )

    assert {c.product_id for c in candidates} == {"safe"}


def test_avoided_ingredient_candidate_never_reaches_scoring_stage():
    """(7c) Filtered-out candidates are dropped from the returned list, so a
    caller iterating the candidate list to build overlap_concepts for scoring
    can never accidentally score an avoided-ingredient product.
    """
    user = _user(avoided_ingredient_ids=[{"id": "concept:Ingredient:bad"}])
    products = [_product("bad", ingredient_concept_ids=["concept:Ingredient:bad"])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


# ---------------------------------------------------------------------------
# (8) source_review_* is trust/tie-break only: cannot be the sole eligibility
# basis for a candidate, only contributes to source_trust_score.
#
# Enforced at:
#   - src/rec/recommendation_evidence_index.py: no "source_review_*" prefix is
#     present in MASTER_TRUTH_TYPES / REVIEW_GRAPH_TYPES /
#     REVIEW_GRAPH_WEAK_TYPES / PURCHASE_BEHAVIOR_TYPES, so classify_overlap()
#     returns None for it and it can never make eligible=True.
#   - src/rec/candidate_generator.py never appends a "source_review_*" or
#     "source_review_stats:*" overlap concept from source_review_count_6m/
#     source_avg_rating_6m at all -- those fields feed the scorer directly
#     (score-time only), not overlap_concepts.
#   - src/rec/scorer.py:282-293 (_source_popularity_score) and :296-308
#     (_source_rating_score) read source_review_count_6m/source_avg_rating_6m
#     directly off the product dict and only feed source_trust_score
#     (_score_layers line 225-228).
# ---------------------------------------------------------------------------


def test_high_source_stats_alone_do_not_make_product_eligible():
    """(8a) Extremely strong source stats with zero profile-aligned evidence
    still yield zero candidates.
    """
    user = _user()
    products = [
        _product(
            source_review_count_6m=999_999,
            source_avg_rating_6m=5.0,
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_source_review_stats_overlap_concept_is_not_eligibility_evidence():
    """(8b) Even if a 'source_review_stats:*' overlap concept were injected
    directly (defensive check on the classifier itself, matching the existing
    test_recommendation_semantic_compatibility.py::
    test_source_review_stats_are_not_eligibility_evidence pattern), it must
    resolve to no evidence family.
    """
    eligibility = build_candidate_eligibility(["source_review_stats:source_review_count_6m"])

    assert eligibility.eligible is False
    assert eligibility.evidence_families == []


def test_source_stats_only_contribute_to_source_trust_score_layer():
    """(8c) source_review_count_6m / source_avg_rating_6m feed
    source_popularity_score / source_rating_score, and those live exclusively
    under source_trust_score — never master_truth/review_graph/purchase layers.
    """
    user = _user()
    product = _product(source_review_count_6m=5000, source_avg_rating_6m=4.9)

    scored = _score(
        user,
        product,
        overlap_concepts=[],
        weights={"source_popularity_score": 1.0, "source_rating_score": 1.0},
    )

    assert scored.score_layers["source_trust_score"] > 0
    assert scored.score_layers["master_truth_score"] == 0
    assert scored.score_layers["review_graph_score"] == 0
    assert scored.score_layers["purchase_behavior_score"] == 0
    assert scored.score_layers["profile_fit_score"] == 0


# ---------------------------------------------------------------------------
# (9) review_summary_sidecar has no effect on candidate set or scoring.
#
# Enforced by absence: grep across src/rec/candidate_generator.py and
# src/rec/scorer.py shows zero references to "review_summary_sidecar" or any
# sidecar-derived field. The only consumers are src/web/server.py and
# src/web/review_summary_sidecar.py (display-layer join), which sit entirely
# outside the recommendation candidate/score path exercised here. These tests
# assert that behavior empirically: attaching an arbitrary
# review_summary_sidecar payload to the product dict changes neither the
# candidate set nor the score.
# ---------------------------------------------------------------------------


def _sidecar_payload():
    return {
        "summary_text": "이 제품은 20대 여성에게 인기 있는 진정 크림입니다.",
        "gender_skew": "female",
        "age_skew": "20s",
        "status_tags": ["trending"],
    }


def test_review_summary_sidecar_does_not_affect_candidate_eligibility():
    user = _user(preferred_brand_ids=[{"id": "concept:Brand:brand_a", "weight": 1.0}])
    product_without_sidecar = _product(brand_concept_ids=["concept:Brand:brand_a"])
    product_with_sidecar = _product(
        brand_concept_ids=["concept:Brand:brand_a"],
        review_summary_sidecar=_sidecar_payload(),
    )

    candidates_without = generate_candidates(user, [product_without_sidecar], mode=RecommendationMode.EXPLORE)
    candidates_with = generate_candidates(user, [product_with_sidecar], mode=RecommendationMode.EXPLORE)

    assert len(candidates_without) == len(candidates_with) == 1
    assert candidates_without[0].overlap_concepts == candidates_with[0].overlap_concepts
    assert candidates_without[0].eligibility.to_dict() == candidates_with[0].eligibility.to_dict()


def test_review_summary_sidecar_alone_does_not_create_candidate():
    """(9b) A product with only a sidecar payload and no profile-aligned
    evidence must still be filtered out entirely.
    """
    user = _user()
    products = [_product(review_summary_sidecar=_sidecar_payload())]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_review_summary_sidecar_does_not_affect_score():
    user = _user()
    product_without_sidecar = _product(source_review_count_6m=5000, source_avg_rating_6m=4.9)
    product_with_sidecar = _product(
        source_review_count_6m=5000,
        source_avg_rating_6m=4.9,
        review_summary_sidecar=_sidecar_payload(),
    )

    weights = {
        "source_popularity_score": 1.0,
        "source_rating_score": 1.0,
        "keyword_match": 1.0,
    }
    scored_without = _score(user, product_without_sidecar, [], weights)
    scored_with = _score(user, product_with_sidecar, [], weights)

    assert scored_without.raw_score == scored_with.raw_score
    assert scored_without.score_layers == scored_with.score_layers
    assert scored_without.feature_contributions == scored_with.feature_contributions


# ---------------------------------------------------------------------------
# (5b) WANTS_GOAL -> semantic_bee_attr/semantic_keyword category_scope gating.
#
# Checklist row: "WANTS_GOAL -> semantic_keyword/semantic_bee_attr | rule 기반
# semantic match | 규칙이 너무 넓거나 category-scope가 필요해 보이면 검토". Phase 3.1
# (fable_doc/03_improvement_plan.md §3.1) adds category_scope to the semantic
# rule schema and scopes the performance/long_lasting rule to [makeup, fragrance]
# — the documented Broad Semantic leak where a global 지속력 goal flooded the
# skincare tab via bee_attr_lasting_power with no other evidence.
#
# Enforced at:
#   - src/rec/semantic_compatibility.py _rule_allows_category: a rule with a
#     category_scope only fires for products classified into a listed group.
#   - configs/recommendation_semantic_compatibility.yaml: long_lasting carries
#     category_scope: [makeup, fragrance]; every other rule stays global.
# ---------------------------------------------------------------------------


def test_scoped_lasting_semantic_does_not_qualify_skincare_candidate():
    """(5b-i) A user whose only skincare-aligned signal is 지속력 must not get a
    skincare candidate off bee_attr_lasting_power — the rule is out of scope for
    skincare, so the tab is legitimately empty (evidence-first no-candidate).
    """
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    products = [
        _product(
            category_name="스킨케어 수분 크림",
            product_name="수분 진정 크림",
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8}
            ],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert candidates == []


def test_scoped_lasting_semantic_still_qualifies_makeup_candidate():
    """(5b-ii) Positive control: the same signal keeps qualifying a makeup
    product (makeup is in scope), so scoping removed only the leak, not the
    legitimate makeup-tab review-graph evidence.
    """
    user = _user(goal_ids=[{"id": "concept:Goal:지속력", "weight": 1.0}])
    products = [
        _product(
            category_name="메이크업 쿠션",
            product_name="롱래스팅 쿠션",
            top_bee_attr_ids=[
                {"id": "concept:BEEAttr:bee_attr_lasting_power", "score": 0.9, "review_cnt": 8}
            ],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(
        c.startswith("semantic_bee_attr:performance:long_lasting")
        for c in candidates[0].overlap_concepts
    )
    assert candidates[0].eligibility.review_graph_paths


def test_unscoped_global_rule_still_fires_for_skincare_candidate():
    """(5b-iii) Gating is per-rule: an unscoped (global) rule such as
    moisture/moist keeps qualifying a skincare product, proving the scope gate
    did not blanket-disable skincare semantic matching.
    """
    user = _user(goal_ids=[{"id": "concept:Goal:보습", "weight": 1.0}])
    products = [
        _product(
            category_name="스킨케어 수분 크림",
            product_name="수분 진정 크림",
            top_keyword_ids=[
                {"id": "concept:Keyword:kw_moist", "score": 0.9, "review_cnt": 8}
            ],
        )
    ]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    assert any(c.startswith("semantic_keyword:moisture:moist") for c in candidates[0].overlap_concepts)
    assert candidates[0].eligibility.review_graph_paths
