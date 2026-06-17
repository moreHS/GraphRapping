"""
Single source of truth for serving_*_profile columns.

`sql/ddl_mart.sql` (CREATE TABLE), `src/db/repos/mart_repo.py` (UPSERT),
and `src/mart/build_serving_views.py` (builder output) must stay in sync.
This module exports the canonical column lists and TypedDicts; a regression
test in `tests/test_serving_profile_columns_align.py` catches drift.

Meta columns (`is_active`, `updated_at`) are managed by the repo layer and
excluded here — these tuples describe the application-level payload.

P3-1 (audit fix): consolidates column names that were previously duplicated
across 3 files.
"""

from __future__ import annotations

from typing import Any, TypedDict


SERVING_PRODUCT_PROFILE_COLUMNS: tuple[str, ...] = (
    "product_id",
    # Source identity fields. `product_id` remains the graph/source join key;
    # these make the source contract explicit for consumers.
    "source_product_id",
    "source_channel",
    "source_key_type",
    # Truth columns (raw, from product_master)
    "brand_id",
    "brand_name",
    "category_id",
    "category_name",
    "country_of_origin",
    "price",
    "price_band",
    "variant_family_id",
    "representative_product_name",
    "main_benefit_ids",
    "ingredient_ids",
    # Concept ID fields (canonical join keys — concept_id, not raw IRI)
    "brand_concept_ids",
    "category_concept_ids",
    "ingredient_concept_ids",
    "main_benefit_concept_ids",
    # Signal columns (from agg_product_signal)
    "top_bee_attr_ids",
    "top_keyword_ids",
    "top_context_ids",
    "top_concern_pos_ids",
    "top_concern_neg_ids",
    "top_tool_ids",
    "top_comparison_product_ids",
    "top_coused_product_ids",
    # Freshness
    "last_signal_at",
    # review_count_* are product-level distinct review_id counts (P3-7).
    # signal_support_count_all is the legacy inflated sum-of-review_cnt,
    # exposed explicitly for downstream UI needs.
    "review_count_30d",
    "review_count_90d",
    "review_count_all",
    "signal_support_count_all",
    # Source review volume/rating fields. These are raw source stats and do
    # not redefine review_count_* graph-support semantics above.
    "source_review_count_6m",
    "source_review_score_count_6m",
    "source_avg_rating_6m",
    "source_review_min_date_6m",
    "source_review_max_date_6m",
    "source_review_count_all",
    "source_review_score_count_all",
    "source_avg_rating_all",
    "source_review_min_date_all",
    "source_review_max_date_all",
    "source_review_stats_source",
)


SERVING_USER_PROFILE_COLUMNS: tuple[str, ...] = (
    "user_id",
    # Demographics
    "age_band",
    "gender",
    "skin_type",
    "skin_tone",
    # Preference summaries (from agg_user_preference)
    "preferred_brand_ids",
    "preferred_category_ids",
    "preferred_ingredient_ids",
    "avoided_ingredient_ids",
    "concern_ids",
    "goal_ids",
    "preferred_bee_attr_ids",
    "preferred_keyword_ids",
    "preferred_context_ids",
    # Behavior section (purchase-derived)
    "recent_purchase_brand_ids",
    "repurchase_brand_ids",
    "repurchase_category_ids",
    "owned_product_ids",
    "owned_family_ids",
    "repurchased_family_ids",
)


class ServingProductProfile(TypedDict, total=False):
    """Application-level payload for serving_product_profile rows.

    Mirrors `SERVING_PRODUCT_PROFILE_COLUMNS`. `total=False` so partial
    construction during build is allowed; the alignment test enforces
    completeness of the builder output.
    """

    product_id: str
    source_product_id: str
    source_channel: str | None
    source_key_type: str | None
    brand_id: str | None
    brand_name: str | None
    category_id: str | None
    category_name: str | None
    country_of_origin: str | None
    price: float | None
    price_band: str | None
    variant_family_id: str | None
    representative_product_name: str | None
    main_benefit_ids: list[str]
    ingredient_ids: list[str]
    brand_concept_ids: list[str]
    category_concept_ids: list[str]
    ingredient_concept_ids: list[str]
    main_benefit_concept_ids: list[str]
    top_bee_attr_ids: list[dict]
    top_keyword_ids: list[dict]
    top_context_ids: list[dict]
    top_concern_pos_ids: list[dict]
    top_concern_neg_ids: list[dict]
    top_tool_ids: list[dict]
    top_comparison_product_ids: list[dict]
    top_coused_product_ids: list[dict]
    last_signal_at: str | None
    review_count_30d: int
    review_count_90d: int
    review_count_all: int
    signal_support_count_all: int
    source_review_count_6m: int | None
    source_review_score_count_6m: int | None
    source_avg_rating_6m: float | None
    source_review_min_date_6m: Any | None
    source_review_max_date_6m: Any | None
    source_review_count_all: int | None
    source_review_score_count_all: int | None
    source_avg_rating_all: float | None
    source_review_min_date_all: Any | None
    source_review_max_date_all: Any | None
    source_review_stats_source: str | None


class ServingUserProfile(TypedDict, total=False):
    """Application-level payload for serving_user_profile rows."""

    user_id: str
    age_band: str | None
    gender: str | None
    skin_type: str | None
    skin_tone: str | None
    preferred_brand_ids: list[dict]
    preferred_category_ids: list[dict]
    preferred_ingredient_ids: list[dict]
    avoided_ingredient_ids: list[dict]
    concern_ids: list[dict]
    goal_ids: list[dict]
    preferred_bee_attr_ids: list[dict]
    preferred_keyword_ids: list[dict]
    preferred_context_ids: list[dict]
    recent_purchase_brand_ids: list[dict]
    repurchase_brand_ids: list[dict]
    repurchase_category_ids: list[dict]
    owned_product_ids: list[dict]
    owned_family_ids: list[dict]
    repurchased_family_ids: list[dict]
