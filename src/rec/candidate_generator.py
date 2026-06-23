"""
Candidate generator: hard filter → concept overlap retrieval.

Step 1: Hard filter (zero-out)
Step 2: Concept overlap scoring for remaining candidates
Supports recommendation modes: STRICT, EXPLORE, COMPARE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.config_loader import get_texture_axis
from src.common.enums import RecommendationMode
from src.common.concept_resolver import resolve_concern_id, resolve_goal_id
from src.rec.concern_bridge import compute_bridged_concerns
from src.rec.category_groups import (
    category_groups_for_values,
    classify_product_category_group,
    product_category_text,
)
from src.rec.recommendation_evidence_index import (
    CandidateEligibility,
    build_candidate_eligibility,
)
from src.rec.semantic_compatibility import find_semantic_matches, normalize_signal_id
from src.rec.scoped_preferences import collect_preference_ids


@dataclass
class CandidateProduct:
    product_id: str
    overlap_concepts: list[str] = field(default_factory=list)
    overlap_score: float = 0.0
    eligibility: CandidateEligibility = field(default_factory=CandidateEligibility)
    hard_filtered: bool = False
    filter_reason: str | None = None
    already_owned: bool = False
    owned_family_match: bool = False
    repurchased_family_match: bool = False
    # Family candidate bucket: classifies the product's relationship to user's owned inventory
    #   EXACT_OWNED — exact SKU the user already has
    #   SAME_FAMILY_OTHER_VARIANT — different SKU in an owned/known family
    #   NON_FAMILY — no family relationship
    candidate_bucket: str = "NON_FAMILY"


def generate_candidates(
    user_profile: dict[str, Any],
    product_profiles: list[dict[str, Any]],
    mode: RecommendationMode = RecommendationMode.STRICT,
    max_candidates: int = 50,
    *,
    require_evidence: bool = True,
) -> list[CandidateProduct]:
    """Generate recommendation candidates.

    Args:
        user_profile: serving_user_profile row
        product_profiles: list of serving_product_profile rows
        mode: Recommendation mode
        max_candidates: Max candidates to return
        require_evidence: When True, source-only/profile-unrelated products are
            hard-filtered after first-class evidence classification.
    """
    # Extract user signals for filtering
    avoided_ingredients = _extract_ids(user_profile.get("avoided_ingredient_ids", []))
    repurchase_brand_ids = _extract_ids(user_profile.get("repurchase_brand_ids", []))
    repurchase_category_ids = _extract_ids(user_profile.get("repurchase_category_ids", []))
    recent_purchase_brand_ids = _extract_ids(user_profile.get("recent_purchase_brand_ids", []))
    owned_product_ids_raw = _extract_ids(user_profile.get("owned_product_ids", []))
    owned_family_ids_raw = _extract_ids(user_profile.get("owned_family_ids", []))
    repurchased_family_ids_raw = _extract_ids(user_profile.get("repurchased_family_ids", []))
    # Normalize: owned_product_ids / family_ids may contain product IRIs ("product:P001")
    # or raw IDs ("P001"). Strip prefix to match against raw product_id / variant_family_id.
    owned_product_ids = set()
    for oid in owned_product_ids_raw:
        if oid.startswith("product:"):
            owned_product_ids.add(oid[len("product:"):])
        else:
            owned_product_ids.add(oid)
    owned_family_ids = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in owned_family_ids_raw}
    repurchased_families = {fid[len("product:"):] if fid.startswith("product:") else fid for fid in repurchased_family_ids_raw}

    candidates: list[CandidateProduct] = []

    for product in product_profiles:
        pid = product["product_id"]
        candidate = CandidateProduct(product_id=pid)
        if pid in owned_product_ids:
            candidate.already_owned = True
        product_family = product.get("variant_family_id")
        if product_family and product_family in owned_family_ids:
            candidate.owned_family_match = True
        if product_family and product_family in repurchased_families:
            candidate.repurchased_family_match = True
        product_category_group = classify_product_category_group(product)
        product_catalog_text = product_category_text(product)

        avoided_ingredients = collect_preference_ids(
            user_profile, "avoided_ingredient_ids", "AVOIDS_INGREDIENT", product_category_group,
        )
        preferred_brands = collect_preference_ids(
            user_profile, "preferred_brand_ids", "PREFERS_BRAND", product_category_group,
        )
        concern_ids = collect_preference_ids(
            user_profile, "concern_ids", "HAS_CONCERN", product_category_group,
        )
        preferred_keywords = collect_preference_ids(
            user_profile, "preferred_keyword_ids", "PREFERS_KEYWORD", product_category_group,
        )
        preferred_bee_attrs = collect_preference_ids(
            user_profile, "preferred_bee_attr_ids", "PREFERS_BEE_ATTR", product_category_group,
        )
        preferred_contexts = collect_preference_ids(
            user_profile, "preferred_context_ids", "PREFERS_CONTEXT", product_category_group,
        )
        goal_ids = collect_preference_ids(
            user_profile, "goal_ids", "WANTS_GOAL", product_category_group,
        )
        preferred_ingredients = collect_preference_ids(
            user_profile, "preferred_ingredient_ids", "PREFERS_INGREDIENT", product_category_group,
        )
        active_categories = collect_preference_ids(
            user_profile, "active_category_ids", "ACTIVE_IN_CATEGORY", product_category_group,
        )
        preferred_categories = collect_preference_ids(
            user_profile, "preferred_category_ids", "PREFERS_CATEGORY", product_category_group,
        )
        active_category_groups = category_groups_for_values(active_categories)
        preferred_category_groups = category_groups_for_values(preferred_categories)

        # Classify candidate bucket
        if candidate.already_owned:
            candidate.candidate_bucket = "EXACT_OWNED"
        elif candidate.owned_family_match or candidate.repurchased_family_match:
            candidate.candidate_bucket = "SAME_FAMILY_OTHER_VARIANT"

        # --- Hard filters (zero-out) ---

        # 1. Ingredient conflict (raw IDs + concept IDs, matching SQL prefilter)
        product_ingredients = set(product.get("ingredient_concept_ids") or [])
        product_ingredients.update(product.get("ingredient_ids") or [])
        if avoided_ingredients & product_ingredients:
            candidate.hard_filtered = True
            candidate.filter_reason = "AVOIDED_INGREDIENT_CONFLICT"
            candidates.append(candidate)
            continue

        # 2. Category mismatch (mode-dependent, via concept_id)
        product_categories = _concept_and_raw_ids(
            product.get("category_concept_ids") or [],
            product.get("category_id"),
        )
        preferred_category_matches = _matching_ids(preferred_categories, product_categories)
        preferred_category_group_matches = (
            {product_category_group}
            if product_category_group in preferred_category_groups
            else set()
        )
        if preferred_categories and product_categories:
            if not preferred_category_matches and not preferred_category_group_matches:
                if mode == RecommendationMode.STRICT:
                    candidate.hard_filtered = True
                    candidate.filter_reason = "CATEGORY_MISMATCH_STRICT"
                    candidates.append(candidate)
                    continue

        # 3. Ownership suppression (mode-dependent)
        # Priority: exact SKU owned > same family other variant > repurchased family
        if candidate.already_owned:
            if mode == RecommendationMode.STRICT:
                candidate.hard_filtered = True
                candidate.filter_reason = "EXACT_SKU_OWNED_SUPPRESS"
                candidates.append(candidate)
                continue
        elif candidate.owned_family_match:
            if mode == RecommendationMode.STRICT:
                candidate.hard_filtered = True
                candidate.filter_reason = "OWNED_FAMILY_STRICT_SUPPRESS"
                candidates.append(candidate)
                continue

        # --- Concept overlap scoring ---
        # NOTE: catalog_validation signals are excluded — they must not influence
        # candidate generation, scoring, or standard explanation (QA/debug only)
        overlap = []

        # Brand match (concept_id join key)
        product_brands = _concept_and_raw_ids(
            product.get("brand_concept_ids") or [],
            product.get("brand_id"),
        )
        for b in _matching_ids(preferred_brands, product_brands):
            overlap.append(f"brand:{b}")

        # Category match (concept_id)
        for c in preferred_category_matches:
            overlap.append(f"category:{c}")
        for group in sorted(preferred_category_group_matches):
            overlap.append(f"category:concept:Category:{group}")
        active_category_matches = _matching_ids(active_categories, product_categories)
        active_category_group_matches = (
            {product_category_group}
            if product_category_group in active_category_groups
            else set()
        )
        for c in active_category_matches:
            overlap.append(f"active_category:{c}")
        for group in sorted(active_category_group_matches):
            overlap.append(f"active_category:concept:Category:{group}")

        # Product-master taxonomy/name keyword overlap. This uses catalog truth
        # only when the user's keyword/category value is present in product
        # category/name text; it is separate from review graph keyword evidence.
        for kw in _catalog_text_matches(preferred_keywords, product_catalog_text):
            overlap.append(f"catalog_keyword:{kw}")
        for c in _catalog_text_matches(repurchase_category_ids, product_catalog_text):
            overlap.append(f"repurchase_category:{c}")

        # Keyword overlap
        product_keywords = _extract_signal_ids(product.get("top_keyword_ids", []))
        exact_keyword_keys = {_join_key(v) for v in preferred_keywords} & {_join_key(v) for v in product_keywords}
        for kw in _matching_ids(preferred_keywords, product_keywords):
            overlap.append(f"keyword:{kw}")

        # BEE_ATTR overlap
        product_attrs = _extract_signal_ids(product.get("top_bee_attr_ids", []))
        preferred_specific_attrs = _exclude_generic_bee_attrs(preferred_bee_attrs)
        product_specific_attrs = _exclude_generic_bee_attrs(product_attrs)
        exact_attr_keys = (
            {_join_key(v) for v in preferred_specific_attrs}
            & {_join_key(v) for v in product_specific_attrs}
        )
        for attr in _matching_ids(preferred_specific_attrs, product_specific_attrs):
            overlap.append(f"bee_attr:{attr}")

        # Semantic compatibility overlap. This is value-and-polarity gated by
        # configs/recommendation_semantic_compatibility.yaml; generic axes such
        # as formulation/texture do not score unless a compatible value exists.
        for match in find_semantic_matches(user_profile, product):
            product_key = normalize_signal_id(match.product_id)
            if match.product_type == "keyword" and product_key in exact_keyword_keys:
                continue
            if match.product_type == "bee_attr" and product_key in exact_attr_keys:
                continue
            overlap.append(match.to_overlap_concept())

        # Context overlap
        product_contexts = _extract_signal_ids(product.get("top_context_ids", []))
        for ctx in _matching_ids(preferred_contexts, product_contexts):
            overlap.append(f"context:{ctx}")

        # Ingredient overlap (product truth ingredients vs user preferred ingredients)
        product_ingredients_concept = _concept_and_raw_ids(
            product.get("ingredient_concept_ids") or [],
            product.get("ingredient_ids") or [],
        )
        for ing in _matching_ids(preferred_ingredients, product_ingredients_concept):
            overlap.append(f"ingredient:{ing}")

        # Concern overlap (with ID normalization for cross-source matching)
        user_concerns_norm = {resolve_concern_id(c) for c in concern_ids}
        product_concerns_raw = _extract_signal_ids(product.get("top_concern_pos_ids", []))
        product_concerns_norm = {resolve_concern_id(c) for c in product_concerns_raw}
        for c in user_concerns_norm & product_concerns_norm:
            overlap.append(f"concern:{c}")

        # BEE_ATTR → Concern bridge (discounted indirect matching)
        bridged = compute_bridged_concerns(product.get("top_bee_attr_ids", []))
        explicit_concerns = {c.split(":", 1)[1] for c in overlap if c.startswith("concern:")}
        for bridge_concern_id in user_concerns_norm & set(bridged.keys()):
            if bridge_concern_id not in explicit_concerns:
                overlap.append(f"concern_bridge:{bridge_concern_id}")

        # Goal overlap: master (with alias normalization)
        user_goals_norm = {resolve_goal_id(g) for g in goal_ids}
        product_benefits = set(product.get("main_benefit_concept_ids") or product.get("main_benefit_ids") or [])
        product_benefits_norm = {resolve_goal_id(g) for g in product_benefits}
        for g in user_goals_norm & product_benefits_norm:
            overlap.append(f"goal_master:{g}")
        # NOTE: Goal × concern cross-match removed — different concept planes
        # cannot match through separate resolvers. Use concern_bridge instead.

        # Tool overlap (user preferred tools × product tool signals)
        preferred_tools = _extract_ids(user_profile.get("preferred_tool_ids", []))
        product_tools = _extract_signal_ids(product.get("top_tool_ids", []))
        for t in _matching_ids(preferred_tools, product_tools):
            overlap.append(f"tool:{t}")

        # Co-used product overlap (user owned products × product co-use signals)
        product_coused = _extract_signal_ids(product.get("top_coused_product_ids", []))
        for co in owned_product_ids & product_coused:
            overlap.append(f"coused:{co}")

        # Purchase-behavior brand overlaps. These qualify candidates because
        # the match is user behavior aligned, not just product catalog presence.
        for b in _matching_ids(repurchase_brand_ids, product_brands):
            overlap.append(f"repurchase_brand:{b}")
        for b in _matching_ids(recent_purchase_brand_ids, product_brands):
            overlap.append(f"recent_purchase_brand:{b}")

        # Family overlap (for explanation paths)
        if candidate.owned_family_match and product_family:
            overlap.append(f"owned_family:{product_family}")
        if candidate.repurchased_family_match and product_family:
            overlap.append(f"repurchased_family:{product_family}")

        candidate.overlap_concepts = overlap
        candidate.overlap_score = len(overlap)
        candidate.eligibility = build_candidate_eligibility(overlap)
        if require_evidence and not candidate.eligibility.eligible:
            candidate.hard_filtered = True
            candidate.filter_reason = "NO_USER_ALIGNED_EVIDENCE"
        candidates.append(candidate)

    # Sort by overlap score, filter out hard-filtered, deprioritize owned
    valid = [c for c in candidates if not c.hard_filtered]
    # Already-owned products sort to the bottom (still returned but deprioritized)
    valid.sort(key=lambda c: (not c.already_owned, c.overlap_score), reverse=True)

    return valid[:max_candidates]


def generate_candidates_prefiltered(
    user_profile: dict[str, Any],
    prefiltered_product_ids: list[str],
    product_profiles_by_id: dict[str, dict[str, Any]],
    mode: RecommendationMode = RecommendationMode.STRICT,
    max_candidates: int = 50,
    *,
    require_evidence: bool = True,
) -> list[CandidateProduct]:
    """Generate candidates from a pre-filtered set of product IDs.

    Use with sql_prefilter_candidates() for SQL-first candidate generation.
    Falls back to in-memory overlap scoring on the reduced product set.
    """
    product_profiles = [
        product_profiles_by_id[pid]
        for pid in prefiltered_product_ids
        if pid in product_profiles_by_id
    ]
    return generate_candidates(
        user_profile,
        product_profiles,
        mode,
        max_candidates,
        require_evidence=require_evidence,
    )


def _extract_ids(items: list) -> set[str]:
    """Extract IDs from preference list (can be dicts with 'id' key or plain strings)."""
    result = set()
    for item in items:
        if isinstance(item, dict):
            result.add(item.get("id", ""))
        else:
            result.add(str(item))
    return result - {""}


def _extract_signal_ids(items: list) -> set[str]:
    """Extract IDs from signal summary (dicts with 'id' key)."""
    return {item["id"] for item in items if isinstance(item, dict) and "id" in item}


def _concept_and_raw_ids(concept_ids: list, raw_ids: Any = None) -> set[str]:
    values = _extract_ids(concept_ids)
    if raw_ids is None:
        return values
    if isinstance(raw_ids, (list, tuple, set)):
        values.update(str(v) for v in raw_ids if v)
    elif raw_ids:
        values.add(str(raw_ids))
    return values - {""}


def _join_key(value: str) -> str:
    if value.startswith("concept:"):
        parts = value.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    if value.startswith("product:"):
        return value[len("product:"):]
    return value


def _matching_ids(left: set[str], right: set[str]) -> list[str]:
    """Return deterministic left-side IDs whose raw/concept join key matches."""
    right_keys = {_join_key(v) for v in right}
    matches = [v for v in left if _join_key(v) in right_keys]
    return sorted(matches, key=lambda v: (_join_key(v), v))


def _catalog_text_matches(values: set[str], catalog_text: str) -> list[str]:
    if not catalog_text:
        return []
    matches = [
        value
        for value in values
        if (key := normalize_signal_id(value)) and key in catalog_text
    ]
    return sorted(matches, key=lambda v: (normalize_signal_id(v), v))


def _exclude_generic_bee_attrs(values: set[str]) -> set[str]:
    generic_keys = {
        normalize_signal_id(get_texture_axis()),
        normalize_signal_id("concept:BEEAttr:bee_attr_texture_feel"),
    }
    return {value for value in values if normalize_signal_id(value) not in generic_keys}
