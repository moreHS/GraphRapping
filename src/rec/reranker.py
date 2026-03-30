"""
Reranker: brand/category diversity bonus + contribution logging.

Penalizes consecutive products from same brand/category in top-k.
Logs contribution details for analysis (optional DB write).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.rec.scorer import ScoredProduct


@dataclass
class RerankedProduct:
    product_id: str
    original_rank: int
    final_rank: int
    final_score: float
    diversity_bonus: float = 0.0
    contribution_log: dict[str, float] = field(default_factory=dict)


def rerank(
    scored_products: list[ScoredProduct],
    product_profiles: dict[str, dict] | None = None,
    diversity_weight: float = 0.05,
    top_k: int = 20,
) -> list[RerankedProduct]:
    """Rerank scored products with brand/category diversity.

    Products of the same brand/category as recently selected items
    get a penalty to encourage diversity in the top-k.
    """
    if not scored_products:
        return []

    # Sort by shrinked_score
    sorted_products = sorted(scored_products, key=lambda s: s.final_score, reverse=True)

    # Greedy diversity reranking (MMR-style)
    selected: list[RerankedProduct] = []
    remaining = list(enumerate(sorted_products[:top_k * 2]))
    seen_brands: dict[str, int] = {}  # brand → count
    seen_categories: dict[str, int] = {}

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_adjusted = -float("inf")

        for i, (orig_rank, sp) in enumerate(remaining):
            bonus = 0.0
            if product_profiles:
                profile = product_profiles.get(sp.product_id, {})
                brand = profile.get("brand_id", "")
                category = profile.get("category_id", "")

                # Penalize repeated brand/category
                brand_count = seen_brands.get(brand, 0) if brand else 0
                cat_count = seen_categories.get(category, 0) if category else 0
                penalty = diversity_weight * (brand_count + cat_count)
                bonus = -penalty

            adjusted = sp.final_score + bonus
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_idx = i

        orig_rank, sp = remaining.pop(best_idx)
        diversity_bonus = best_adjusted - sp.final_score

        # Track seen brands/categories
        if product_profiles:
            profile = product_profiles.get(sp.product_id, {})
            brand = profile.get("brand_id", "")
            category = profile.get("category_id", "")
            if brand:
                seen_brands[brand] = seen_brands.get(brand, 0) + 1
            if category:
                seen_categories[category] = seen_categories.get(category, 0) + 1

        selected.append(RerankedProduct(
            product_id=sp.product_id,
            original_rank=orig_rank,
            final_rank=len(selected),
            final_score=round(best_adjusted, 4),
            diversity_bonus=round(diversity_bonus, 4),
            contribution_log=sp.feature_contributions,
        ))

    return selected


def build_contribution_log_rows(
    reranked: list[RerankedProduct],
    run_id: int | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build rows for reranker_contribution_log table."""
    rows = []
    for r in reranked:
        rows.append({
            "run_id": run_id,
            "user_id": user_id,
            "product_id": r.product_id,
            "original_rank": r.original_rank,
            "final_rank": r.final_rank,
            "diversity_bonus": r.diversity_bonus,
            "contribution_json": r.contribution_log,
        })
    return rows
