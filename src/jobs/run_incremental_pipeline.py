"""
Incremental pipeline: processes only reviews changed since last run.

Uses (updated_at, review_id) total-order cursor from pipeline_run watermark.
Handles: new reviews, modified reviews (version bump), tombstones.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

from src.common.config_loader import get_kg_mode, load_predicate_contracts
from src.db.persist import persist_review_bundle
from src.db.repos import product_repo
from src.db.repos.review_repo import load_full_review_snapshot
from src.db.unit_of_work import UnitOfWork
from src.ingest.review_ingest import RawReviewRecord
from src.jobs.run_daily_pipeline import process_review
from src.link.product_matcher import ProductIndex
from src.mart.aggregate_product_signals import aggregate_product_signals
from src.mart.build_serving_views import (
    build_serving_product_profile,
    build_serving_user_profile,
)
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.qa.quarantine_handler import QuarantineHandler
from src.wrap.projection_registry import ProjectionRegistry

logger = logging.getLogger(__name__)


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
    """Handle tombstoned review: close facts, delete signals, return dirty products.

    P0-4: dirty set now includes comparison/co-use dst_ids in addition to
    target_product_id via signal_repo.get_dirty_product_ids_for_review(), so
    aggregates of compared/co-used products are refreshed when the source review
    is removed.
    """
    from src.db.repos import signal_repo

    dirty: set[str] = set()
    if matched_product_id:
        dirty.add(matched_product_id)

    async with UnitOfWork(pool) as uow:
        # Close canonical facts
        await uow.execute("""
            UPDATE canonical_fact SET valid_to = $1
            WHERE review_id = $2 AND valid_to IS NULL
        """, uow.as_of_ts, review_id)

        # P0-4: helper unions target + comparison + co-use dirty products.
        # Must run BEFORE the DELETE so OLD signals are visible.
        helper_dirty = await signal_repo.get_dirty_product_ids_for_review(uow, review_id)
        dirty.update(helper_dirty)

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
        run_id = await conn.fetchval("""
            INSERT INTO pipeline_run (run_type, started_at, status)
            VALUES ($1, $2, 'RUNNING')
            RETURNING run_id
        """, run_type, datetime.now(timezone.utc))
        return int(run_id)


def _compute_watermark(
    *,
    changed: list[dict[str, Any]],
    skipped_reviews: set[str],
    last_processed_review: dict[str, Any] | None,
    previous_wm_ts: datetime | None,
    previous_wm_rid: str | None,
) -> tuple[datetime | None, str | None]:
    """Decide the watermark to record for this incremental run.

    Policy (P0-5, early-stop):
      - If any review was skipped, find the EARLIEST skipped cursor
        (updated_at, review_id). Choose the max cursor strictly less than that
        from successfully-processed reviews. If none exists, **preserve previous
        watermark exactly** (may be (None, None) on first-run with no safe
        progress).
      - If no skipped reviews, fall through to existing behavior:
        last_processed_review or changed[-1].

    Returns (final_wm_ts, final_wm_rid). Caller must accept None (no fake
    watermark like (run_start, "")).
    """
    if not skipped_reviews:
        wm_source = last_processed_review or changed[-1]
        return (
            wm_source.get("updated_at"),
            wm_source.get("review_id", ""),
        )

    skipped_rows = [r for r in changed if r["review_id"] in skipped_reviews]
    if not skipped_rows:
        # Defensive: skipped_reviews referenced an id not in changed (invariant
        # violation). Fall back to no-skip behavior.
        wm_source = last_processed_review or changed[-1]
        return (wm_source.get("updated_at"), wm_source.get("review_id", ""))

    earliest_skipped = min(
        skipped_rows,
        key=lambda r: (r.get("updated_at"), r.get("review_id", "")),
    )
    earliest_cursor = (
        earliest_skipped.get("updated_at"),
        earliest_skipped.get("review_id", ""),
    )

    candidates = [
        r for r in changed
        if r["review_id"] not in skipped_reviews
        and (r.get("updated_at"), r.get("review_id", "")) < earliest_cursor
    ]
    if not candidates:
        # No safe forward progress — preserve previous watermark exactly.
        return (previous_wm_ts, previous_wm_rid)

    wm_source = max(
        candidates,
        key=lambda r: (r.get("updated_at"), r.get("review_id", "")),
    )
    return (wm_source.get("updated_at"), wm_source.get("review_id", ""))


async def complete_pipeline_run(
    pool: asyncpg.Pool,
    run_id: int,
    watermark_ts: datetime | None,
    watermark_rid: str | None,
    review_count: int = 0,
    signal_count: int = 0,
    quarantine_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Mark pipeline_run as completed/failed.

    P0-5: watermark_ts/watermark_rid may be None when this run made no safe
    forward progress (e.g. first-run with all reviews skipped). asyncpg writes
    NULL to the nullable columns.
    """
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


def _unique_strs(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        if value is None:
            continue
        item = str(value)
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _cleanup_counts_only(cleanup_result: dict[str, Any]) -> dict[str, int]:
    def _as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "product_signals": _as_int(cleanup_result.get("product_signals")),
        "user_preferences": _as_int(cleanup_result.get("user_preferences")),
    }


async def _attach_review_ids_from_wrapped_signal(
    uow: UnitOfWork,
    agg_rows: list[dict[str, Any]],
) -> None:
    """Restore transient review_ids needed for serving review_count fields."""
    base_q = """
        SELECT DISTINCT NULLIF(review_id, '') AS review_id
        FROM wrapped_signal
        WHERE target_product_id = $1
          AND edge_type = $2
          AND dst_type = $3
          AND dst_id = $4
          AND signal_family != 'CATALOG_VALIDATION'
          AND review_id IS NOT NULL
          AND review_id <> ''
    """
    for row in agg_rows:
        query = base_q
        if row.get("window_type") == "30d":
            query += "\n          AND window_ts >= now() - interval '30 days'"
        elif row.get("window_type") == "90d":
            query += "\n          AND window_ts >= now() - interval '90 days'"

        review_rows = await uow.fetch(
            query,
            row.get("target_product_id"),
            row.get("canonical_edge_type"),
            row.get("dst_node_type"),
            row.get("dst_node_id"),
        )
        row["review_ids"] = [
            review_row["review_id"]
            for review_row in review_rows
            if review_row["review_id"]
        ]


async def _load_source_review_stats_for_product(
    uow: UnitOfWork,
    product_id: str,
    master: dict[str, Any],
) -> dict[str, Any] | None:
    """Load persisted source stats for a serving-profile rebuild."""
    return await product_repo.load_product_review_stats(
        uow,
        product_id,
        master.get("source_channel"),
        master.get("source_key_type"),
    )


async def _load_product_master_for_rebuild(
    uow: UnitOfWork,
    product_id: str,
    product_masters: dict[str, dict],
) -> dict[str, Any] | None:
    """Load DB-grounded product truth for serving rebuilds.

    Incremental callers may carry stale in-memory masters from an older
    process. Once product_master exists in DB, it is the source of serving
    truth; the caller map is only a compatibility fallback.
    """
    db_master = await product_repo.load_product_master(uow, product_id)
    if db_master is not None:
        return db_master
    return product_masters.get(product_id)


async def _rebuild_serving_profiles_after_cleanup(
    uow: UnitOfWork,
    cleanup_result: dict[str, Any],
    product_masters: dict[str, dict],
    concept_links: dict[str, list[dict]],
) -> dict[str, int]:
    """Rebuild serving rows whose backing aggregate rows were soft-deleted."""
    product_ids = _unique_strs(cleanup_result.get("product_ids"))
    user_ids = _unique_strs(cleanup_result.get("user_ids"))
    if not product_ids and not user_ids:
        return {"serving_products": 0, "serving_users": 0}

    from src.db.repos import mart_repo

    rebuilt_products = 0
    rebuilt_users = 0
    for product_id in product_ids:
        master = await _load_product_master_for_rebuild(uow, product_id, product_masters)
        if not master:
            continue
        agg_rows = [
            dict(row)
            for row in await uow.fetch("""
                SELECT *
                FROM agg_product_signal
                WHERE target_product_id = $1
                  AND is_active = true
                ORDER BY window_type, canonical_edge_type, dst_node_id
            """, product_id)
        ]
        await _attach_review_ids_from_wrapped_signal(uow, agg_rows)
        links = concept_links.get(f"product:{product_id}", concept_links.get(product_id, []))
        source_stats = await _load_source_review_stats_for_product(uow, product_id, master)
        profile = build_serving_product_profile(
            master,
            agg_rows,
            concept_links=links,
            source_review_stats=source_stats,
        )
        await mart_repo.upsert_serving_product_profile(uow, profile)
        rebuilt_products += 1

    for user_id in user_ids:
        user_master = await uow.fetchrow(
            "SELECT * FROM user_master WHERE user_id = $1",
            user_id,
        )
        if not user_master:
            continue
        preference_rows = [
            dict(row)
            for row in await uow.fetch("""
                SELECT *
                FROM agg_user_preference
                WHERE user_id = $1
                  AND is_active = true
                ORDER BY preference_edge_type, dst_node_id
            """, user_id)
        ]
        profile = build_serving_user_profile(dict(user_master), preference_rows)
        await mart_repo.upsert_serving_user_profile(uow, profile)
        rebuilt_users += 1

    return {"serving_products": rebuilt_products, "serving_users": rebuilt_users}


async def _maybe_run_stale_cleanup(
    pool: asyncpg.Pool,
    product_masters: dict[str, dict],
    concept_links: dict[str, list[dict]],
) -> dict[str, int] | None:
    """P3-8 / Wave 3.8: opt-in soft-delete cleanup for stale aggregate rows.

    Gated by env `GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`. Threshold comes from
    `GRAPHRAPPING_AGG_CLEANUP_DAYS` (default 90). Invalid int → warn + 90.

    Called from BOTH exit paths of `run_incremental` so stale rows can be
    trimmed even on no-change runs.

    Returns the per-table count dict, or None if disabled.
    """
    if os.environ.get("GRAPHRAPPING_AGG_CLEANUP_ENABLED") != "1":
        return None
    threshold_raw = os.environ.get("GRAPHRAPPING_AGG_CLEANUP_DAYS", "90")
    try:
        threshold_days = int(threshold_raw)
        if threshold_days <= 0:
            raise ValueError("threshold_days must be > 0")
    except ValueError:
        logger.warning(
            "Invalid GRAPHRAPPING_AGG_CLEANUP_DAYS=%r — falling back to 90",
            threshold_raw,
        )
        threshold_days = 90

    from src.db.repos import mart_repo

    async with UnitOfWork(pool) as uow:
        cleanup_result = await mart_repo.mark_stale_agg_signals_inactive(
            uow,
            threshold_days=threshold_days,
            include_ids=True,
        )
        rebuild_counts = await _rebuild_serving_profiles_after_cleanup(
            uow,
            cleanup_result,
            product_masters,
            concept_links,
        )
    counts = _cleanup_counts_only(cleanup_result)
    logger.info(
        "Stale agg cleanup: product_signals=%s user_preferences=%s "
        "rebuilt_serving_products=%s rebuilt_serving_users=%s (threshold=%sd)",
        counts.get("product_signals"), counts.get("user_preferences"),
        rebuild_counts.get("serving_products"), rebuild_counts.get("serving_users"),
        threshold_days,
    )
    return counts


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
    *,
    kg_mode: str | None = None,
) -> dict[str, Any]:
    """Run incremental pipeline: process only changed reviews since last watermark.

    P0-2: predicate_contracts default-loads when caller passes None.
    P0-3: kg_mode resolves arg → env GRAPHRAPPING_KG_MODE → "off".
    """
    if predicate_contracts is None:
        predicate_contracts = load_predicate_contracts()
    kg_mode = get_kg_mode(kg_mode)

    run_start = datetime.now(timezone.utc)
    run_id = await start_pipeline_run(pool, "INCREMENTAL")

    try:
        # Get watermark
        wm_ts, wm_rid = await get_last_watermark(pool)

        # Fetch changed reviews
        changed = await fetch_changed_reviews(pool, wm_ts, wm_rid, run_start, batch_size)
        if not changed:
            # P3-8 (Wave 3.8): still trim stale aggregates on quiet runs.
            cleanup_counts = await _maybe_run_stale_cleanup(
                pool,
                product_masters,
                concept_links,
            )
            await complete_pipeline_run(pool, run_id, wm_ts, wm_rid, 0, 0, 0)
            return {
                "run_id": run_id,
                "status": "COMPLETED",
                "review_count": 0,
                "signal_count": 0,
                "dirty_product_count": 0,
                "skipped_count": 0,
                "watermark": {"ts": wm_ts, "review_id": wm_rid},
                "cleanup_counts": cleanup_counts,
            }

        all_dirty_products: set[str] = set()
        total_signals = 0
        total_quarantined = 0
        skipped_reviews: set[str] = set()
        last_processed_review: dict | None = None

        for review_row in changed:
            quarantine = QuarantineHandler()

            if not review_row.get("is_active", True):
                # Tombstone
                dirty = await handle_tombstone(
                    pool, review_row["review_id"],
                    review_row.get("matched_product_id"),
                )
                all_dirty_products.update(dirty)
                last_processed_review = review_row
                continue

            # Load full raw snapshot (ner/bee/rel child rows) from DB
            async with UnitOfWork(pool) as snapshot_uow:
                snapshot, has_child_rows = await load_full_review_snapshot(
                    snapshot_uow, review_row["review_id"],
                )

            if not snapshot or not has_child_rows:
                logger.error(
                    "Skip reprocessing: empty child rows for review %s (has_child=%s) — will NOT advance watermark past this",
                    review_row["review_id"], has_child_rows,
                )
                skipped_reviews.add(review_row["review_id"])
                continue

            record = RawReviewRecord(
                brnd_nm=snapshot.get("brnd_nm", ""),
                prod_nm=snapshot.get("prod_nm", ""),
                text=snapshot.get("text", ""),
                clct_site_nm=snapshot.get("clct_site_nm", ""),
                source_review_key=snapshot.get("source_review_key"),
                source_product_id=snapshot.get("source_product_id"),
                source_channel=snapshot.get("source_channel"),
                source_key_type=snapshot.get("source_key_type"),
                source_rating=snapshot.get("source_rating"),
                created_at=str(review_row["event_time_utc"]) if review_row.get("event_time_utc") else None,
                ner=snapshot.get("ner", []),
                bee=snapshot.get("bee", []),
                relation=snapshot.get("relation", []),
            )

            # Process review with full raw data
            bundle = process_review(
                record=record,
                source=review_row.get("source", ""),
                product_index=product_index,
                bee_normalizer=bee_normalizer,
                relation_canonicalizer=relation_canonicalizer,
                projection_registry=projection_registry,
                quarantine=quarantine,
                deriver=deriver,
                predicate_contracts=predicate_contracts,
                kg_mode=kg_mode,
            )

            # Check for previous match (relink case)
            async with pool.acquire() as conn:
                prev_link = await conn.fetchrow(
                    "SELECT matched_product_id FROM review_catalog_link WHERE review_id = $1",
                    review_row["review_id"],
                )

            persist_stats = await persist_review_bundle(pool, bundle)

            total_signals += persist_stats.get("signal_count", len(bundle.wrapped_signals))
            total_quarantined += persist_stats.get("quarantine_count", len(bundle.quarantine_entries))
            last_processed_review = review_row  # Track for watermark only after successful persistence.

            if bundle.matched_product_id:
                all_dirty_products.add(bundle.matched_product_id)
            all_dirty_products.update(bundle.dirty_product_ids)
            all_dirty_products.update(persist_stats.get("dirty_product_ids", []))

            if prev_link and prev_link["matched_product_id"]:
                all_dirty_products.add(prev_link["matched_product_id"])

        # Re-aggregate dirty products
        for pid in all_dirty_products:
            # Full re-aggregate for this product from all signals in DB
            async with pool.acquire() as conn:
                signal_rows = await conn.fetch("""
                    SELECT * FROM wrapped_signal WHERE target_product_id = $1
                """, pid)
                signals_dicts = [dict(r) for r in signal_rows]

            agg = aggregate_product_signals(signals_dicts)

            # Persist
            from src.db.repos import mart_repo
            async with UnitOfWork(pool) as uow:
                master = await _load_product_master_for_rebuild(uow, pid, product_masters)
                if not master:
                    continue
                links = concept_links.get(f"product:{pid}", [])
                for a in agg:
                    await mart_repo.upsert_agg_product_signal(uow, _agg_to_dict(a))
                source_stats = await _load_source_review_stats_for_product(uow, pid, master)
                profile = build_serving_product_profile(
                    master,
                    [_agg_to_dict(a) for a in agg],
                    concept_links=links,
                    source_review_stats=source_stats,
                )
                await mart_repo.upsert_serving_product_profile(uow, profile)

        # P0-5: Watermark — early-stop at earliest skipped cursor so a
        # skipped review is NEVER passed by the watermark.
        final_wm_ts, final_wm_rid = _compute_watermark(
            changed=changed,
            skipped_reviews=skipped_reviews,
            last_processed_review=last_processed_review,
            previous_wm_ts=wm_ts,
            previous_wm_rid=wm_rid,
        )
        if skipped_reviews:
            logger.warning(
                "Skipped %d reviews with empty child rows; watermark held at "
                "(%s, %s): %s",
                len(skipped_reviews), final_wm_ts, final_wm_rid,
                sorted(skipped_reviews),
            )

        # P3-8 (Wave 3.8): trim stale aggregates after the normal flow too.
        cleanup_counts = await _maybe_run_stale_cleanup(
            pool,
            product_masters,
            concept_links,
        )

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
            "skipped_count": len(skipped_reviews),
            "watermark": {"ts": final_wm_ts, "review_id": final_wm_rid},
            "cleanup_counts": cleanup_counts,
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
        "neu_cnt": agg.neu_cnt,
        "score": agg.score,
        "support_count": agg.support_count,
        "recent_score": agg.recent_score,
        "recent_support_count": agg.recent_support_count,
        "window_start": agg.window_start,
        "window_end": agg.window_end,
        "evidence_sample": agg.evidence_sample,
        "distinct_review_count": agg.distinct_review_count,
        "avg_confidence": agg.avg_confidence,
        "synthetic_ratio": agg.synthetic_ratio,
        "corpus_weight": agg.corpus_weight,
        "last_seen_at": agg.last_seen_at,
        "is_promoted": agg.is_promoted,
        # P3-7: transient — needed by build_serving for product-level
        # distinct review union. NOT persisted to agg_product_signal.
        "review_ids": agg.review_ids,
    }
