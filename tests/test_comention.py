"""Phase 7 P7-4 D2 — product-product co-mention → co-mention affinity signal.

Covers the full wiring of the review-native product-product connectivity signal:
  * product_comention: review→products membership (ghost + negative-polarity
    exclusion, real-id filter), pair support gate, strength, symmetry.
  * recommendation_evidence_index: `comention` is boost-only and — like
    `collab` — is NEVER admissible (cannot qualify a candidate in ANY mode).
  * candidate_generator: comention overlap generation + the mandatory
    "comention alone is not eligible" contract across STRICT/EXPLORE/COMPARE.
  * scorer: comention_product_bonus is scored in every mode, is zero without a
    comention overlap (default path byte-identical), and lands in
    review_graph_score.
  * explainer: comention path + "리뷰에서 함께 언급" summary.
  * density guard: on the real fixture, no review co-mentions two distinct real
    products (the measured "wired-but-waiting" state).

The default (no upstream comention wiring) recommendation path must stay
byte-identical to pre-D2; the dense/wide ranking snapshots enforce that
separately. Here we prove the co-mention behaviour actually fires when a caller
populates the signal.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from src.common.enums import RecommendationMode
from src.rec.candidate_generator import generate_candidates
from src.rec.explainer import explain
from src.rec.recommendation_evidence_index import (
    BOOST_ONLY_ADMISSIBLE_TYPES,
    BOOST_ONLY_TYPES,
    build_candidate_eligibility,
)
from src.rec.scorer import ScoredProduct, Scorer
from src.mart.product_comention import (
    ComentionProductSignal,
    attach_comention_signals,
    build_comention_signals,
    review_products_from_signals,
)


ROOT = Path(__file__).resolve().parents[1]


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


def _comention_field(pid, support=2, strength=0.5):
    return [{"id": pid, "support": support, "strength": strength}]


def _sig(review_id, target, *, dst_type="", dst_id="", polarity="POS"):
    return {
        "review_id": review_id,
        "target_product_id": target,
        "dst_type": dst_type,
        "dst_id": dst_id,
        "polarity": polarity,
    }


# ---------------------------------------------------------------------------
# review_products_from_signals — membership extraction
# ---------------------------------------------------------------------------

def test_membership_collects_target_and_product_dst():
    signals = [
        _sig("r1", "product:A", dst_type="Product", dst_id="product:B"),
    ]
    rp = review_products_from_signals(signals, real_product_ids={"A", "B"})
    assert rp["r1"] == {"A", "B"}


def test_membership_drops_ghost_products():
    # An unresolved product mention never seeds co-mention.
    signals = [
        _sig("r1", "product:A", dst_type="Product", dst_id="concept:Product:다른라인"),
    ]
    rp = review_products_from_signals(signals, real_product_ids={"A"})
    assert rp["r1"] == {"A"}


def test_membership_excludes_negative_polarity_product():
    # A disparaging comparison (NEG) must not count as relatedness.
    signals = [
        _sig("r1", "A", dst_type="Product", dst_id="B", polarity="NEG"),
    ]
    rp = review_products_from_signals(signals, real_product_ids={"A", "B"})
    assert rp["r1"] == {"A"}  # B dropped
    # ...but allowed when exclude_negative is off.
    rp2 = review_products_from_signals(
        signals, real_product_ids={"A", "B"}, exclude_negative=False,
    )
    assert rp2["r1"] == {"A", "B"}


def test_membership_honours_real_id_filter():
    signals = [_sig("r1", "A", dst_type="Product", dst_id="B")]
    rp = review_products_from_signals(signals, real_product_ids={"A"})
    assert rp["r1"] == {"A"}  # B not a real catalog product


# ---------------------------------------------------------------------------
# build_comention_signals — pairing, support gate, symmetry, strength
# ---------------------------------------------------------------------------

def test_comention_pair_below_support_gate_is_dropped():
    # Single shared review = noise, dropped by the default min_support=2.
    review_products = {"r1": {"A", "B"}}
    assert build_comention_signals(review_products) == {}


def test_comention_pair_meets_support_gate_is_symmetric():
    review_products = {"r1": {"A", "B"}, "r2": {"A", "B"}}
    result = build_comention_signals(review_products)
    assert result["A"][0].product_id == "B"
    assert result["B"][0].product_id == "A"
    assert result["A"][0].support == 2


def test_comention_strength_scales_with_support():
    weak = build_comention_signals({"r1": {"A", "B"}, "r2": {"A", "B"}})
    strong = build_comention_signals(
        {f"r{i}": {"A", "B"} for i in range(6)}
    )
    assert strong["A"][0].strength > weak["A"][0].strength
    assert strong["A"][0].strength == 1.0  # saturates


def test_comention_top_n_truncation_and_ordering():
    reviews = {}
    # A co-mentioned with B (support 4) and C (support 2)
    for i in range(4):
        reviews[f"ab{i}"] = {"A", "B"}
    for i in range(2):
        reviews[f"ac{i}"] = {"A", "C"}
    result = build_comention_signals(reviews, top_n=1)
    assert len(result["A"]) == 1
    assert result["A"][0].product_id == "B"  # stronger first


def test_build_comention_rejects_bad_params():
    with pytest.raises(ValueError):
        build_comention_signals({}, min_support=0)
    with pytest.raises(ValueError):
        build_comention_signals({}, top_n=0)


# ---------------------------------------------------------------------------
# attach_comention_signals — wiring point + dormancy
# ---------------------------------------------------------------------------

def test_attach_populates_field_only_for_real_products():
    products = [_product("A"), _product("B")]
    signals = [
        _sig("r1", "A", dst_type="Product", dst_id="B"),
        _sig("r2", "A", dst_type="Product", dst_id="B"),
    ]
    attach_comention_signals(products, signals)
    by_id = {p["product_id"]: p for p in products}
    assert by_id["A"]["comention_product_ids"][0]["id"] == "B"
    assert by_id["B"]["comention_product_ids"][0]["id"] == "A"


def test_attach_empty_when_no_qualifying_pair():
    products = [_product("A"), _product("B")]
    signals = [_sig("r1", "A", dst_type="Product", dst_id="B")]  # support 1
    attach_comention_signals(products, signals)
    assert all(p["comention_product_ids"] == [] for p in products)


# ---------------------------------------------------------------------------
# recommendation_evidence_index — boost-only + non-admissible contract
# ---------------------------------------------------------------------------

def test_comention_is_boost_only_and_never_admissible():
    assert "comention" in BOOST_ONLY_TYPES
    assert "comention" not in BOOST_ONLY_ADMISSIBLE_TYPES


def test_comention_only_candidate_never_eligible():
    for boost_only_qualifies in (False, True):
        elig = build_candidate_eligibility(
            ["comention:P_OWNED|strength=0.6"],
            boost_only_qualifies=boost_only_qualifies,
        )
        assert elig.eligible is False
        assert elig.boost_only_paths == ["comention:P_OWNED|strength=0.6"]
        assert elig.evidence_families == []


def test_comention_reported_alongside_real_evidence():
    elig = build_candidate_eligibility(["brand:b1", "comention:P_OWNED|strength=0.6"])
    assert elig.eligible is True
    assert elig.master_truth_paths == ["brand:b1"]
    assert elig.boost_only_paths == ["comention:P_OWNED|strength=0.6"]
    assert elig.evidence_families == ["PRODUCT_MASTER_TRUTH"]


# ---------------------------------------------------------------------------
# candidate_generator — comention overlap + mandatory solo-eligibility fail
# ---------------------------------------------------------------------------

def test_comention_overlap_generated_and_boosts_eligible_candidate():
    # Candidate is eligible via brand master truth; comention rides on top
    # because it is co-mentioned with a product the user owns.
    user = _user(
        owned_product_ids=["product:P_OWNED"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand", "weight": 1.0}],
    )
    products = [_product(
        brand_concept_ids=["concept:Brand:brand_cand"],
        comention_product_ids=_comention_field("P_OWNED", support=3, strength=0.6),
    )]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert len(candidates) == 1
    overlaps = candidates[0].overlap_concepts
    assert "comention:P_OWNED|strength=0.6" in overlaps
    elig = candidates[0].eligibility
    assert elig.eligible is True
    assert elig.master_truth_paths  # brand carries eligibility
    assert elig.boost_only_paths == ["comention:P_OWNED|strength=0.6"]


def test_comention_only_candidate_is_not_eligible_in_every_mode():
    for mode in (RecommendationMode.STRICT, RecommendationMode.EXPLORE, RecommendationMode.COMPARE):
        user = _user(owned_product_ids=["P_OWNED"])
        products = [_product(comention_product_ids=_comention_field("P_OWNED"))]
        candidates = generate_candidates(user, products, mode=mode)
        assert candidates == [], f"comention-only candidate leaked in mode={mode}"


def test_no_comention_overlap_when_field_absent():
    # Dormant by default: without the field, no comention overlap is generated.
    user = _user(
        owned_product_ids=["P_OWNED"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand"}],
    )
    products = [_product(brand_concept_ids=["concept:Brand:brand_cand"])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert candidates
    assert not any(c.startswith("comention:") for c in candidates[0].overlap_concepts)


def test_comention_requires_owned_anchor():
    # A co-mention neighbour the user does NOT own produces no overlap.
    user = _user(
        owned_product_ids=["P_OTHER"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand"}],
    )
    products = [_product(
        brand_concept_ids=["concept:Brand:brand_cand"],
        comention_product_ids=_comention_field("P_OWNED"),
    )]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    assert candidates
    assert not any(c.startswith("comention:") for c in candidates[0].overlap_concepts)


def test_comention_not_applied_to_owned_product():
    user = _user(
        owned_product_ids=["P_CAND", "P_OWNED"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand"}],
    )
    products = [_product(
        brand_concept_ids=["concept:Brand:brand_cand"],
        comention_product_ids=_comention_field("P_OWNED"),
    )]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    if candidates:  # EXPLORE keeps owned but deprioritized
        assert not any(c.startswith("comention:") for c in candidates[0].overlap_concepts)


# ---------------------------------------------------------------------------
# scorer — comention_product_bonus scoring (all modes, dormant by default)
# ---------------------------------------------------------------------------

def test_comention_scores_in_every_mode():
    scorer = Scorer()
    scorer.load_config()  # loads comention_product_weight
    for mode in (RecommendationMode.STRICT, RecommendationMode.EXPLORE, RecommendationMode.COMPARE):
        base = scorer.score(_user(), _product(), [], mode=mode)
        with_co = scorer.score(_user(), _product(), ["comention:P_OWNED|strength=0.5"], mode=mode)
        assert with_co.feature_contributions["comention_product_bonus"] > 0
        assert with_co.raw_score > base.raw_score
        assert with_co.score_layers["review_graph_score"] > 0


def test_comention_zero_without_overlap():
    scorer = Scorer()
    scorer.load_config()
    base = scorer.score(_user(), _product(), [])
    other = scorer.score(_user(), _product(), ["keyword:kw1"])
    assert "comention_product_bonus" not in base.feature_contributions
    assert "comention_product_bonus" not in other.feature_contributions


def test_comention_zero_without_config():
    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 0.5})
    s = scorer.score(_user(), _product(), ["comention:P_OWNED|strength=0.9"])
    assert "comention_product_bonus" not in s.feature_contributions
    assert s.raw_score == 0.0


def test_comention_scales_with_strength():
    scorer = Scorer()
    scorer.load_config()
    weak = scorer.score(_user(), _product(), ["comention:P1|strength=0.2"])
    strong = scorer.score(_user(), _product(), ["comention:P1|strength=0.9"])
    assert (
        strong.feature_contributions["comention_product_bonus"]
        > weak.feature_contributions["comention_product_bonus"]
    )


# ---------------------------------------------------------------------------
# explainer
# ---------------------------------------------------------------------------

def test_comention_explanation_mentions_co_mention():
    scored = ScoredProduct(
        product_id="P_CAND", raw_score=0.02, shrinked_score=0.01, final_score=0.01,
        feature_contributions={"comention_product_bonus": 0.01},
    )
    result = explain(scored, ["comention:P_OWNED|strength=0.6"])
    assert result.paths
    assert result.paths[0].concept_type == "comention"
    assert result.paths[0].concept_id == "P_OWNED"
    assert "함께 언급" in result.summary_ko


# ---------------------------------------------------------------------------
# End-to-end: signals → attach → candidate → score fires the boost
# ---------------------------------------------------------------------------

def test_end_to_end_comention_boost():
    # A and B are co-mentioned across two reviews; the user owns A; candidate B
    # is independently eligible via brand, and the co-mention boost lifts it.
    signals = [
        _sig("r1", "A", dst_type="Product", dst_id="B"),
        _sig("r2", "A", dst_type="Product", dst_id="B"),
    ]
    product_b = _product("B", brand_concept_ids=["concept:Brand:brand_cand"])
    attach_comention_signals([_product("A"), product_b], signals)
    assert product_b["comention_product_ids"], "B should carry A as a co-mention neighbour"

    user = _user(
        owned_product_ids=["product:A"],
        preferred_brand_ids=[{"id": "concept:Brand:brand_cand", "weight": 1.0}],
    )
    candidates = generate_candidates(user, [product_b], mode=RecommendationMode.EXPLORE)
    assert candidates
    overlaps = candidates[0].overlap_concepts
    assert any(c.startswith("comention:A") for c in overlaps)

    scorer = Scorer()
    scorer.load_config()
    scored = scorer.score(user, product_b, overlaps)
    assert scored.feature_contributions.get("comention_product_bonus", 0) > 0


# ---------------------------------------------------------------------------
# Density guard: the measured "wired-but-waiting" state on the real fixture
# ---------------------------------------------------------------------------

def test_real_fixture_has_no_real_real_comention():
    """Locks the D2 density finding: in the review-only dense_golden fixture no
    review co-mentions two distinct real (catalog-linked) products, so the
    co-mention signal is wired-but-dormant until real multi-product review data
    arrives. If this ever fails, real co-mention data has appeared and D2 can be
    promoted from "waiting" to "active" (re-review the ranking snapshots)."""
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    fdir = ROOT / "mockdata" / "dense_golden"
    products = json.loads((fdir / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = json.loads((fdir / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(fdir / "review_triples_raw.json"),
            product_es_records=products,
            user_profiles=users,
            kg_mode="on",
        ))
    signals = [
        s
        for bundle in result.batch_result["all_bundles"]
        for s in (_sig_dict(sig) for sig in bundle.wrapped_signals)
    ]
    real_ids = {str(pid) for pid in result.batch_result["product_masters"]}
    review_products = review_products_from_signals(signals, real_product_ids=real_ids)
    comention = build_comention_signals(review_products)
    assert comention == {}, f"unexpected real co-mention pairs: {comention}"


def _sig_dict(sig):
    return {
        "review_id": sig.review_id,
        "target_product_id": sig.target_product_id,
        "dst_type": sig.dst_type,
        "dst_id": sig.dst_id,
        "polarity": sig.polarity,
    }


def test_comention_signal_to_dict_shape():
    s = ComentionProductSignal("B", support=3, strength=0.6)
    assert s.to_dict() == {"id": "B", "support": 3, "strength": 0.6}
