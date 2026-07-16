"""
Scorer: linear scoring + evidence shrinkage + brand confidence-weight.

Score = residual_bee_attr + keyword + context + concern + ingredient + brand + goal + category + freshness
Shrinkage: score * (support / (support + k))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.common.config_loader import load_yaml
from src.common.enums import RecommendationMode


SCORING_FEATURE_KEYS = (
    "keyword_match",
    "residual_bee_attr_match",
    "review_graph_weak_relation_match",
    "context_match",
    "catalog_keyword_match",
    "concern_fit",
    "concern_bridge_fit",
    "ingredient_match",
    "brand_match_conf_weighted",
    "goal_fit_master",
    "category_affinity",
    "active_category_affinity",
    "freshness_boost",
    "source_popularity_score",
    "source_rating_score",
    "skin_type_fit",
    "purchase_loyalty_score",
    "novelty_bonus",
    "exact_owned_penalty",
    "owned_family_penalty",
    "same_family_explore_bonus",
    "repurchase_family_affinity",
    "repurchase_category_affinity",
    "tool_alignment",
    "coused_product_bonus",
)


@dataclass
class ScoredProduct:
    product_id: str
    raw_score: float
    shrinked_score: float
    final_score: float
    feature_contributions: dict[str, float]
    support_count: int = 0
    score_layers: dict[str, float] = field(default_factory=dict)


class Scorer:
    """Scores candidate products against user preferences."""

    def __init__(self) -> None:
        self._weights: dict[str, float] = {}
        self._shrinkage_k: float = 10.0
        self._brand_confidence: dict[str, float] = {}
        # Mode-scoped scoring config (e.g. modes.compare.comparison_neighbor).
        # Populated by load_config; load_from_dict leaves it empty so weight-only
        # callers get no mode-scoped scoring (the comparison weight stays 0).
        self._mode_config: dict[str, Any] = {}
        # Collaborative-affinity (Phase 7 D1) weight. Kept OUT of the `features`
        # contract map (which is mirrored byte-for-byte by the frontend
        # DEFAULT_WEIGHTS slider set), so it is a non-tunable backend boost.
        # Applied in ALL modes (unlike comparison, which is mode-scoped), but
        # conservative and dormant until user_similarity populates collab
        # overlaps. load_config sets it; load_from_dict leaves it (fresh scorers
        # default to 0.0), mirroring _mode_config.
        self._collaborative_affinity_weight: float = 0.0
        # Co-mention product boost (Phase 7 D2). Same discipline as the
        # collaborative weight: kept OUT of the `features` map (non-tunable
        # backend boost), applied in ALL modes, and dormant until
        # src/mart/product_comention populates comention overlaps.
        self._comention_product_weight: float = 0.0
        # Similar-product boost (Phase 8 G4). Same discipline again: kept OUT of
        # the `features` map (non-tunable backend boost), applied in ALL modes,
        # and dormant until a caller assembles `similar_boost` from the ungated
        # similarity sidecar (candidate_generator.build_similar_boost_index).
        # load_config sets it; load_from_dict (manual weight sliders) leaves it
        # at 0.0 — the D1/D2 semantics, pinned by tests.
        self._similar_product_weight: float = 0.0

    def load_config(self, filename: str = "scoring_weights.yaml") -> None:
        config = load_yaml(filename)
        self._weights = config.get("features", {})
        self._shrinkage_k = config.get("shrinkage_k", 10.0)
        self._brand_confidence = config.get("brand_confidence", {})
        self._mode_config = config.get("modes", {}) or {}
        try:
            self._collaborative_affinity_weight = max(
                0.0, float(config.get("collaborative_affinity_weight", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            self._collaborative_affinity_weight = 0.0
        try:
            self._comention_product_weight = max(
                0.0, float(config.get("comention_product_weight", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            self._comention_product_weight = 0.0
        try:
            self._similar_product_weight = max(
                0.0, float(config.get("similar_product_weight", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            self._similar_product_weight = 0.0

    def load_from_dict(self, weights: dict, shrinkage_k: float = 10.0) -> None:
        self._weights = weights
        self._shrinkage_k = shrinkage_k

    @property
    def weights(self) -> dict[str, float]:
        """Read-only view of the current feature weight mapping.

        P1-3: prefer this over reading the private `_weights` attribute.
        Returns a copy so external code cannot mutate the scorer state.
        """
        return dict(self._weights)

    def score(
        self,
        user_profile: dict[str, Any],
        product_profile: dict[str, Any],
        overlap_concepts: list[str] | None = None,
        brand_source: str = "purchase",
        *,
        mode: RecommendationMode = RecommendationMode.EXPLORE,
    ) -> ScoredProduct:
        """Score a single product against user preferences.

        Uses residual BEE_ATTR scoring to avoid double-counting with keywords.

        ``mode`` gates mode-scoped scoring: the ``comparison_alternative``
        feature is weighted only in COMPARE mode (0 otherwise), so the default
        (STRICT/EXPLORE) scoring path is byte-identical to the pre-mode behavior.
        """
        pid = product_profile["product_id"]
        contributions: dict[str, float] = {}

        # Parse overlap concepts by type
        overlaps_by_type: dict[str, int] = {}
        overlap_strength_by_type: dict[str, float] = {}
        for concept in (overlap_concepts or []):
            ctype, strength = _parse_overlap_concept(concept)
            overlaps_by_type[ctype] = overlaps_by_type.get(ctype, 0) + 1
            overlap_strength_by_type[ctype] = overlap_strength_by_type.get(ctype, 0.0) + strength

        # Feature scoring
        semantic_keyword_strength = overlap_strength_by_type.get("semantic_keyword", 0.0)
        semantic_bee_attr_strength = overlap_strength_by_type.get("semantic_bee_attr", 0.0)
        weak_relation_strength = (
            overlap_strength_by_type.get("weak_semantic_keyword", 0.0)
            + overlap_strength_by_type.get("weak_semantic_bee_attr", 0.0)
        )
        keyword_score_units = overlaps_by_type.get("keyword", 0) + semantic_keyword_strength
        bee_attr_score_units = overlaps_by_type.get("bee_attr", 0) + semantic_bee_attr_strength

        # Residual BEE_ATTR: only count attrs not already covered by keywords
        residual_attr = max(0.0, bee_attr_score_units - keyword_score_units)

        # Goal match uses product truth/main benefit only; concern_bridge covers
        # indirect review evidence without crossing concept planes.
        goal_master_count = overlaps_by_type.get("goal_master", 0) + overlaps_by_type.get("goal", 0)
        category_score_units = overlaps_by_type.get("category", 0)
        active_category_score_units = overlaps_by_type.get("active_category", 0)

        # skin_type_fit: user skin_type × product concern signals
        skin_type_fit_val = _skin_type_fit(user_profile, product_profile)

        # Purchase-derived features
        purchase_loyalty = _purchase_loyalty_score(user_profile, product_profile)
        novelty = _novelty_bonus(user_profile, product_profile)

        features = {
            "keyword_match": min(keyword_score_units / 3.0, 1.0),
            "residual_bee_attr_match": min(residual_attr / 2.0, 1.0),
            "context_match": min(overlaps_by_type.get("context", 0) / 2.0, 1.0),
            "catalog_keyword_match": min(overlaps_by_type.get("catalog_keyword", 0) / 2.0, 1.0),
            "concern_fit": min(overlaps_by_type.get("concern", 0) / 2.0, 1.0),
            "concern_bridge_fit": _concern_bridge_score(overlaps_by_type.get("concern_bridge", 0), product_profile),
            "ingredient_match": min(overlaps_by_type.get("ingredient", 0) / 3.0, 1.0),
            "brand_match_conf_weighted": _brand_score(overlaps_by_type.get("brand", 0), brand_source, self._brand_confidence),
            "goal_fit_master": min(goal_master_count / 2.0, 1.0),
            "category_affinity": min(category_score_units, 1.0),
            "active_category_affinity": min(active_category_score_units * 0.5, 1.0),
            "freshness_boost": _freshness_score(product_profile),
            "source_popularity_score": _source_popularity_score(product_profile),
            "source_rating_score": _source_rating_score(product_profile),
            "skin_type_fit": skin_type_fit_val,
            "purchase_loyalty_score": purchase_loyalty,
            "novelty_bonus": novelty,
            "exact_owned_penalty": _exact_owned_penalty(user_profile, product_profile),
            "owned_family_penalty": _owned_family_penalty(user_profile, product_profile),
            "same_family_explore_bonus": _same_family_explore_bonus(user_profile, product_profile),
            "repurchase_family_affinity": _repurchase_family_affinity(user_profile, product_profile),
            "repurchase_category_affinity": min(overlaps_by_type.get("repurchase_category", 0) / 2.0, 1.0),
            "tool_alignment": min(overlaps_by_type.get("tool", 0) / 2.0, 1.0),
            "coused_product_bonus": min(overlaps_by_type.get("coused", 0) / 2.0, 1.0),
            "review_graph_weak_relation_match": min(weak_relation_strength / 3.0, 1.0),
        }

        raw_score = sum(
            _feature_weight(feature, self._weights) * value
            for feature, value in features.items()
        )

        contributions = {
            k: _feature_weight(k, self._weights) * v
            for k, v in features.items()
            if v != 0
        }

        # comparison_alternative: mode-scoped boost-only feature. Its weight is 0
        # outside COMPARE mode, so it contributes nothing (and stays out of
        # `contributions`/`score_layers`) on the default path — keeping existing
        # snapshots/expected-sets byte-identical. The weight is sourced from
        # modes.compare.comparison_neighbor, not the `features` map, so it is not
        # a user-tunable slider (kept out of the frontend feature contract).
        comparison_value = min(overlaps_by_type.get("comparison", 0) / 2.0, 1.0)
        comparison_weight = self._comparison_weight(mode)
        comparison_contribution = comparison_weight * comparison_value
        if comparison_contribution != 0:
            raw_score += comparison_contribution
            contributions["comparison_alternative"] = comparison_contribution

        # collaborative_affinity: boost-only feature (Phase 7 D1). Its magnitude
        # rides on the `collab:*|strength=` overlap channel, so the value is 0
        # whenever no collaborative overlap is present — keeping the default path
        # (no upstream collab wiring) byte-identical regardless of the weight.
        # Applied in every mode; weight sourced from the top-level
        # collaborative_affinity_weight config key, not the `features` map, so it
        # stays out of the frontend feature contract (like comparison).
        collab_value = min(overlap_strength_by_type.get("collab", 0.0), 1.0)
        collab_contribution = self._collaborative_affinity_weight * collab_value
        if collab_contribution != 0:
            raw_score += collab_contribution
            contributions["collaborative_affinity"] = collab_contribution

        # comention_product_bonus: boost-only feature (Phase 7 D2). Magnitude
        # rides on the `comention:*|strength=` overlap channel, so the value is 0
        # whenever no co-mention overlap is present — keeping the default path
        # (no upstream comention wiring) byte-identical regardless of the weight.
        # Applied in every mode; weight from the top-level comention_product_weight
        # config key, kept OUT of the `features` map (like collaborative_affinity).
        comention_value = min(overlap_strength_by_type.get("comention", 0.0), 1.0)
        comention_contribution = self._comention_product_weight * comention_value
        if comention_contribution != 0:
            raw_score += comention_contribution
            contributions["comention_product_bonus"] = comention_contribution

        # similar_product_affinity: boost-only feature (Phase 8 G4). Magnitude
        # rides on the `similar:*|strength=` overlap channel (per-anchor strength
        # = min(shared-IDF score / 30, 1), summed across anchors then clamped),
        # so the value is 0 whenever no similar overlap is present — keeping the
        # default path (no similar_boost wiring upstream) byte-identical
        # regardless of the weight. Applied in every mode; weight from the
        # top-level similar_product_weight config key, kept OUT of the
        # `features` map (like collaborative_affinity/comention_product_bonus).
        similar_value = min(overlap_strength_by_type.get("similar", 0.0), 1.0)
        similar_contribution = self._similar_product_weight * similar_value
        if similar_contribution != 0:
            raw_score += similar_contribution
            contributions["similar_product_affinity"] = similar_contribution

        score_layers = _score_layers(contributions)

        # Evidence shrinkage
        support_count = product_profile.get("review_count_all", 0) or 0
        shrinkage = support_count / (support_count + self._shrinkage_k) if support_count > 0 else 0.1
        shrinked_score = raw_score * shrinkage

        return ScoredProduct(
            product_id=pid,
            raw_score=round(raw_score, 4),
            shrinked_score=round(shrinked_score, 4),
            final_score=round(shrinked_score, 4),
            feature_contributions=contributions,
            support_count=support_count,
            score_layers=score_layers,
        )

    def _comparison_weight(self, mode: RecommendationMode) -> float:
        """Weight for the comparison_alternative feature.

        Non-zero only in COMPARE mode, sourced from
        ``modes.compare.comparison_neighbor``. Every other mode returns 0.0 so
        comparison never affects the default scoring path. Absent config (e.g.
        load_from_dict callers) also yields 0.0.
        """
        if mode != RecommendationMode.COMPARE:
            return 0.0
        compare_cfg = self._mode_config.get("compare", {}) or {}
        try:
            return max(0.0, float(compare_cfg.get("comparison_neighbor", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0


def _score_layers(contributions: dict[str, float]) -> dict[str, float]:
    groups = {
        "master_truth_score": {
            "brand_match_conf_weighted",
            "category_affinity",
            "catalog_keyword_match",
            "ingredient_match",
            "goal_fit_master",
        },
        "review_graph_score": {
            "keyword_match",
            "residual_bee_attr_match",
            "context_match",
            "concern_fit",
            "concern_bridge_fit",
            "tool_alignment",
            "coused_product_bonus",
            "comparison_alternative",
            "collaborative_affinity",
            "comention_product_bonus",
            "similar_product_affinity",
        },
        "review_graph_weak_evidence_score": {
            "review_graph_weak_relation_match",
        },
        "product_activity_score": {
            "freshness_boost",
        },
        "profile_fit_score": {
            "skin_type_fit",
            "active_category_affinity",
        },
        "purchase_behavior_score": {
            "purchase_loyalty_score",
            "novelty_bonus",
            "exact_owned_penalty",
            "owned_family_penalty",
            "same_family_explore_bonus",
            "repurchase_family_affinity",
            "repurchase_category_affinity",
        },
        "source_trust_score": {
            "source_popularity_score",
            "source_rating_score",
        },
    }
    return {
        layer: round(sum(contributions.get(feature, 0.0) for feature in features), 4)
        for layer, features in groups.items()
    }


def _feature_weight(feature: str, weights: dict[str, float]) -> float:
    return weights.get(feature, 0.0)


def _parse_overlap_concept(concept: str) -> tuple[str, float]:
    ctype = concept.split(":", 1)[0] if ":" in concept else "other"
    strength = 1.0
    if "|strength=" in concept:
        raw_strength = concept.rsplit("|strength=", 1)[1]
        try:
            strength = float(raw_strength)
        except ValueError:
            strength = 1.0
    return ctype, max(0.0, min(strength, 1.0))


def _brand_score(brand_overlap: int, brand_source: str, conf_map: dict) -> float:
    if brand_overlap == 0:
        return 0.0
    conf = conf_map.get(brand_source, 0.5)
    return min(float(conf), 1.0)


def _concern_bridge_score(bridge_count: int, product_profile: dict) -> float:
    """Score concern bridge: uses weighted bridge scores instead of just count."""
    if bridge_count == 0:
        return 0.0
    from src.rec.concern_bridge import compute_bridged_concerns
    bridged = compute_bridged_concerns(product_profile.get("top_bee_attr_ids") or [])
    if not bridged:
        return min(bridge_count / 2.0, 1.0)  # fallback to count
    max_score = max(float(b.get("score", 0.0)) for b in bridged.values())
    return min(max_score, 1.0)


def _freshness_score(product: dict) -> float:
    count_30d = product.get("review_count_30d", 0) or 0
    if count_30d > 10:
        return 1.0
    elif count_30d > 3:
        return 0.6
    elif count_30d > 0:
        return 0.3
    return 0.0


def _source_popularity_score(product: dict) -> float:
    count = product.get("source_review_count_6m")
    if count is None:
        count = product.get("source_review_count_all")
    try:
        count_int = int(count or 0)
    except (TypeError, ValueError):
        count_int = 0
    if count_int <= 0:
        return 0.0
    cap = 1000
    return min(math.log1p(count_int) / math.log1p(cap), 1.0)


def _source_rating_score(product: dict) -> float:
    rating = product.get("source_avg_rating_6m")
    if rating is None:
        rating = product.get("source_avg_rating_all")
    if rating is None:
        return 0.0
    try:
        rating_float = float(rating)
    except (TypeError, ValueError):
        return 0.0
    if rating_float < 4.0:
        return 0.0
    return min(max(rating_float - 4.0, 0.0), 1.0)


# P4-4 (Wave 3.4): skin_type → concern boost/penalty map loaded from
# configs/skin_type_concern_map.yaml. Cached on first use. `normalize_text`
# applied to both canonical names and aliases so 한국어 / 영문 / normalized
# inputs all match consistently.
_SKIN_TYPE_CONCERN_MAP_CACHE: dict[str, dict] | None = None


def _load_skin_type_concern_map() -> dict[str, dict]:
    """Build a normalized lookup: normalize_text(name) → {boost, penalty}.

    Both canonical names and aliases populate the same row.
    """
    from src.common.text_normalize import normalize_text

    data = load_yaml("skin_type_concern_map.yaml") or {}
    entries = data.get("skin_types") or []
    lookup: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        names: list[str] = [entry["canonical"]] + list(entry.get("aliases") or [])
        row = {
            "boost": list(entry.get("boost") or []),
            "penalty": list(entry.get("penalty") or []),
        }
        for name in names:
            key = normalize_text(name)
            if key:
                lookup[key] = row
    return lookup


def _get_skin_type_concern_map() -> dict[str, dict]:
    global _SKIN_TYPE_CONCERN_MAP_CACHE
    if _SKIN_TYPE_CONCERN_MAP_CACHE is None:
        _SKIN_TYPE_CONCERN_MAP_CACHE = _load_skin_type_concern_map()
    return _SKIN_TYPE_CONCERN_MAP_CACHE


def _skin_type_fit(user_profile: dict, product_profile: dict) -> float:
    """Score skin type fit: user skin_type × product concern signals."""
    from src.common.text_normalize import normalize_text

    skin_type = user_profile.get("skin_type")
    if not skin_type:
        return 0.0

    mapping = _get_skin_type_concern_map().get(normalize_text(skin_type), {})
    if not mapping:
        return 0.0

    boost_concepts = set(mapping.get("boost", []))
    penalty_concepts = set(mapping.get("penalty", []))

    # Check product's concern signals (normalize IDs for matching)
    from src.common.concept_resolver import resolve_concern_id
    pos_ids = {resolve_concern_id(entry["id"] if isinstance(entry, dict) else entry)
               for entry in (product_profile.get("top_concern_pos_ids") or [])}
    neg_ids = {resolve_concern_id(entry["id"] if isinstance(entry, dict) else entry)
               for entry in (product_profile.get("top_concern_neg_ids") or [])}

    score = 0.0
    if boost_concepts & pos_ids:
        score += 1.0
    if penalty_concepts & neg_ids:
        score -= 0.5

    return max(min(score, 1.0), 0.0)


def _purchase_loyalty_score(user_profile: dict, product_profile: dict) -> float:
    """Score based on purchase history: repurchased brand → 1.0, recent → 0.5."""
    brand_id = product_profile.get("brand_id", "")
    if not brand_id:
        return 0.0

    def _strip_brand(b):
        return b[len("concept:Brand:"):] if b.startswith("concept:Brand:") else b
    repurchased = {_strip_brand(entry["id"] if isinstance(entry, dict) else entry)
                   for entry in (user_profile.get("repurchase_brand_ids") or [])}
    if brand_id in repurchased:
        return 1.0

    recent = {_strip_brand(entry["id"] if isinstance(entry, dict) else entry)
              for entry in (user_profile.get("recent_purchase_brand_ids") or [])}
    if brand_id in recent:
        return 0.5

    return 0.0


def _novelty_bonus(user_profile: dict, product_profile: dict) -> float:
    """Score novelty: unknown brand → 1.0, known → 0.5, owned → 0.0, same family → 0.2.

    Priority vs family features:
    - exact_owned_penalty: strong negative for exact SKU (separate feature)
    - owned_family_penalty: mild negative for same family different variant
    - same_family_explore_bonus: positive for exploring known family's other variants
    - novelty_bonus: rewards truly unknown products; reduced for known family
    These features are additive and designed not to double-count.
    """
    product_id = product_profile.get("product_id", "")
    brand_id = product_profile.get("brand_id", "")

    # owned_product_ids may contain product IRIs ("product:P001") or raw IDs
    owned_raw = {entry["id"] if isinstance(entry, dict) else entry
                 for entry in (user_profile.get("owned_product_ids") or [])}
    owned = set()
    for oid in owned_raw:
        if oid.startswith("product:"):
            owned.add(oid[len("product:"):])
        else:
            owned.add(oid)
    if product_id in owned:
        return 0.0

    # Same variant family but different SKU = low novelty
    product_family = product_profile.get("variant_family_id")
    if product_family:
        owned_families_raw = {entry["id"] if isinstance(entry, dict) else entry
                              for entry in (user_profile.get("owned_family_ids") or [])}
        owned_families = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in owned_families_raw}
        if product_family in owned_families:
            return 0.2

    def _strip_brand_prefix(b):
        return b[len("concept:Brand:"):] if b.startswith("concept:Brand:") else b
    all_brands = set()
    for key in ("recent_purchase_brand_ids", "repurchase_brand_ids", "preferred_brand_ids"):
        for entry in (user_profile.get(key) or []):
            raw = entry["id"] if isinstance(entry, dict) else entry
            all_brands.add(_strip_brand_prefix(raw))

    if brand_id and brand_id in all_brands:
        return 0.5

    return 1.0


def _exact_owned_penalty(user_profile: dict, product_profile: dict) -> float:
    """Strong penalty for exact SKU the user already owns."""
    product_id = product_profile.get("product_id", "")
    owned_raw = {entry["id"] if isinstance(entry, dict) else entry
                 for entry in (user_profile.get("owned_product_ids") or [])}
    owned = set()
    for oid in owned_raw:
        owned.add(oid[len("product:"):] if oid.startswith("product:") else oid)
    if product_id in owned:
        return -1.0  # Strong penalty for exact same SKU
    return 0.0


def _owned_family_penalty(user_profile: dict, product_profile: dict) -> float:
    """Mild penalty for same variant family (different SKU) as owned products."""
    family_id = product_profile.get("variant_family_id")
    if not family_id:
        return 0.0
    # Skip if it's an exact owned product (handled by exact_owned_penalty)
    product_id = product_profile.get("product_id", "")
    owned_raw = {entry["id"] if isinstance(entry, dict) else entry
                 for entry in (user_profile.get("owned_product_ids") or [])}
    owned = {oid[len("product:"):] if oid.startswith("product:") else oid for oid in owned_raw}
    if product_id in owned:
        return 0.0  # exact owned handled separately
    owned_families_raw = {entry["id"] if isinstance(entry, dict) else entry
                          for entry in (user_profile.get("owned_family_ids") or [])}
    owned_families = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in owned_families_raw}
    if family_id in owned_families:
        return -0.3  # Milder penalty for same family different variant
    return 0.0


def _same_family_explore_bonus(user_profile: dict, product_profile: dict) -> float:
    """Bonus for exploring a different variant in a family the user has experience with.

    Only applies when the product is NOT exact-owned but IS in an owned or repurchased family.
    Encourages "try a different shade/size from a line you know".
    """
    family_id = product_profile.get("variant_family_id")
    if not family_id:
        return 0.0
    product_id = product_profile.get("product_id", "")
    owned_raw = {entry["id"] if isinstance(entry, dict) else entry
                 for entry in (user_profile.get("owned_product_ids") or [])}
    owned = {oid[len("product:"):] if oid.startswith("product:") else oid for oid in owned_raw}
    if product_id in owned:
        return 0.0  # exact owned — no explore bonus
    # Check if in any known family (owned or repurchased)
    known_families_raw = set()
    for key in ("owned_family_ids", "repurchased_family_ids"):
        for entry in (user_profile.get(key) or []):
            known_families_raw.add(entry["id"] if isinstance(entry, dict) else entry)
    known_families = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in known_families_raw}
    if family_id in known_families:
        return 0.5  # Bonus for exploring familiar family
    return 0.0


def _repurchase_family_affinity(user_profile: dict, product_profile: dict) -> float:
    """Boost products in same family as repurchased products."""
    family_id = product_profile.get("variant_family_id")
    if not family_id:
        return 0.0
    repurchased_families_raw = {entry["id"] if isinstance(entry, dict) else entry
                                for entry in (user_profile.get("repurchased_family_ids") or [])}
    repurchased_families = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in repurchased_families_raw}
    if family_id in repurchased_families:
        return 1.0
    return 0.0
