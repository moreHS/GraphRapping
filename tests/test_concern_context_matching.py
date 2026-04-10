"""Tests: concern/goal matching across user↔product via concept resolver + bridge."""
from src.rec.candidate_generator import generate_candidates
from src.rec.scorer import Scorer
from src.rec.concern_bridge import compute_bridged_concerns
from src.common.enums import RecommendationMode


def _user(**overrides):
    base = {
        "user_id": "u1", "skin_type": "건성",
        "owned_product_ids": [], "owned_family_ids": [], "repurchased_family_ids": [],
        "preferred_brand_ids": [], "preferred_category_ids": [],
        "preferred_ingredient_ids": [], "avoided_ingredient_ids": [],
        "concern_ids": [], "goal_ids": [],
        "preferred_bee_attr_ids": [], "preferred_keyword_ids": [],
        "preferred_context_ids": [],
        "recent_purchase_brand_ids": [], "repurchase_brand_ids": [],
    }
    base.update(overrides)
    return base


def _product(pid, **overrides):
    base = {
        "product_id": pid, "brand_id": "b1", "brand_name": "B",
        "category_id": "c1", "category_name": "C", "variant_family_id": None,
        "price": 10000,
        "ingredient_concept_ids": [], "category_concept_ids": [],
        "brand_concept_ids": [], "main_benefit_concept_ids": [],
        "top_bee_attr_ids": [], "top_keyword_ids": [],
        "top_context_ids": [], "top_concern_pos_ids": [], "top_concern_neg_ids": [],
        "top_tool_ids": [], "top_comparison_product_ids": [], "top_coused_product_ids": [],
        "review_count_30d": 5, "review_count_90d": 20, "review_count_all": 50,
        "last_signal_at": None,
    }
    base.update(overrides)
    return base


# --- Concern matching ---

def test_concern_overlap_with_resolver():
    """User concern '건조함' should match product concern 'concern_dryness' via resolver."""
    user = _user(concern_ids=[{"id": "concept:Concern:건조함", "weight": 0.8}])
    products = [_product("P1", top_concern_pos_ids=[
        {"id": "concern_dryness", "score": 0.8, "review_cnt": 10}
    ])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    concern_overlaps = [c for c in p1.overlap_concepts if c.startswith("concern:")]
    assert len(concern_overlaps) > 0, f"Expected concern overlap, got: {p1.overlap_concepts}"


# --- Goal matching ---

def test_goal_overlap_with_alias():
    """User goal '보습강화' should match product benefit '보습' via alias map."""
    user = _user(goal_ids=[{"id": "concept:Goal:보습강화", "weight": 0.8}])
    products = [_product("P1", main_benefit_concept_ids=["concept:Goal:보습"])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    goal_overlaps = [c for c in p1.overlap_concepts if c.startswith("goal_master:")]
    assert len(goal_overlaps) > 0, f"Expected goal overlap, got: {p1.overlap_concepts}"


def test_goal_antiaging_alias():
    """User goal '안티에이징' should match product benefit '주름개선'."""
    user = _user(goal_ids=[{"id": "concept:Goal:안티에이징", "weight": 0.8}])
    products = [_product("P1", main_benefit_concept_ids=["concept:Goal:주름개선"])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    goal_overlaps = [c for c in p1.overlap_concepts if c.startswith("goal_master:")]
    assert len(goal_overlaps) > 0


# --- BEE_ATTR → Concern bridge ---

def test_concern_bridge_from_bee_attr():
    """Product with 보습력 BEE_ATTR should bridge to user concern 건조함."""
    user = _user(concern_ids=[{"id": "concept:Concern:건조함", "weight": 0.8}])
    products = [_product("P1", top_bee_attr_ids=[
        {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 30}
    ])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    bridge_overlaps = [c for c in p1.overlap_concepts if c.startswith("concern_bridge:")]
    assert len(bridge_overlaps) > 0, f"Expected concern_bridge overlap, got: {p1.overlap_concepts}"


def test_explicit_concern_beats_bridge():
    """When explicit concern matches, bridge should be suppressed."""
    user = _user(concern_ids=[{"id": "concept:Concern:건조함", "weight": 0.8}])
    products = [_product("P1",
        top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.8, "review_cnt": 10}],
        top_bee_attr_ids=[{"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 30}],
    )]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    explicit = [c for c in p1.overlap_concepts if c.startswith("concern:") and not c.startswith("concern_bridge:")]
    bridge = [c for c in p1.overlap_concepts if c.startswith("concern_bridge:")]
    assert len(explicit) > 0, "Explicit concern should match"
    assert len(bridge) == 0, "Bridge should be suppressed when explicit exists"


def test_no_bridge_for_unmapped_attr():
    """BEE_ATTR without bridge mapping should not produce concern_bridge."""
    user = _user(concern_ids=[{"id": "concept:Concern:건조함", "weight": 0.8}])
    products = [_product("P1", top_bee_attr_ids=[
        {"id": "concept:BEEAttr:bee_attr_scent", "score": 0.9, "review_cnt": 30}  # 향 — no bridge
    ])]
    candidates = generate_candidates(user, products, mode=RecommendationMode.EXPLORE)
    p1 = next(c for c in candidates if c.product_id == "P1")
    bridge = [c for c in p1.overlap_concepts if c.startswith("concern_bridge:")]
    assert len(bridge) == 0


def test_bridge_scorer_feature():
    """concern_bridge_fit should appear in scorer feature contributions."""
    scorer = Scorer()
    scorer.load_from_dict({"concern_bridge_fit": 1.0})
    user = _user(concern_ids=[{"id": "건조함", "weight": 0.8}])
    product = _product("P1")
    overlaps = ["concern_bridge:concern_dryness"]
    result = scorer.score(user, product, overlaps)
    assert result.feature_contributions.get("concern_bridge_fit", 0) > 0


def test_compute_bridged_concerns():
    """Direct test of bridge computation."""
    attrs = [
        {"id": "concept:BEEAttr:bee_attr_moisturizing_power", "score": 0.9, "review_cnt": 30},
        {"id": "concept:BEEAttr:bee_attr_scent", "score": 0.8, "review_cnt": 20},  # no mapping
    ]
    result = compute_bridged_concerns(attrs)
    assert "concern_dryness" in result
    assert result["concern_dryness"]["score"] > 0
    assert "concern_oiliness" not in result  # no mapping for scent
