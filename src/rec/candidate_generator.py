"""
Candidate generator: hard filter → concept overlap retrieval.

Step 1: Hard filter (zero-out)
Step 2: Concept overlap scoring for remaining candidates
Supports recommendation modes: STRICT, EXPLORE, COMPARE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.enums import RecommendationMode


@dataclass
class CandidateProduct:
    product_id: str
    overlap_concepts: list[str] = field(default_factory=list)
    overlap_score: float = 0.0
    hard_filtered: bool = False
    filter_reason: str | None = None
    already_owned: bool = False


def generate_candidates(
    user_profile: dict[str, Any],
    product_profiles: list[dict[str, Any]],
    mode: RecommendationMode = RecommendationMode.STRICT,
    max_candidates: int = 50,
) -> list[CandidateProduct]:
    """Generate recommendation candidates.

    Args:
        user_profile: serving_user_profile row
        product_profiles: list of serving_product_profile rows
        mode: Recommendation mode
        max_candidates: Max candidates to return
    """
    # Extract user signals for filtering
    avoided_ingredients = _extract_ids(user_profile.get("avoided_ingredient_ids", []))
    preferred_categories = _extract_ids(user_profile.get("preferred_category_ids", []))
    preferred_brands = _extract_ids(user_profile.get("preferred_brand_ids", []))
    concern_ids = _extract_ids(user_profile.get("concern_ids", []))
    preferred_keywords = _extract_ids(user_profile.get("preferred_keyword_ids", []))
    preferred_bee_attrs = _extract_ids(user_profile.get("preferred_bee_attr_ids", []))
    preferred_contexts = _extract_ids(user_profile.get("preferred_context_ids", []))
    goal_ids = _extract_ids(user_profile.get("goal_ids", []))
    owned_product_ids = _extract_ids(user_profile.get("owned_product_ids", []))

    candidates: list[CandidateProduct] = []

    for product in product_profiles:
        pid = product["product_id"]
        candidate = CandidateProduct(product_id=pid)
        if pid in owned_product_ids:
            candidate.already_owned = True

        # --- Hard filters (zero-out) ---

        # 1. Ingredient conflict (via concept_id)
        product_ingredients = set(product.get("ingredient_concept_ids") or product.get("ingredient_ids") or [])
        if avoided_ingredients & product_ingredients:
            candidate.hard_filtered = True
            candidate.filter_reason = "AVOIDED_INGREDIENT_CONFLICT"
            candidates.append(candidate)
            continue

        # 2. Category mismatch (mode-dependent, via concept_id)
        product_categories = set(product.get("category_concept_ids") or [])
        if not product_categories:
            product_categories = {product.get("category_id", "")} - {""}
        if preferred_categories and product_categories:
            if not (preferred_categories & product_categories):
                if mode == RecommendationMode.STRICT:
                    candidate.hard_filtered = True
                    candidate.filter_reason = "CATEGORY_MISMATCH_STRICT"
                    candidates.append(candidate)
                    continue

        # --- Concept overlap scoring ---
        # NOTE: catalog_validation signals are excluded — they must not influence
        # candidate generation, scoring, or standard explanation (QA/debug only)
        overlap = []

        # Brand match (concept_id join key)
        product_brands = set(product.get("brand_concept_ids") or [])
        for b in preferred_brands & product_brands:
            overlap.append(f"brand:{b}")

        # Category match (concept_id)
        for c in preferred_categories & product_categories:
            overlap.append(f"category:{c}")

        # Keyword overlap
        product_keywords = _extract_signal_ids(product.get("top_keyword_ids", []))
        for kw in preferred_keywords & product_keywords:
            overlap.append(f"keyword:{kw}")

        # BEE_ATTR overlap
        product_attrs = _extract_signal_ids(product.get("top_bee_attr_ids", []))
        for attr in preferred_bee_attrs & product_attrs:
            overlap.append(f"bee_attr:{attr}")

        # Context overlap
        product_contexts = _extract_signal_ids(product.get("top_context_ids", []))
        for ctx in preferred_contexts & product_contexts:
            overlap.append(f"context:{ctx}")

        # Concern overlap (product addresses user's concern)
        product_concerns = _extract_signal_ids(product.get("top_concern_pos_ids", []))
        for c in concern_ids & product_concerns:
            overlap.append(f"concern:{c}")

        # Goal overlap: master (product benefits) + review (concern→goal match)
        product_benefits = set(product.get("main_benefit_concept_ids") or product.get("main_benefit_ids") or [])
        for g in goal_ids & product_benefits:
            overlap.append(f"goal_master:{g}")
        # Goal from review signals: product concerns that match user goals
        for g in goal_ids & product_concerns:
            overlap.append(f"goal_review:{g}")

        candidate.overlap_concepts = overlap
        candidate.overlap_score = len(overlap)
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
    return generate_candidates(user_profile, product_profiles, mode, max_candidates)


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
