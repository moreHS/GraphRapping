"""
Wave 5.3: Pipeline-run advisory lock + concurrency error.

Single source-of-truth lock that serializes FULL ↔ INCREMENTAL DB entrypoints
against each other. Uses Postgres `pg_try_advisory_lock` with a deterministic
constant key (not `hash(...)` — PYTHONHASHSEED is non-deterministic).

Contract:
  - `acquire_pipeline_lock(pool)` yields once the lock is held, raises
    `PipelineConcurrencyError` otherwise.
  - Lock is connection-scoped: the helper holds one pool connection for the
    entire critical section; inner work must use other pool connections.
  - Pool guard: `pool.get_max_size() >= 2` enforced before acquire — a
    `max_size=1` pool deadlocks (lock conn blocks inner acquire).
  - Lock is released on normal exit AND on exception (`finally`).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

logger = logging.getLogger(__name__)

# Deterministic 64-bit constant — ASCII "GRPRPLRQ" (GraphRapping Pipeline RunQ).
# Fits Postgres bigint (signed): 0x47525052_504C5251 = 5_141_174_345_793_344_593.
# Same key for FULL and INCREMENTAL → cross-mutex.
GRAPHRAPPING_PIPELINE_LOCK_KEY = 0x47525052504C5251


class PipelineConcurrencyError(RuntimeError):
    """Raised when another pipeline run holds the advisory lock OR when the
    pool is too small to safely hold the lock connection alongside inner work.
    The message distinguishes the two causes."""


@asynccontextmanager
async def acquire_pipeline_lock(
    pool: asyncpg.Pool,
    run_label: str = "pipeline",
) -> AsyncIterator[int]:
    """Hold the pipeline advisory lock for the lifetime of the `async with`.

    Yields the holder PID so callers can record it (e.g., into
    `pipeline_run.lock_holder_pid`).

    Raises `PipelineConcurrencyError` if:
      - pool.get_max_size() < 2 (would deadlock)
      - another holder already has the lock (pg_try_advisory_lock returns FALSE)
    """
    max_size = pool.get_max_size()
    if max_size < 2:
        raise PipelineConcurrencyError(
            f"Pool max_size={max_size} < 2: cannot hold the pipeline lock "
            f"connection AND let {run_label!s} acquire a second connection "
            f"for inner work. Increase pool size to >= 2."
        )

    lock_conn = await pool.acquire()
    try:
        acquired = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)",
            GRAPHRAPPING_PIPELINE_LOCK_KEY,
        )
        if not acquired:
            raise PipelineConcurrencyError(
                f"Another pipeline run is in progress; {run_label!s} "
                f"could not acquire the advisory lock "
                f"(key=0x{GRAPHRAPPING_PIPELINE_LOCK_KEY:016X})."
            )
        pid = os.getpid()
        logger.info(
            "Acquired pipeline lock (label=%s, pid=%d)", run_label, pid,
        )
        try:
            yield pid
        finally:
            # pg_advisory_unlock is safe to call from the same connection
            # that acquired the lock; ignore the boolean return.
            await lock_conn.execute(
                "SELECT pg_advisory_unlock($1)",
                GRAPHRAPPING_PIPELINE_LOCK_KEY,
            )
            logger.info(
                "Released pipeline lock (label=%s, pid=%d)", run_label, pid,
            )
    finally:
        await pool.release(lock_conn)
