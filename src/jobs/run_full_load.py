"""
Full initial load: Product → User → Review → Aggregate → Serve.

Orchestrates all loaders + pipeline for first-time data population.
Order matters: Product first (concept seed), then User (optional), then Review (needs product_index).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.loaders.relation_loader import load_reviews_from_json
from src.loaders.product_loader import load_products_from_es_records, ProductLoadResult
from src.loaders.user_loader import load_users_from_profiles, UserLoadResult
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
    sale_status_filter: str = "판매중"


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

    # --- Step 2: Load Users (optional) ---
    if config.user_profiles:
        user_result = load_users_from_profiles(config.user_profiles)
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

    # Run batch pipeline
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
    )

    result.review_count = len(reviews)
    result.signal_count = batch_result.get("total_signals", 0)
    result.quarantine_count = batch_result.get("total_quarantined", 0)
    result.serving_product_count = len(batch_result.get("serving_products", []))
    result.serving_user_count = len(batch_result.get("serving_users", []))

    print("[4/4] Pipeline complete:")
    print(f"  Reviews processed: {result.review_count}")
    print(f"  Signals generated: {result.signal_count}")
    print(f"  Quarantined: {result.quarantine_count}")
    print(f"  Serving products: {result.serving_product_count}")
    print(f"  Serving users: {result.serving_user_count}")

    return result
