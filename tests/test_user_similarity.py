"""Phase 7 P7-4 D1 — user-user similarity → collaborative-affinity signal.

Covers the full wiring of the first *connectivity* signal:
  * user_similarity: Jaccard similarity, axis namespacing, neighbour threshold,
    collaborative product signal derivation, sparse/cold behaviour.
  * recommendation_evidence_index: `collab` is boost-only and — unlike
    `comparison` — is NEVER admissible (cannot qualify a candidate in ANY mode).
  * candidate_generator: collab overlap generation + the mandatory
    "collab alone is not eligible" contract across STRICT/EXPLORE/COMPARE.
  * scorer: collaborative_affinity is scored in every mode, is zero without a
    collab overlap (default path byte-identical), and lands in review_graph_score.
  * explainer: collab path + "취향이 비슷한 고객" summary.

The default (no upstream collab wiring) recommendation path must stay
byte-identical to pre-D1; the dense/wide ranking snapshots enforce that
separately. Here we prove the collaborative behaviour actually fires when a
caller populates the signal.
"""

from __future__ import annotations

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.explainer import explain
from src.rec.recommendation_evidence_index import (
    BOOST_ONLY_ADMISSIBLE_TYPES,
    BOOST_ONLY_TYPES,
    REVIEW_GRAPH_TYPES,
    build_candidate_eligibility,
)
from src.rec.scorer import ScoredProduct, Scorer
from src.rec.user_similarity import (
    CollaborativeProductSignal,
    attach_collaborative_signals,
    build_collaborative_signals,
    jaccard_similarity,
    owned_product_ids,
    preference_signature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _product(pid="P_CAND", **overrides):
    base = {
        "product_id": pid,
        "brand_id": "brand_cand",
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
        "source_review_count_6m": 0,
        "source_avg_rating_6m": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# user_similarity — metric + signature
# ---------------------------------------------------------------------------

def test_jaccard_similarity_basic():
    assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard_similarity({"a", "b", "c"}, {"a"}) == 1 / 3
    assert jaccard_similarity(set(), {"a"}) == 0.0
    assert jaccard_similarity({"a"}, {"b"}) == 0.0


def test_preference_signature_is_axis_namespaced():
    # Same raw id "x" in two different axes must NOT collapse into one token.
    prof = _user(concern_ids=["x"], goal_ids=["x"])
    sig = preference_signature(prof)
    assert "concern_ids::x" in sig
    assert "goal_ids::x" in sig
    assert len(sig) == 2


def test_owned_product_ids_strips_iri_prefix():
    prof = _user(owned_product_ids=["product:P1", "P2"])
    assert owned_product_ids(prof) == {"P1", "P2"}


# ---------------------------------------------------------------------------
# user_similarity — collaborative signal derivation
# ---------------------------------------------------------------------------

def _sim_user(uid, concerns, owned=None):
    return _user(
        user_id=uid,
        concern_ids=list(concerns),
        owned_product_ids=list(owned or []),
    )


def test_similar_neighbor_products_become_collaborative_signal():
    # u1 & u2 share 3 concerns (>= min_common). u2 owns P_X. u1 does not.
    users = [
        _sim_user("u1", ["c1", "c2", "c3"]),
        _sim_user("u2", ["c1", "c2", "c3"], owned=["product:P_X"]),
    ]
    signals = build_collaborative_signals(users, min_common_prefs=3)

    assert "u1" in signals
    sig = signals["u1"]
    assert [s.product_id for s in sig] == ["P_X"]
    assert sig[0].supporter_count == 1
    assert sig[0].max_similarity == 1.0
    assert 0.0 < sig[0].strength <= 1.0
    # u2 has no neighbour who owns a product u2 lacks → no signal for u2.
    assert "u2" not in signals


def test_user_never_recommended_their_own_owned_product():
    users = [
        _sim_user("u1", ["c1", "c2", "c3"], owned=["P_X"]),
        _sim_user("u2", ["c1", "c2", "c3"], owned=["P_X", "P_Y"]),
    ]
    signals = build_collaborative_signals(users, min_common_prefs=3)
    # u1 already owns P_X → only P_Y is a novel collaborative candidate.
    assert [s.product_id for s in signals["u1"]] == ["P_Y"]


def test_below_min_common_threshold_produces_no_signal():
    # Only 2 shared concerns; threshold is 3.
    users = [
        _sim_user("u1", ["c1", "c2"]),
        _sim_user("u2", ["c1", "c2", "c9"], owned=["P_X"]),
    ]
    assert build_collaborative_signals(users, min_common_prefs=3) == {}


def test_strength_accumulates_across_supporters():
    # Two distinct neighbours both own P_X → higher supporter_count + strength
    # than a single supporter.
    users = [
        _sim_user("u1", ["c1", "c2", "c3"]),
        _sim_user("u2", ["c1", "c2", "c3"], owned=["P_X"]),
        _sim_user("u3", ["c1", "c2", "c3"], owned=["P_X"]),
    ]
    signals = build_collaborative_signals(users, min_common_prefs=3)
    p_x = signals["u1"][0]
    assert p_x.product_id == "P_X"
    assert p_x.supporter_count == 2
    assert p_x.strength > 0.5  # two full-similarity supporters accumulate


def test_empty_and_prefless_users_are_absent():
    users = [_sim_user("u1", []), _sim_user("u2", ["c1", "c2", "c3"], owned=["P_X"])]
    signals = build_collaborative_signals(users, min_common_prefs=3)
    assert "u1" not in signals  # no preferences → no signature → no neighbours


def test_attach_populates_field_in_place_including_empty():
    users = [
        _sim_user("u1", ["c1", "c2", "c3"]),
        _sim_user("u2", ["c1", "c2", "c3"], owned=["P_X"]),
        _sim_user("u3", []),
    ]
    attach_collaborative_signals(users, min_common_prefs=3)
    by_id = {u["user_id"]: u for u in users}
    assert by_id["u1"]["collaborative_product_ids"][0]["id"] == "P_X"
    # Users with no signal still get an explicit empty list (dormant, not missing).
    assert by_id["u3"]["collaborative_product_ids"] == []


def test_signal_to_dict_shape():
    sig = CollaborativeProductSignal("P1", supporter_count=2, max_similarity=0.5, strength=0.75)
    assert sig.to_dict() == {
        "id": "P1", "supporter_count": 2, "max_similarity": 0.5, "strength": 0.75,
    }


# ---------------------------------------------------------------------------
# evidence index — boost-only + never-admissible contract
# ---------------------------------------------------------------------------

def test_collab_is_boost_only_and_never_review_graph():
    assert "collab" in BOOST_ONLY_TYPES
    assert "collab" not in REVIEW_GRAPH_TYPES
    # collab is deliberately NOT admissible; comparison is.
    assert "collab" not in BOOST_ONLY_ADMISSIBLE_TYPES
    assert "comparison" in BOOST_ONLY_ADMISSIBLE_TYPES


def test_collab_alone_never_buys_eligibility_even_when_boost_qualifies():
    default = build_candidate_eligibility(["collab:2|strength=0.4"])
    assert default.eligible is False
    assert default.boost_only_paths == ["collab:2|strength=0.4"]
    assert default.evidence_families == []
    assert "NO_USER_ALIGNED_EVIDENCE" in default.rejection_reasons

    # Unlike comparison, collab stays ineligible even with boost_only_qualifies.
    admitted = build_candidate_eligibility(
        ["collab:2|strength=0.4"], boost_only_qualifies=True,
    )
    assert admitted.eligible is False
    assert admitted.boost_only_paths == ["collab:2|strength=0.4"]
    assert admitted.evidence_families == []


def test_comparison_admission_unbroken_by_collab_refinement():
    # Regression: the admissible-subset change must NOT alter comparison, which
    # is still admitted under boost_only_qualifies (COMPARE).
    assert build_candidate_eligibility(["comparison:P_OWNED"]).eligible is False
    admitted = build_candidate_eligibility(
        ["comparison:P_OWNED"], boost_only_qualifies=True,
    )
    assert admitted.eligible is True
    assert admitted.evidence_families == []


def test_collab_reported_alongside_real_evidence():
    elig = build_candidate_eligibility(["brand:b1", "collab:3|strength=0.6"])
    assert elig.eligible is True
    assert elig.master_truth_paths == ["brand:b1"]
    assert elig.boost_only_paths == ["collab:3|strength=0.6"]
    assert elig.evidence_families == ["PRODUCT_MASTER_TRUTH"]


# ---------------------------------------------------------------------------
# candidate_generator — collab overlap + mandatory solo-eligibility fail
# ---------------------------------------------------------------------------

def _collab_field(pid, supporter_count=2, strength=0.5):
    return [{"id": pid, "supporter_count": supporter_count, "max_similarity": strength, "strength": strength}]

def test_collab_overlap_generated_and_boosts_eligible_candidate():
    # Candidate is eligible via brand master truth; collab rides on top.
    user = _user(
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand", "weight": 1.0}],
        collaborative_product_ids=_collab_field("P_CAND", supporter_count=3, strength=0.6),
    )
    products = [_product(brand_concept_ids=["concept:Brand:brand_cand"])]

    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)

    assert len(candidates) == 1
    overlaps = candidates[0].overlap_concepts
    assert "collab:3|strength=0.6" in overlaps
    elig = candidates[0].eligibility
    assert elig.eligible is True
    assert elig.master_truth_paths  # brand carries eligibility
    assert elig.boost_only_paths == ["collab:3|strength=0.6"]
    assert elig.evidence_families == ["PRODUCT_MASTER_TRUTH"]


def test_collab_only_candidate_is_not_eligible_in_every_mode():
    # The mandatory contract: collaborative affinity alone never qualifies.
    for mode in (RecommendationMode.STRICT, RecommendationMode.EXPLORE, RecommendationMode.COMPARE):
        user = _user(collaborative_product_ids=_collab_field("P_CAND"))
        products = [_product()]
        candidates = generate_candidates(user, products, mode=mode)
        assert candidates == [], f"collab-only candidate leaked in mode={mode}"


def test_no_collab_overlap_when_field_absent():
    # Dormant by default: without the field, no collab overlap is generated.
    user = _user(preferred_brand_ids=[{"id": "concept:Brand:brand_cand"}])
    products = [_product(brand_concept_ids=["concept:Brand:brand_cand"])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert candidates
    assert not any(c.startswith("collab:") for c in candidates[0].overlap_concepts)


def test_collab_not_applied_to_owned_product():
    # An owned product is deprioritized; collab must not tag it.
    user = _user(
        owned_product_ids=["P_CAND"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand"}],
        collaborative_product_ids=_collab_field("P_CAND"),
    )
    products = [_product(brand_concept_ids=["concept:Brand:brand_cand"])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    if candidates:  # EXPLORE keeps owned but deprioritized
        assert not any(c.startswith("collab:") for c in candidates[0].overlap_concepts)


# ---------------------------------------------------------------------------
# scorer — collaborative_affinity scoring (all modes, dormant by default)
# ---------------------------------------------------------------------------

def test_collaborative_affinity_scores_in_every_mode():
    scorer = Scorer()
    scorer.load_config()  # loads collaborative_affinity_weight
    for mode in (RecommendationMode.STRICT, RecommendationMode.EXPLORE, RecommendationMode.COMPARE):
        base = scorer.score(_user(), _product(), [], mode=mode)
        with_collab = scorer.score(_user(), _product(), ["collab:2|strength=0.5"], mode=mode)
        assert with_collab.feature_contributions["collaborative_affinity"] > 0
        assert with_collab.raw_score > base.raw_score
        # Lands in the review_graph score layer (grouped with comparison).
        assert with_collab.score_layers["review_graph_score"] > 0


def test_collaborative_affinity_zero_without_overlap():
    scorer = Scorer()
    scorer.load_config()
    base = scorer.score(_user(), _product(), [])
    same = scorer.score(_user(), _product(), ["keyword:kw1"])  # unrelated overlap
    assert "collaborative_affinity" not in base.feature_contributions
    assert "collaborative_affinity" not in same.feature_contributions


def test_collaborative_affinity_zero_without_config():
    # load_from_dict callers have no collaborative weight → never scores.
    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 0.5})
    s = scorer.score(_user(), _product(), ["collab:2|strength=0.9"])
    assert "collaborative_affinity" not in s.feature_contributions
    assert s.raw_score == 0.0


def test_collaborative_affinity_scales_with_strength():
    scorer = Scorer()
    scorer.load_config()
    weak = scorer.score(_user(), _product(), ["collab:1|strength=0.2"])
    strong = scorer.score(_user(), _product(), ["collab:5|strength=0.9"])
    assert (
        strong.feature_contributions["collaborative_affinity"]
        > weak.feature_contributions["collaborative_affinity"]
    )


# ---------------------------------------------------------------------------
# explainer
# ---------------------------------------------------------------------------

def test_collab_explanation_mentions_similar_customers():
    scored = ScoredProduct(
        product_id="P_CAND", raw_score=0.02, shrinked_score=0.01, final_score=0.01,
        feature_contributions={"collaborative_affinity": 0.012},
    )
    result = explain(scored, ["collab:3|strength=0.6"])
    assert result.paths
    assert result.paths[0].concept_type == "collab"
    assert "취향이 비슷한 고객" in result.summary_ko


# ---------------------------------------------------------------------------
# End-to-end: similarity → attach → candidate → score fires the boost
# ---------------------------------------------------------------------------

def test_end_to_end_collaborative_boost():
    # u1 and u2 share taste; u2 owns P_CAND; the candidate is independently
    # eligible for u1 via brand, and the collaborative boost raises its score.
    brand_pref = [{"id": "concept:Brand:brand_cand", "weight": 1.0}]
    users = [
        _user(user_id="u1", concern_ids=["c1", "c2", "c3"], preferred_brand_ids=brand_pref),
        _user(user_id="u2", concern_ids=["c1", "c2", "c3"], owned_product_ids=["product:P_CAND"]),
    ]
    attach_collaborative_signals(users, min_common_prefs=3)
    u1 = next(u for u in users if u["user_id"] == "u1")
    assert u1["collaborative_product_ids"], "u1 should inherit u2's product as a collab signal"

    products = [_product("P_CAND", brand_concept_ids=["concept:Brand:brand_cand"])]
    candidates = generate_candidates(u1, products, mode=RecommendationMode.EXPLORE)
    assert candidates
    overlaps = candidates[0].overlap_concepts
    assert any(c.startswith("collab:") for c in overlaps)

    scorer = Scorer()
    scorer.load_config()
    with_collab = scorer.score(u1, products[0], overlaps)
    without_collab = scorer.score(u1, products[0], [c for c in overlaps if not c.startswith("collab:")])
    assert with_collab.raw_score > without_collab.raw_score
