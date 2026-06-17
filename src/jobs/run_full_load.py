"""
Full initial load: Product → User → Review → Aggregate → Serve.

Orchestrates all loaders + pipeline for first-time data population.
Order matters: Product first (concept seed), then User (optional), then Review (needs product_index).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.config_loader import get_kg_mode, load_predicate_contracts
from src.ingest.purchase_ingest import PurchaseEvent, build_product_lookups_from_masters
from src.loaders.relation_loader import load_reviews_from_json
from src.loaders.product_loader import load_products_from_es_records, ProductLoadResult
from src.loaders.product_truth_merge import merge_product_truth
from src.loaders.user_loader import load_users_from_profiles, UserLoadResult
from src.link.product_matcher import ProductIndex
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.wrap.projection_registry import ProjectionRegistry
from src.qa.quarantine_handler import QuarantineHandler
from src.jobs.run_daily_pipeline import run_batch


@dataclass
class FullLoadConfig:
    """Configuration for full initial load."""
    review_json_path: str
    product_es_records: list[dict] | None = None
    product_json_path: str | None = None
    user_profiles: dict[str, dict] | None = None
    max_reviews: int | None = None
    sale_status_filter: str | None = None
    # P0-1 (audit fix): per-user purchase events for OWNS_*/REPURCHASES_* fact building.
    purchase_events_by_user: dict[str, list[PurchaseEvent]] | None = None
    # P0-3 (audit fix): kg_mode override. None → env GRAPHRAPPING_KG_MODE → "off".
    kg_mode: str | None = None
    # Source-grounded review stats keyed by product_id. Values may be parsed
    # Snowflake rows or product_review_stats persistence rows.
    source_review_stats_by_product: dict[str, dict[str, Any]] | None = None


@dataclass
class FullLoadResult:
    """Results from full initial load."""
    product_count: int = 0
    user_count: int = 0
    review_count: int = 0
    signal_count: int = 0
    quarantine_count: int = 0
    serving_product_count: int = 0
    serving_user_count: int = 0
    # P0-1: expose serving content for downstream test observability.
    serving_users: list[dict] = field(default_factory=list)
    serving_products: list[dict] = field(default_factory=list)
    batch_result: dict[str, Any] = field(default_factory=dict)
    # Wave 4 Task 4: expose concept_seeds so DB-materialization path can
    # persist concept_registry (run_batch doesn't see them; they live on the
    # loader output).
    concept_seeds: list[dict] = field(default_factory=list)
    source_review_stats_by_product: dict[str, dict[str, Any]] = field(default_factory=dict)


def run_full_load(config: FullLoadConfig) -> FullLoadResult:
    """Execute full initial data load.

    Order: Product → User → Review → Aggregate → Serve
    """
    result = FullLoadResult()

    # --- Step 1: Load Products ---
    if config.product_es_records:
        product_result = load_products_from_es_records(
            config.product_es_records,
            sale_status_filter=config.sale_status_filter,
        )
    elif config.product_json_path:
        from src.loaders.product_loader import load_products_from_json
        product_result = load_products_from_json(
            config.product_json_path,
            sale_status_filter=config.sale_status_filter,
        )
    else:
        product_result = ProductLoadResult()

    result.product_count = product_result.product_count
    print(f"[1/4] Products loaded: {result.product_count}")

    source_review_stats_by_product = _merge_source_review_stats(
        product_result.product_masters,
        config.source_review_stats_by_product,
    )
    if source_review_stats_by_product:
        for pid, stats in source_review_stats_by_product.items():
            if pid in product_result.product_masters:
                product_result.product_masters[pid] = merge_product_truth(
                    product_result.product_masters[pid],
                    source_review_stats=stats,
                )
        product_result.product_index = ProductIndex.build([
            {
                "product_id": pid,
                "product_name": master.get("product_name", ""),
                "brand_name": master.get("brand_name") or "",
            }
            for pid, master in product_result.product_masters.items()
        ])

    # Build product-id lookups for purchase feature derivation.
    # Contract: raw normalized ids (e.g. brand_id="b1"), not concept IRIs.
    brand_lookup, category_lookup, family_lookup = build_product_lookups_from_masters(
        product_result.product_masters
    )

    # --- Step 2: Load Users (optional) ---
    if config.user_profiles:
        user_result = load_users_from_profiles(
            config.user_profiles,
            purchase_events_by_user=config.purchase_events_by_user,
            brand_lookup=brand_lookup,
            category_lookup=category_lookup,
            family_lookup=family_lookup,
        )
    else:
        user_result = UserLoadResult()

    result.user_count = user_result.user_count
    print(f"[2/4] Users loaded: {result.user_count}")

    # --- Step 3: Load Reviews + Run Pipeline ---
    reviews = load_reviews_from_json(
        config.review_json_path,
        max_count=config.max_reviews,
    )
    print(f"[3/4] Reviews loaded: {len(reviews)}, processing...")

    # Initialize normalizers
    bee_norm = BEENormalizer()
    bee_norm.load_dictionaries()

    rel_canon = RelationCanonicalizer()
    rel_canon.load()

    proj_registry = ProjectionRegistry()
    proj_registry.load()

    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()

    quarantine = QuarantineHandler()

    # Load predicate contracts for operational validation (P0-2).
    predicate_contracts = load_predicate_contracts()

    # Resolve kg_mode (P0-3): arg → env → "off" default.
    kg_mode = get_kg_mode(config.kg_mode)

    # Run batch pipeline.
    # Contract split (P0-1):
    #   load_users_from_profiles(purchase_events_by_user=...) → user-fact build
    #     (OWNS_PRODUCT / OWNS_FAMILY / REPURCHASES_* / RECENTLY_PURCHASED)
    #   run_batch(purchase_events_by_user=...)                → brand-confidence weighting
    # Forward the same dict to BOTH to keep both paths active.
    batch_result = run_batch(
        reviews=reviews,
        source="full_load",
        product_index=product_result.product_index or __import__("src.link.product_matcher", fromlist=["ProductIndex"]).ProductIndex.build([]),
        product_masters=product_result.product_masters,
        concept_links=product_result.concept_links,
        user_masters=user_result.user_masters,
        user_adapted_facts=user_result.user_adapted_facts,
        bee_normalizer=bee_norm,
        relation_canonicalizer=rel_canon,
        projection_registry=proj_registry,
        quarantine=quarantine,
        deriver=deriver,
        predicate_contracts=predicate_contracts,
        purchase_events_by_user=config.purchase_events_by_user,
        kg_mode=kg_mode,
        source_review_stats_by_product=source_review_stats_by_product,
    )

    # Wave 4 Task 4: expose concept_seeds for DB persist.
    result.concept_seeds = product_result.concept_seeds
    result.source_review_stats_by_product = source_review_stats_by_product
    result.review_count = len(reviews)
    result.signal_count = batch_result.get("total_signals", 0)
    result.quarantine_count = batch_result.get("total_quarantined", 0)
    result.serving_products = batch_result.get("serving_products", [])
    result.serving_users = batch_result.get("serving_users", [])
    result.serving_product_count = len(result.serving_products)
    result.serving_user_count = len(result.serving_users)
    result.batch_result = batch_result

    print("[4/4] Pipeline complete:")
    print(f"  Reviews processed: {result.review_count}")
    print(f"  Signals generated: {result.signal_count}")
    print(f"  Quarantined: {result.quarantine_count}")
    print(f"  Serving products: {result.serving_product_count}")
    print(f"  Serving users: {result.serving_user_count}")

    return result


def _merge_source_review_stats(
    product_masters: dict[str, dict],
    configured_stats: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Return source review stats keyed by product_id.

    Snowflake/configured stats win. Product catalog REVIEW_COUNT/REVIEW_SCORE
    are preserved only as explicit fallback source stats, never as graph counts.
    """
    merged: dict[str, dict[str, Any]] = {}
    for pid, stats in (configured_stats or {}).items():
        row = dict(stats)
        if _is_mock_synthetic_source(row):
            continue
        merged[str(pid)] = row
    for pid, master in product_masters.items():
        if pid in merged:
            continue
        if _is_mock_synthetic_source(master):
            continue
        count = master.get("source_review_count")
        score = master.get("source_review_score")
        if count is None and score is None:
            continue
        score_count = int(count or 0) if score is not None else 0
        merged[pid] = {
            "product_id": pid,
            "source_product_id": master.get("source_product_id") or pid,
            "source_channel": master.get("source_channel"),
            "source_key_type": master.get("source_key_type"),
            "source_review_count_6m": 0,
            "source_review_score_count_6m": 0,
            "source_avg_rating_6m": None,
            "source_review_count_all": int(count or 0),
            "source_review_score_count_all": score_count,
            "source_avg_rating_all": score,
            "source": master.get("source_truth_source") or "product_catalog_es",
        }
    return merged


def _is_mock_synthetic_source(master: dict[str, Any]) -> bool:
    """Synthetic mock catalog fields must not become source review stats."""
    source = str(
        master.get("source")
        or master.get("source_review_stats_source")
        or master.get("source_truth_source")
        or ""
    ).strip().lower()
    quality = str(
        master.get("source_truth_quality")
        or master.get("SOURCE_TRUTH_QUALITY")
        or ""
    ).strip().upper()
    return source.startswith("mock") or quality.startswith(("MOCK", "SYNTHETIC"))
