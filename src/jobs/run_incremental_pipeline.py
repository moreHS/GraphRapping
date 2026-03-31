"""
Incremental pipeline: processes only reviews changed since last run.

Uses (updated_at, review_id) total-order cursor from pipeline_run watermark.
Handles: new reviews, modified reviews (version bump), tombstones.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

import logging

from src.db.unit_of_work import UnitOfWork
from src.db.persist import persist_review_bundle, persist_aggregates
from src.db.persist_bundle import ReviewPersistBundle
from src.db.repos.review_repo import load_full_review_snapshot
from src.ingest.review_ingest import RawReviewRecord, ingest_review
from src.jobs.run_daily_pipeline import process_review

logger = logging.getLogger(__name__)
from src.link.product_matcher import ProductIndex
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.wrap.projection_registry import ProjectionRegistry
from src.qa.quarantine_handler import QuarantineHandler
from src.mart.aggregate_product_signals import aggregate_product_signals
from src.mart.build_serving_views import build_serving_product_profile


async def get_last_watermark(pool: asyncpg.Pool) -> tuple[datetime | None, str | None]:
    """Get last successful watermark from pipeline_run."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT watermark_ts, watermark_rid
            FROM pipeline_run
            WHERE status = 'COMPLETED'
            ORDER BY completed_at DESC
            LIMIT 1
        """)
        if row:
            return row["watermark_ts"], row["watermark_rid"]
        return None, None


async def fetch_changed_reviews(
    pool: asyncpg.Pool,
    watermark_ts: datetime | None,
    watermark_rid: str | None,
    run_start: datetime,
    limit: int = 1000,
) -> list[dict]:
    """Fetch reviews changed since last watermark using total-order cursor."""
    async with pool.acquire() as conn:
        if watermark_ts and watermark_rid:
            rows = await conn.fetch("""
                SELECT * FROM review_raw
                WHERE (updated_at, review_id) > ($1, $2)
                  AND updated_at <= $3
                ORDER BY updated_at, review_id
                LIMIT $4
            """, watermark_ts, watermark_rid, run_start, limit)
        else:
            # First run: process everything
            rows = await conn.fetch("""
                SELECT * FROM review_raw
                WHERE updated_at <= $1
                ORDER BY updated_at, review_id
                LIMIT $2
            """, run_start, limit)
        return [dict(r) for r in rows]


async def handle_tombstone(
    pool: asyncpg.Pool,
    review_id: str,
    matched_product_id: str | None,
) -> set[str]:
    """Handle tombstoned review: close facts, delete signals, return dirty products."""
    dirty = set()
    if matched_product_id:
        dirty.add(matched_product_id)

    async with UnitOfWork(pool) as uow:
        # Close canonical facts
        await uow.execute("""
            UPDATE canonical_fact SET valid_to = $1
            WHERE review_id = $2 AND valid_to IS NULL
        """, uow.as_of_ts, review_id)

        # Get signal products before delete
        rows = await uow.fetch(
            "SELECT DISTINCT target_product_id FROM wrapped_signal WHERE review_id = $1",
            review_id,
        )
        for r in rows:
            if r["target_product_id"]:
                dirty.add(r["target_product_id"])

        # Delete signals + evidence
        await uow.execute("""
            DELETE FROM signal_evidence WHERE signal_id IN (
                SELECT signal_id FROM wrapped_signal WHERE review_id = $1
            )
        """, review_id)
        await uow.execute("DELETE FROM wrapped_signal WHERE review_id = $1", review_id)

    return dirty


async def start_pipeline_run(pool: asyncpg.Pool, run_type: str = "INCREMENTAL") -> int:
    """Create a pipeline_run record. Returns run_id."""
    async with pool.acquire() as conn:
        return await conn.fetchval("""
            INSERT INTO pipeline_run (run_type, started_at, status)
            VALUES ($1, $2, 'RUNNING')
            RETURNING run_id
        """, run_type, datetime.now(timezone.utc))


async def complete_pipeline_run(
    pool: asyncpg.Pool,
    run_id: int,
    watermark_ts: datetime,
    watermark_rid: str,
    review_count: int = 0,
    signal_count: int = 0,
    quarantine_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Mark pipeline_run as completed/failed."""
    status = "FAILED" if error_message else "COMPLETED"
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE pipeline_run SET
                completed_at = $2, status = $3,
                watermark_ts = $4, watermark_rid = $5,
                review_count = $6, signal_count = $7,
                quarantine_count = $8, error_message = $9
            WHERE run_id = $1
        """,
            run_id, datetime.now(timezone.utc), status,
            watermark_ts, watermark_rid,
            review_count, signal_count, quarantine_count, error_message,
        )


async def run_incremental(
    pool: asyncpg.Pool,
    product_index: ProductIndex,
    product_masters: dict[str, dict],
    concept_links: dict[str, list[dict]],
    bee_normalizer: BEENormalizer,
    relation_canonicalizer: RelationCanonicalizer,
    projection_registry: ProjectionRegistry,
    deriver: ToolConcernSegmentDeriver | None = None,
    predicate_contracts: dict | None = None,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Run incremental pipeline: process only changed reviews since last watermark."""
    run_start = datetime.now(timezone.utc)
    run_id = await start_pipeline_run(pool, "INCREMENTAL")

    try:
        # Get watermark
        wm_ts, wm_rid = await get_last_watermark(pool)

        # Fetch changed reviews
        changed = await fetch_changed_reviews(pool, wm_ts, wm_rid, run_start, batch_size)
        if not changed:
            await complete_pipeline_run(pool, run_id, wm_ts or run_start, wm_rid or "", 0, 0, 0)
            return {"run_id": run_id, "status": "COMPLETED", "review_count": 0}

        all_dirty_products: set[str] = set()
        total_signals = 0
        total_quarantined = 0

        for review_row in changed:
            quarantine = QuarantineHandler()

            if not review_row.get("is_active", True):
                # Tombstone
                dirty = await handle_tombstone(
                    pool, review_row["review_id"],
                    review_row.get("matched_product_id"),
                )
                all_dirty_products.update(dirty)
                continue

            # Load full raw snapshot (ner/bee/rel child rows) from DB
            async with UnitOfWork(pool) as snapshot_uow:
                snapshot, has_child_rows = await load_full_review_snapshot(
                    snapshot_uow, review_row["review_id"],
                )

            if not snapshot or not has_child_rows:
                logger.warning(
                    "Skip reprocessing: empty child rows for review %s (has_child=%s)",
                    review_row["review_id"], has_child_rows,
                )
                continue

            record = RawReviewRecord(
                brnd_nm=snapshot.get("brnd_nm", ""),
                prod_nm=snapshot.get("prod_nm", ""),
                text=snapshot.get("text", ""),
                clct_site_nm=snapshot.get("clct_site_nm", ""),
                source_review_key=snapshot.get("source_review_key"),
                created_at=str(review_row["event_time_utc"]) if review_row.get("event_time_utc") else None,
                ner=snapshot.get("ner", []),
                bee=snapshot.get("bee", []),
                relation=snapshot.get("relation", []),
            )

            # Process review with full raw data
            result = process_review(
                record=record,
                source=review_row.get("source", ""),
                product_index=product_index,
                bee_normalizer=bee_normalizer,
                relation_canonicalizer=relation_canonicalizer,
                projection_registry=projection_registry,
                quarantine=quarantine,
                deriver=deriver,
                predicate_contracts=predicate_contracts,
            )

            total_signals += result.get("signal_count", 0)
            total_quarantined += quarantine.pending_count

            if result.get("matched_product_id"):
                all_dirty_products.add(result["matched_product_id"])

            # Check for previous match (relink case)
            async with pool.acquire() as conn:
                prev_link = await conn.fetchrow(
                    "SELECT matched_product_id FROM review_catalog_link WHERE review_id = $1",
                    review_row["review_id"],
                )
                if prev_link and prev_link["matched_product_id"]:
                    all_dirty_products.add(prev_link["matched_product_id"])

        # Re-aggregate dirty products
        for pid in all_dirty_products:
            if pid in product_masters:
                master = product_masters[pid]
                links = concept_links.get(f"product:{pid}", [])
                # Full re-aggregate for this product from all signals in DB
                async with pool.acquire() as conn:
                    signal_rows = await conn.fetch("""
                        SELECT * FROM wrapped_signal WHERE target_product_id = $1
                    """, pid)
                    signals_dicts = [dict(r) for r in signal_rows]

                agg = aggregate_product_signals(signals_dicts)
                profile = build_serving_product_profile(master, [_agg_to_dict(a) for a in agg], concept_links=links)

                # Persist
                from src.db.repos import mart_repo
                async with UnitOfWork(pool) as uow:
                    for a in agg:
                        await mart_repo.upsert_agg_product_signal(uow, _agg_to_dict(a))
                    await mart_repo.upsert_serving_product_profile(uow, profile)

        # Update watermark (last processed review)
        last_review = changed[-1]
        final_wm_ts = last_review.get("updated_at", run_start)
        final_wm_rid = last_review.get("review_id", "")

        await complete_pipeline_run(
            pool, run_id, final_wm_ts, final_wm_rid,
            len(changed), total_signals, total_quarantined,
        )

        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "review_count": len(changed),
            "signal_count": total_signals,
            "dirty_product_count": len(all_dirty_products),
        }

    except Exception as e:
        await complete_pipeline_run(pool, run_id, wm_ts or run_start, wm_rid or "",
                                     error_message=str(e))
        raise


def _agg_to_dict(agg) -> dict:
    return {
        "target_product_id": agg.target_product_id,
        "canonical_edge_type": agg.canonical_edge_type,
        "dst_node_type": agg.dst_node_type,
        "dst_node_id": agg.dst_node_id,
        "window_type": agg.window_type,
        "review_cnt": agg.review_cnt,
        "pos_cnt": agg.pos_cnt,
        "neg_cnt": agg.neg_cnt,
        "score": agg.score,
        "support_count": agg.support_count,
        "last_seen_at": agg.last_seen_at,
    }
