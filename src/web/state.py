"""
DemoState: in-memory state for the demo web UI.

Holds loaded batch results, review bundles, serving profiles,
and quarantine entries for browsing and recommendation testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.db.persist_bundle import ReviewPersistBundle


@dataclass
class DemoState:
    """In-memory state loaded from pipeline run."""
    loaded: bool = False
    source: str = ""

    # Raw inputs
    review_count: int = 0
    product_count: int = 0
    user_count: int = 0

    # Per-review bundles (keyed by review_id)
    bundles: dict[str, ReviewPersistBundle] = field(default_factory=dict)

    # Batch results
    batch_result: dict[str, Any] = field(default_factory=dict)

    # Serving profiles
    serving_products: list[dict] = field(default_factory=list)
    serving_users: list[dict] = field(default_factory=list)

    # Product masters + concept links (for recommendation)
    product_masters: dict[str, dict] = field(default_factory=dict)
    concept_links: dict[str, list[dict]] = field(default_factory=dict)
    user_adapted_facts: dict[str, dict] = field(default_factory=dict)

    # Aggregated stats
    signal_family_counts: dict[str, int] = field(default_factory=dict)
    relation_type_counts: dict[str, int] = field(default_factory=dict)
    bee_attr_counts: dict[str, int] = field(default_factory=dict)
    quarantine_stats: dict[str, int] = field(default_factory=dict)

    # All quarantine entries
    quarantine_entries: list[dict] = field(default_factory=list)

    # Per-product signal index (for graph API hierarchy)
    product_signals: dict[str, list[dict]] = field(default_factory=dict)


# Global singleton
demo_state = DemoState()


def load_demo_data(
    review_json_path: str,
    product_es_records: list[dict],
    user_profiles: dict[str, dict],
    max_reviews: int = 100,
    source: str = "demo",
) -> DemoState:
    """Load data and run pipeline, populating demo_state."""
    from src.loaders.relation_loader import load_reviews_from_json
    from src.loaders.product_loader import load_products_from_es_records
    from src.loaders.user_loader import load_users_from_profiles
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
    from src.wrap.projection_registry import ProjectionRegistry
    from src.qa.quarantine_handler import QuarantineHandler
    from src.jobs.run_daily_pipeline import process_review, bundle_to_result_dict, run_batch
    from src.common.text_normalize import normalize_text

    global demo_state
    # Reset by clearing attributes (not replacing object — preserves import references)
    demo_state.__init__()
    demo_state.source = source

    # Load products
    product_result = load_products_from_es_records(product_es_records)
    # Add aliases for prod_nm = product_id matching (current data format)
    for record in product_es_records:
        pid = record["ONLINE_PROD_SERIAL_NUMBER"]
        brand = record.get("BRAND_NAME", "")
        alias_key = f"{normalize_text(brand)}|{normalize_text(pid)}"
        product_result.product_index.add_alias(alias_key, pid)

    demo_state.product_masters = product_result.product_masters
    demo_state.concept_links = product_result.concept_links
    demo_state.product_count = product_result.product_count

    # Load users
    user_result = load_users_from_profiles(user_profiles)
    demo_state.user_count = user_result.user_count
    demo_state.user_adapted_facts = user_result.user_adapted_facts

    # Load reviews
    reviews = load_reviews_from_json(review_json_path, max_count=max_reviews)
    demo_state.review_count = len(reviews)

    # Init normalizers
    bee_norm = BEENormalizer()
    bee_norm.load_dictionaries()
    rel_canon = RelationCanonicalizer()
    rel_canon.load()
    proj_registry = ProjectionRegistry()
    proj_registry.load()
    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()

    quarantine = QuarantineHandler()

    # Run batch
    batch_result = run_batch(
        reviews=reviews, source=source,
        product_index=product_result.product_index,
        product_masters=product_result.product_masters,
        concept_links=product_result.concept_links,
        user_masters=user_result.user_masters,
        user_adapted_facts=user_result.user_adapted_facts,
        bee_normalizer=bee_norm, relation_canonicalizer=rel_canon,
        projection_registry=proj_registry, quarantine=quarantine,
        deriver=deriver,
        kg_mode="on",  # Use KG pipeline for BEE/REL processing
    )

    demo_state.batch_result = batch_result
    demo_state.serving_products = batch_result.get("serving_products", [])
    demo_state.serving_users = batch_result.get("serving_users", [])

    # Collect bundles and stats
    for result in batch_result.get("review_results", []):
        rid = result.get("review_id", "")
        demo_state.bundles[rid] = result

    # Per-product signal index (for hierarchical graph)
    for result in batch_result.get("review_results", []):
        for sig in result.get("signals", []):
            pid = sig.get("target_product_id")
            if pid:
                demo_state.product_signals.setdefault(pid, []).append(sig)

    # Signal family distribution
    for result in batch_result.get("review_results", []):
        for sig in result.get("signals", []):
            fam = sig.get("signal_family", "unknown")
            demo_state.signal_family_counts[fam] = demo_state.signal_family_counts.get(fam, 0) + 1

    # Relation / BEE stats from raw reviews
    for review in reviews:
        for rel in review.relation:
            rt = rel.get("relation", "")
            demo_state.relation_type_counts[rt] = demo_state.relation_type_counts.get(rt, 0) + 1
        for bee in review.bee:
            ba = bee.get("entity_group", "")
            demo_state.bee_attr_counts[ba] = demo_state.bee_attr_counts.get(ba, 0) + 1

    # Quarantine
    demo_state.quarantine_stats = quarantine.pending_by_table()
    demo_state.quarantine_entries = [
        {"table": e.table, **e.data} for e in quarantine.flush()
    ]

    demo_state.loaded = True
    return demo_state
