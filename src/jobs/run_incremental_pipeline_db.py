"""
Wave 5.1: Incremental pipeline → DB entrypoint.

Thin wrap around `src.jobs.run_incremental_pipeline.run_incremental` so callers
get the same shape as `run_full_load_to_db`: caller-owned pool, structured
result dataclass, opt-in contract validation.

The wrap does NOT add new pipeline logic — `run_incremental` already persists
all 5 layers, advances the watermark, and (re)builds serving profiles for
dirty products. This module exists so external consumers (e.g., AmoreSimulation)
have a single, validator-integrated entrypoint mirroring the FULL load shape.

Watermark contract (recap):
    - Source of truth: `pipeline_run.watermark_ts/watermark_rid`
    - First-run after FULL: returns 0 reviews (FULL seeds the watermark via
      `run_full_load_db._max_review_watermark`)
    - First-run with no prior pipeline_run: processes everything
    - No-op runs: keep the watermark unchanged
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from src.db.contract_validator import ContractValidationResult, validate_all
from src.db.pipeline_lock import acquire_pipeline_lock
from src.jobs.run_incremental_pipeline import run_incremental
from src.link.product_matcher import ProductIndex
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.wrap.projection_registry import ProjectionRegistry

logger = logging.getLogger(__name__)


@dataclass
class IncrementalDbResult:
    """Result of `run_incremental_to_db`.

    Mirrors `FullLoadDbResult` so callers can treat full + incremental
    uniformly. `in_memory` carries the raw dict returned by
    `run_incremental` (run_id, status, review_count, signal_count,
    dirty_product_count, skipped_count, watermark, cleanup_counts).
    """

    in_memory: dict[str, Any]
    run_id: int
    persisted: dict[str, int] = field(default_factory=dict)
    validation: ContractValidationResult | None = None


async def run_incremental_to_db(
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
    validate_after: bool = True,
    validator_options: dict[str, Any] | None = None,
) -> IncrementalDbResult:
    """Run the incremental pipeline and return a validator-integrated result.

    Args mirror `run_incremental` exactly (no override / no caller-supplied
    watermark — the DB is the single source of truth).

    Args:
      validate_after: Run `validate_all` after the pipeline completes.
        Default True so failed-readiness states show up immediately.
      validator_options: kwargs forwarded to `validate_all` (e.g.
        `expected_min_active_products=1`, `signal_window="all"`).
    """
    # Wave 5.3: serialize against any other FULL or INCREMENTAL writer.
    # NOTE: the advisory lock serializes COOPERATING pipeline writers
    # (anything that also calls `acquire_pipeline_lock`). It does NOT block
    # passive DB readers (consumers using SELECT) or out-of-band writers
    # that ignore the lock helper. Use a read-only role for consumers; rely
    # on operational discipline for any ad-hoc raw writes.
    #
    # Lock spans the inner pipeline AND validator so consumers don't see a
    # partial state mid-validation.
    # Follow-up (Codex 1차 recommendation #2): when `run_incremental` raises
    # before returning a run_id, the pipeline_run row produced by its
    # internal `start_pipeline_run` keeps `lock_holder_pid=NULL`. Mutex
    # correctness is unaffected; ops observability is degraded for failed
    # incremental runs. Tracked for Wave 6.
    async with acquire_pipeline_lock(pool, run_label="run_incremental_to_db") as lock_pid:
        in_memory = await run_incremental(
            pool,
            product_index=product_index,
            product_masters=product_masters,
            concept_links=concept_links,
            bee_normalizer=bee_normalizer,
            relation_canonicalizer=relation_canonicalizer,
            projection_registry=projection_registry,
            deriver=deriver,
            predicate_contracts=predicate_contracts,
            batch_size=batch_size,
            kg_mode=kg_mode,
        )

        # Record lock_holder_pid on the pipeline_run row produced by run_incremental.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pipeline_run SET lock_holder_pid = $1 WHERE run_id = $2",
                lock_pid, int(in_memory["run_id"]),
            )

        persisted = {
            "review_count": int(in_memory.get("review_count", 0)),
            "signal_count": int(in_memory.get("signal_count", 0)),
            "dirty_product_count": int(in_memory.get("dirty_product_count", 0)),
            "skipped_count": int(in_memory.get("skipped_count", 0)),
        }

        validation: ContractValidationResult | None = None
        if validate_after:
            validation = await validate_all(pool, **(validator_options or {}))

    return IncrementalDbResult(
        in_memory=in_memory,
        run_id=int(in_memory["run_id"]),
        persisted=persisted,
        validation=validation,
    )
