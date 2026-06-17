"""
Wave 4 Task 4: Full load with DB materialization.

Runs the existing in-memory `run_full_load` pipeline, then persists every
produced layer to Postgres so downstream consumers (e.g. AmoreSimulation)
can read truth from the DB.

Design:
- **Caller-owned pool**. The function never closes `pool`. Long-running
  services inject their own `asyncpg.Pool`; short-lived scripts create one
  via `src.db.connection.create_pool` and close it after this returns.
- **Idempotent**. Migrations are CREATE IF NOT EXISTS; product / user /
  purchase / aggregate / serving writes are deterministic upserts; review
  bundles via `persist_review_bundle` use UPSERT/version semantics.
- **pipeline_run row**. A single `pipeline_run` row tracks the load:
  `run_type='FULL'`, status `RUNNING` → `COMPLETED` / `FAILED`. On failure
  the exception re-raises so operators see it.
- **Optional validation**. After persist, runs `validate_all(pool, ...)`
  with caller-provided minimums. Result is attached to `FullLoadDbResult`
  but does NOT raise on EMPTY / INVALID — the caller decides how to react.

Acceptance (Wave 4 plan v2):
- DB load over the mock fixture matches the in-memory baseline counts.
- Re-running the same fixture does not duplicate rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg

from src.db.contract_validator import (
    ContractValidationResult,
    validate_all,
)
from src.db.migrate import migrate
from src.db.persist import persist_aggregates, persist_review_bundle
from src.db.pipeline_lock import acquire_pipeline_lock
from src.db.repos import product_repo, user_repo
from src.db.unit_of_work import UnitOfWork
from src.ingest.purchase_ingest import PurchaseEvent
from src.jobs.run_daily_pipeline import _agg_to_dict
from src.jobs.run_full_load import FullLoadConfig, FullLoadResult, run_full_load

logger = logging.getLogger(__name__)


@dataclass
class FullLoadDbResult:
    """Combined in-memory load result + DB persist stats + optional validator."""
    in_memory: FullLoadResult
    run_id: int | None = None
    persisted: dict[str, int] = field(default_factory=dict)
    validation: ContractValidationResult | None = None
    error_message: str | None = None


async def _start_full_run(pool: asyncpg.Pool, lock_pid: int | None = None) -> int:
    """Create a `pipeline_run` row with run_type='FULL', status='RUNNING'.

    Wave 5.3: `lock_pid` is recorded so operators can identify which process
    holds the advisory lock for this run (null for legacy callers).
    """
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO pipeline_run (run_type, started_at, status, lock_holder_pid)
            VALUES ('FULL', $1, 'RUNNING', $2)
            RETURNING run_id
            """,
            datetime.now(timezone.utc),
            lock_pid,
        )
        return int(run_id)


async def _max_review_watermark(
    pool: asyncpg.Pool,
) -> tuple[datetime | None, str | None]:
    """Return the (updated_at, review_id) of the most recent active review_raw row.

    Wave 5.1: FULL load seeds the incremental cursor so the first subsequent
    `run_incremental_to_db` call starts from "everything processed up to here"
    and reports zero new reviews instead of reprocessing the entire corpus.
    Returns (None, None) when review_raw is empty.

    Operational assumption (1차 Codex review): raw ingest is quiesced while
    a FULL run is in flight. If a raw writer inserts a NEW active review
    during FULL but BEFORE this watermark read, that review is captured
    here and may have been skipped by FULL's review iteration — i.e.,
    incremental will treat it as "already processed". Wave 5.3 advisory
    lock is the production safeguard; until then operators must serialize
    raw ingestion against FULL runs.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT updated_at, review_id
            FROM review_raw
            WHERE is_active = true
            ORDER BY updated_at DESC, review_id DESC
            LIMIT 1
            """
        )
        if not row:
            return None, None
        return row["updated_at"], row["review_id"]


async def _complete_full_run(
    pool: asyncpg.Pool,
    run_id: int,
    review_count: int,
    signal_count: int,
    quarantine_count: int,
    *,
    watermark_ts: datetime | None = None,
    watermark_rid: str | None = None,
    error_message: str | None = None,
) -> None:
    """Mark pipeline_run as COMPLETED or FAILED.

    Wave 5.1: writes `watermark_ts/watermark_rid` so the next incremental
    run starts from the position the FULL load just baselined. Pass None to
    leave the watermark NULL (e.g., FAILED runs).
    """
    status = "FAILED" if error_message else "COMPLETED"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_run
            SET status = $1,
                completed_at = $2,
                review_count = $3,
                signal_count = $4,
                quarantine_count = $5,
                watermark_ts = $6,
                watermark_rid = $7,
                error_message = $8
            WHERE run_id = $9
            """,
            status,
            datetime.now(timezone.utc),
            review_count,
            signal_count,
            quarantine_count,
            watermark_ts,
            watermark_rid,
            error_message,
            run_id,
        )


def _coerce_purchased_at(value: Any) -> datetime | None:
    """purchase_event_raw.purchased_at is timestamptz; PurchaseEvent.purchased_at
    is `str | None`. Parse here so asyncpg can bind."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            try:
                return datetime.fromisoformat(s + "T00:00:00+00:00")
            except ValueError as exc:
                raise ValueError(f"Cannot coerce purchased_at {value!r}") from exc
    raise TypeError(f"Unsupported purchased_at type: {type(value).__name__}")


def _purchase_events_to_dicts(
    purchase_events_by_user: dict[str, list[PurchaseEvent]] | None,
) -> list[dict[str, Any]]:
    """Flatten `purchase_events_by_user` into rows for purchase_event_raw."""
    if not purchase_events_by_user:
        return []
    rows: list[dict[str, Any]] = []
    for user_id, events in purchase_events_by_user.items():
        for ev in events:
            rows.append({
                "purchase_event_id": ev.purchase_event_id,
                "user_id": user_id,
                "product_id": ev.product_id,
                "purchased_at": _coerce_purchased_at(ev.purchased_at),
                "price": ev.price,
                "quantity": ev.quantity,
                "channel": ev.channel,
            })
    return rows


async def _persist_layer0(
    pool: asyncpg.Pool,
    in_memory: FullLoadResult,
    purchase_events_by_user: dict[str, list[PurchaseEvent]] | None,
) -> dict[str, int]:
    """Persist Layer 0 truth: product_master, concept seeds/links, user_master, purchase events."""
    batch = in_memory.batch_result
    product_masters = batch.get("product_masters", {})
    user_masters = batch.get("user_masters", {})
    concept_links_by_iri = batch.get("concept_links", {})

    counts = {
        "product_masters": 0,
        "product_review_stats": 0,
        "concept_seeds": 0,
        "entity_concept_links": 0,
        "user_masters": 0,
        "purchase_events": 0,
    }

    async with UnitOfWork(pool) as uow:
        for product in product_masters.values():
            await product_repo.upsert_product_master(uow, product)
            counts["product_masters"] += 1

        for stats in batch.get("source_review_stats_by_product", {}).values():
            await product_repo.upsert_product_review_stats(uow, stats)
            counts["product_review_stats"] += 1

        # Concept registry seeds (from product_loader) — required so consumer
        # joins on concept_id resolve. ON CONFLICT DO NOTHING already; safe.
        if in_memory.concept_seeds:
            await product_repo.upsert_concept_seeds(uow, in_memory.concept_seeds)
            counts["concept_seeds"] = len(in_memory.concept_seeds)

        # Entity-concept links per product
        all_links: list[dict[str, Any]] = []
        for entity_iri, links in concept_links_by_iri.items():
            for link in links:
                all_links.append({"entity_iri": entity_iri, **link})
        if all_links:
            await product_repo.upsert_entity_concept_links(uow, all_links)
            counts["entity_concept_links"] = len(all_links)

        for user_id, master in user_masters.items():
            await user_repo.upsert_user_master(uow, {"user_id": user_id, **master})
            counts["user_masters"] += 1

        events = _purchase_events_to_dicts(purchase_events_by_user)
        if events:
            await user_repo.insert_purchase_events(uow, events)
            counts["purchase_events"] = len(events)

    return counts


async def _persist_review_bundles(pool: asyncpg.Pool, in_memory: FullLoadResult) -> int:
    """Persist Layer 1/2/2.5 per-review artifacts."""
    bundles = in_memory.batch_result.get("all_bundles", [])
    persisted = 0
    for bundle in bundles:
        await persist_review_bundle(pool, bundle)
        persisted += 1
    return persisted


async def _persist_layer3(pool: asyncpg.Pool, in_memory: FullLoadResult) -> dict[str, int]:
    """Persist Layer 3 aggregates + serving profiles via persist_aggregates."""
    batch = in_memory.batch_result
    agg_signals = batch.get("agg_signals", [])
    agg_rows = [_agg_to_dict(a) for a in agg_signals]
    return await persist_aggregates(
        pool,
        agg_rows=agg_rows,
        serving_products=in_memory.serving_products,
        serving_users=in_memory.serving_users,
        user_pref_rows=batch.get("user_pref_rows", []),
    )


async def run_full_load_to_db(
    pool: asyncpg.Pool,
    config: FullLoadConfig,
    *,
    run_migrations: bool = True,
    validate_after: bool = True,
    validator_options: dict[str, Any] | None = None,
) -> FullLoadDbResult:
    """Run the in-memory full load then materialize all layers to Postgres.

    Args:
      pool: caller-owned asyncpg pool. NOT closed by this function.
      config: same `FullLoadConfig` accepted by `run_full_load`.
      run_migrations: when True (default), apply migrations before persist.
      validate_after: when True (default), run `validate_all(pool, ...)` at the end.
      validator_options: kwargs forwarded to `validate_all` (e.g.
        `expected_min_active_products=1`, `signal_window="all"`).

    Returns `FullLoadDbResult` with in-memory result, run_id, persist counts,
    and optional validation. On failure, marks `pipeline_run.status='FAILED'`
    and re-raises so operators see the error.
    """
    if run_migrations:
        await migrate(pool)

    # Wave 5.3: serialize against any other FULL or INCREMENTAL run.
    # Lock is held for the entire critical section (Layer0 → bundles →
    # Layer3 → validator). Released on normal exit AND on exception.
    async with acquire_pipeline_lock(pool, run_label="run_full_load_to_db") as lock_pid:
        run_id = await _start_full_run(pool, lock_pid=lock_pid)

        try:
            # Step 1: in-memory full pipeline (the existing entrypoint).
            in_memory = run_full_load(config)

            # Step 2: persist each layer.
            layer0_counts = await _persist_layer0(
                pool, in_memory, config.purchase_events_by_user,
            )
            review_bundles_persisted = await _persist_review_bundles(pool, in_memory)
            layer3_counts = await _persist_layer3(pool, in_memory)

            persisted = {
                **layer0_counts,
                "review_bundles": review_bundles_persisted,
                **layer3_counts,
            }

            # Step 3: complete the pipeline_run row, seeding the watermark so
            # the first incremental run after a FULL reports zero new reviews.
            wm_ts, wm_rid = await _max_review_watermark(pool)
            await _complete_full_run(
                pool, run_id,
                review_count=in_memory.review_count,
                signal_count=in_memory.signal_count,
                quarantine_count=in_memory.quarantine_count,
                watermark_ts=wm_ts,
                watermark_rid=wm_rid,
            )

            # Step 4: optional contract validation.
            validation: ContractValidationResult | None = None
            if validate_after:
                validation = await validate_all(pool, **(validator_options or {}))

            return FullLoadDbResult(
                in_memory=in_memory,
                run_id=run_id,
                persisted=persisted,
                validation=validation,
            )

        except Exception as exc:
            logger.exception("run_full_load_to_db failed (run_id=%s)", run_id)
            await _complete_full_run(
                pool, run_id,
                review_count=0,
                signal_count=0,
                quarantine_count=0,
                error_message=str(exc),
            )
            raise
