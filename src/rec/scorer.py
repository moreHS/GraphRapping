"""
Scorer: linear scoring + evidence shrinkage + brand confidence-weight.

Score = residual_bee_attr + keyword + context + concern + ingredient + brand + goal + category + freshness
Shrinkage: score * (support / (support + k))
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.config_loader import load_yaml
from src.common.enums import SCORING_EXCLUDED_FAMILIES


@dataclass
class ScoredProduct:
    product_id: str
    raw_score: float
    shrinked_score: float
    final_score: float
    feature_contributions: dict[str, float]
    support_count: int = 0


class Scorer:
    """Scores candidate products against user preferences."""

    def __init__(self) -> None:
        self._weights: dict[str, float] = {}
        self._shrinkage_k: float = 10.0
        self._brand_confidence: dict[str, float] = {}

    def load_config(self, filename: str = "scoring_weights.yaml") -> None:
        config = load_yaml(filename)
        self._weights = config.get("features", {})
        self._shrinkage_k = config.get("shrinkage_k", 10.0)
        self._brand_confidence = config.get("brand_confidence", {})

    def load_from_dict(self, weights: dict, shrinkage_k: float = 10.0) -> None:
        self._weights = weights
        self._shrinkage_k = shrinkage_k

    def score(
        self,
        user_profile: dict[str, Any],
        product_profile: dict[str, Any],
        overlap_concepts: list[str] | None = None,
        brand_source: str = "purchase",
    ) -> ScoredProduct:
        """Score a single product against user preferences.

        Uses residual BEE_ATTR scoring to avoid double-counting with keywords.
        """
        pid = product_profile["product_id"]
        contributions: dict[str, float] = {}

        # Parse overlap concepts by type
        overlaps_by_type: dict[str, int] = {}
        for concept in (overlap_concepts or []):
            ctype = concept.split(":")[0] if ":" in concept else "other"
            overlaps_by_type[ctype] = overlaps_by_type.get(ctype, 0) + 1

        # Feature scoring
        keyword_count = overlaps_by_type.get("keyword", 0)
        bee_attr_count = overlaps_by_type.get("bee_attr", 0)

        # Residual BEE_ATTR: only count attrs not already covered by keywords
        residual_attr = max(0, bee_attr_count - keyword_count)

        # Goal split: master (product truth) vs review (review-derived signals)
        goal_master_count = overlaps_by_type.get("goal_master", 0) + overlaps_by_type.get("goal", 0)
        goal_review_count = overlaps_by_type.get("goal_review", 0)

        # skin_type_fit: user skin_type × product concern signals
        skin_type_fit_val = _skin_type_fit(user_profile, product_profile)

        # Purchase-derived features
        purchase_loyalty = _purchase_loyalty_score(user_profile, product_profile)
        novelty = _novelty_bonus(user_profile, product_profile)

        features = {
            "keyword_match": min(keyword_count / 3.0, 1.0),
            "residual_bee_attr_match": min(residual_attr / 2.0, 1.0),
            "context_match": min(overlaps_by_type.get("context", 0) / 2.0, 1.0),
            "concern_fit": min(overlaps_by_type.get("concern", 0) / 2.0, 1.0),
            "ingredient_match": min(overlaps_by_type.get("ingredient", 0) / 3.0, 1.0),
            "brand_match_conf_weighted": _brand_score(overlaps_by_type.get("brand", 0), brand_source, self._brand_confidence),
            "goal_fit_master": min(goal_master_count / 2.0, 1.0),
            "goal_fit_review_signal": min(goal_review_count / 2.0, 1.0),
            "category_affinity": min(overlaps_by_type.get("category", 0), 1.0),
            "freshness_boost": _freshness_score(product_profile),
            "skin_type_fit": skin_type_fit_val,
            "purchase_loyalty_score": purchase_loyalty,
            "novelty_bonus": novelty,
            "exact_owned_penalty": _exact_owned_penalty(user_profile, product_profile),
            "owned_family_penalty": _owned_family_penalty(user_profile, product_profile),
            "same_family_explore_bonus": _same_family_explore_bonus(user_profile, product_profile),
            "repurchase_family_affinity": _repurchase_family_affinity(user_profile, product_profile),
            "tool_alignment": min(overlaps_by_type.get("tool", 0) / 2.0, 1.0),
            "coused_product_bonus": min(overlaps_by_type.get("coused", 0) / 2.0, 1.0),
        }

        raw_score = sum(
            self._weights.get(feature, 0.0) * value
            for feature, value in features.items()
        )

        contributions = {k: self._weights.get(k, 0.0) * v for k, v in features.items() if v > 0}

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
        )


def _brand_score(brand_overlap: int, brand_source: str, conf_map: dict) -> float:
    if brand_overlap == 0:
        return 0.0
    conf = conf_map.get(brand_source, 0.5)
    return min(conf, 1.0)


def _freshness_score(product: dict) -> float:
    count_30d = product.get("review_count_30d", 0) or 0
    if count_30d > 10:
        return 1.0
    elif count_30d > 3:
        return 0.6
    elif count_30d > 0:
        return 0.3
    return 0.0


# Skin type → concern mapping for scoring
_SKIN_TYPE_CONCERN_MAP = {
    "건성": {"boost": ["dryness", "moisturizing", "보습"], "penalty": ["oily", "유분"]},
    "지성": {"boost": ["oily", "유분", "피지"], "penalty": ["heavy", "끈적"]},
    "복합성": {"boost": ["t_zone", "유분", "보습"], "penalty": []},
    "민감성": {"boost": ["sensitivity", "자극", "진정"], "penalty": ["harsh", "자극적"]},
    "dry": {"boost": ["dryness", "moisturizing"], "penalty": ["oily"]},
    "oily": {"boost": ["oily", "sebum"], "penalty": ["heavy"]},
    "combination": {"boost": ["t_zone", "oily", "moisturizing"], "penalty": []},
    "sensitive": {"boost": ["sensitivity", "soothing"], "penalty": ["harsh"]},
}


def _skin_type_fit(user_profile: dict, product_profile: dict) -> float:
    """Score skin type fit: user skin_type × product concern signals."""
    skin_type = user_profile.get("skin_type")
    if not skin_type:
        return 0.0

    mapping = _SKIN_TYPE_CONCERN_MAP.get(skin_type, {})
    if not mapping:
        return 0.0

    boost_concepts = set(mapping.get("boost", []))
    penalty_concepts = set(mapping.get("penalty", []))

    # Check product's concern signals
    pos_ids = {entry["id"] if isinstance(entry, dict) else entry
               for entry in (product_profile.get("top_concern_pos_ids") or [])}
    neg_ids = {entry["id"] if isinstance(entry, dict) else entry
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

    repurchased = {entry["id"] if isinstance(entry, dict) else entry
                   for entry in (user_profile.get("repurchase_brand_ids") or [])}
    if brand_id in repurchased:
        return 1.0

    recent = {entry["id"] if isinstance(entry, dict) else entry
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
        owned_families = {entry["id"] if isinstance(entry, dict) else entry
                         for entry in (user_profile.get("owned_family_ids") or [])}
        if product_family in owned_families:
            return 0.2

    all_brands = set()
    for key in ("recent_purchase_brand_ids", "repurchase_brand_ids", "preferred_brand_ids"):
        for entry in (user_profile.get(key) or []):
            all_brands.add(entry["id"] if isinstance(entry, dict) else entry)

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
    owned_families = {entry["id"] if isinstance(entry, dict) else entry
                      for entry in (user_profile.get("owned_family_ids") or [])}
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
    known_families = set()
    for key in ("owned_family_ids", "repurchased_family_ids"):
        for entry in (user_profile.get(key) or []):
            known_families.add(entry["id"] if isinstance(entry, dict) else entry)
    if family_id in known_families:
        return 0.5  # Bonus for exploring familiar family
    return 0.0


def _repurchase_family_affinity(user_profile: dict, product_profile: dict) -> float:
    """Boost products in same family as repurchased products."""
    family_id = product_profile.get("variant_family_id")
    if not family_id:
        return 0.0
    repurchased_families = {entry["id"] if isinstance(entry, dict) else entry
                           for entry in (user_profile.get("repurchased_family_ids") or [])}
    if family_id in repurchased_families:
        return 1.0
    return 0.0
