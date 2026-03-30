"""
Persistence orchestrator: writes ReviewPersistBundle to DB via repositories.

Transaction boundary: per-review atomic (L1→L2→L2.5+QA).
L3 aggregate is separate batch call.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from src.db.unit_of_work import UnitOfWork
from src.db.persist_bundle import ReviewPersistBundle
from src.db.repos import review_repo, canonical_repo, signal_repo, quarantine_repo


async def persist_review_bundle(pool: asyncpg.Pool, bundle: ReviewPersistBundle) -> dict[str, Any]:
    """Persist a single review bundle atomically.

    L1 (raw) + L2 (canonical) + L2.5 (signals) + QA (quarantine) in one transaction.
    Returns stats dict.
    """
    async with UnitOfWork(pool) as uow:
        # L1: review_raw + history (versioned upsert)
        review_version = await review_repo.upsert_review_raw(uow, bundle.review_raw)

        # L1: review_catalog_link
        if bundle.review_catalog_link:
            await review_repo.upsert_review_catalog_link(uow, bundle.review_catalog_link)

        # L1: child rows (append-only with review_version)
        await review_repo.batch_insert_ner_raw(uow, bundle.ner_rows, review_version)
        await review_repo.batch_insert_bee_raw(uow, bundle.bee_rows, review_version)
        await review_repo.batch_insert_rel_raw(uow, bundle.rel_rows, review_version)

        # L2: canonical entities
        for entity in bundle.canonical_entities:
            await canonical_repo.upsert_canonical_entity(uow, entity)

        # L2: canonical facts (diff-based for reprocess)
        fact_stats = await canonical_repo.diff_upsert_facts(
            uow, bundle.review_id, bundle.canonical_facts,
        )

        # L2.5: signals + evidence (full-replace per review)
        dirty_from_signals = await signal_repo.replace_signals_for_review(
            uow, bundle.review_id,
            bundle.wrapped_signals, bundle.signal_evidence_rows,
        )

        # Track dirty products (from signals + any relink)
        bundle.dirty_product_ids.update(dirty_from_signals)

        # QA: quarantine entries
        q_count = await quarantine_repo.flush_quarantine(uow, bundle.quarantine_entries)

    return {
        "review_id": bundle.review_id,
        "review_version": review_version,
        "fact_stats": fact_stats,
        "signal_count": len(bundle.wrapped_signals),
        "quarantine_count": q_count,
        "dirty_product_ids": list(bundle.dirty_product_ids),
    }


async def persist_aggregates(
    pool: asyncpg.Pool,
    agg_rows: list[dict],
    serving_products: list[dict],
    serving_users: list[dict],
    user_pref_rows: list[dict],
) -> dict[str, int]:
    """Persist Layer 3 aggregates and serving profiles.

    Separate transaction from per-review persistence.
    """
    from src.db.repos import mart_repo

    async with UnitOfWork(pool) as uow:
        for row in agg_rows:
            await mart_repo.upsert_agg_product_signal(uow, row)

        for row in user_pref_rows:
            await mart_repo.upsert_agg_user_preference(uow, row)

        for row in serving_products:
            await mart_repo.upsert_serving_product_profile(uow, row)

        for row in serving_users:
            await mart_repo.upsert_serving_user_profile(uow, row)

    return {
        "agg_rows": len(agg_rows),
        "serving_products": len(serving_products),
        "serving_users": len(serving_users),
        "user_prefs": len(user_pref_rows),
    }
