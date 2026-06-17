"""
Wave 5.3: Pipeline-run advisory lock — concurrency + max_size guard +
FULL ↔ INCREMENTAL cross-mutex.

All tests skip if GRAPHRAPPING_TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from src.common.config_loader import load_predicate_contracts
from src.db.migrate import migrate
from src.db.pipeline_lock import (
    PipelineConcurrencyError,
    acquire_pipeline_lock,
)
from src.jobs.run_full_load import FullLoadConfig
from src.jobs.run_full_load_db import run_full_load_to_db
from src.jobs.run_incremental_pipeline_db import run_incremental_to_db
from src.link.product_matcher import ProductIndex
from src.loaders.product_loader import load_products_from_es_records
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.wrap.projection_registry import ProjectionRegistry

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run.",
    ),
    pytest.mark.timeout(600),
]

MOCK = Path(__file__).parent.parent / "mockdata"


def _mock_inputs():
    products = json.loads((MOCK / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = json.loads((MOCK / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    return products, users


def _build_incremental_context(products):
    product_result = load_products_from_es_records(products, sale_status_filter=None)
    product_index = product_result.product_index or ProductIndex.build([])
    bee_norm = BEENormalizer()
    rel_canon = RelationCanonicalizer()
    rel_canon.load()
    proj_registry = ProjectionRegistry()
    proj_registry.load()
    return {
        "product_index": product_index,
        "product_masters": product_result.product_masters,
        "concept_links": product_result.concept_links,
        "bee_normalizer": bee_norm,
        "relation_canonicalizer": rel_canon,
        "projection_registry": proj_registry,
        "deriver": ToolConcernSegmentDeriver(),
        "predicate_contracts": load_predicate_contracts(),
    }


@pytest_asyncio.fixture()
async def pg_schema():
    """Per-test schema; pool size is per-test (some tests need max_size=1)."""
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_t53_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()
    try:
        yield schema
    finally:
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


async def _make_pool(schema: str, max_size: int = 4) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        TEST_DATABASE_URL, min_size=1, max_size=max_size,
        server_settings={"search_path": schema},
    )


@pytest.mark.asyncio
async def test_lock_helper_acquires_and_releases(pg_schema) -> None:
    """Single acquire returns the holder pid; re-acquire after release succeeds."""
    pool = await _make_pool(pg_schema, max_size=4)
    try:
        await migrate(pool)
        # 1st acquire
        async with acquire_pipeline_lock(pool, run_label="test1") as pid1:
            assert isinstance(pid1, int) and pid1 > 0
        # 2nd acquire — must succeed (released)
        async with acquire_pipeline_lock(pool, run_label="test2") as pid2:
            assert pid2 == pid1  # same process
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_lock_helper_max_size_guard_raises(pg_schema) -> None:
    """max_size=1 pool must raise PipelineConcurrencyError immediately
    (would deadlock if it tried to acquire the lock conn + inner work conn)."""
    pool = await _make_pool(pg_schema, max_size=1)
    try:
        with pytest.raises(PipelineConcurrencyError, match="max_size=1"):
            async with acquire_pipeline_lock(pool, run_label="guard-test"):
                pass  # pragma: no cover — should not enter
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_lock_helper_concurrent_acquire_one_raises(pg_schema) -> None:
    """Two concurrent acquires on the same key: one holds, the other raises."""
    pool = await _make_pool(pg_schema, max_size=4)
    try:
        await migrate(pool)

        holder_entered = asyncio.Event()
        holder_release = asyncio.Event()
        results = {"holder": None, "loser": None}

        async def hold():
            async with acquire_pipeline_lock(pool, run_label="holder"):
                holder_entered.set()
                await holder_release.wait()
            results["holder"] = "completed"

        async def attempt():
            await holder_entered.wait()
            try:
                async with acquire_pipeline_lock(pool, run_label="attempt"):
                    results["loser"] = "unexpected-success"  # pragma: no cover
            except PipelineConcurrencyError as exc:
                results["loser"] = str(exc)

        hold_task = asyncio.create_task(hold())
        attempt_task = asyncio.create_task(attempt())
        await attempt_task  # second one fast-fails
        holder_release.set()
        await hold_task

        assert results["holder"] == "completed"
        assert results["loser"] is not None
        assert "Another pipeline run is in progress" in results["loser"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_lock_helper_releases_on_exception(pg_schema) -> None:
    """Lock must release if the critical-section body raises (finally semantic)."""
    pool = await _make_pool(pg_schema, max_size=4)
    try:
        await migrate(pool)

        with pytest.raises(ValueError, match="boom"):
            async with acquire_pipeline_lock(pool, run_label="will-fail"):
                raise ValueError("boom")

        # Lock must be released — next acquire succeeds without raising.
        async with acquire_pipeline_lock(pool, run_label="recovery"):
            pass
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_full_load_and_incremental_cross_mutex(pg_schema) -> None:
    """FULL holding the lock blocks INCREMENTAL (and vice versa) — single key
    serializes ALL pipeline run types."""
    pool = await _make_pool(pg_schema, max_size=4)
    try:
        await migrate(pool)

        # Manually hold the lock to simulate an in-flight FULL.
        # Use an Event so the test does not race on a wall-clock sleep
        # (Codex 1차 recommendation — sleep(0.1) flakes on slow CI).
        holder_acquired = asyncio.Event()
        holder_release = asyncio.Event()

        async def hold_lock():
            async with acquire_pipeline_lock(pool, run_label="simulated-full"):
                holder_acquired.set()
                await holder_release.wait()

        holder = asyncio.create_task(hold_lock())
        await holder_acquired.wait()  # deterministic: lock is held now

        products, users = _mock_inputs()
        ctx = _build_incremental_context(products)

        # INCREMENTAL must raise PipelineConcurrencyError while FULL "runs".
        with pytest.raises(PipelineConcurrencyError):
            await run_incremental_to_db(pool, **ctx, validate_after=False)

        holder_release.set()
        await holder  # release

        # After release, INCREMENTAL works again.
        # (no-op call — no review_raw rows yet; FULL hasn't actually run.
        # We still expect a COMPLETED pipeline_run row.)
        result = await run_incremental_to_db(pool, **ctx, validate_after=False)
        assert result.in_memory["status"] == "COMPLETED"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_full_load_to_db_records_lock_holder_pid(pg_schema) -> None:
    """Wave 5.3: lock_holder_pid is populated on FULL run pipeline_run row."""
    pool = await _make_pool(pg_schema, max_size=4)
    try:
        products, users = _mock_inputs()
        config = FullLoadConfig(
            review_json_path=str(MOCK / "review_triples_raw.json"),
            product_es_records=products, user_profiles=users, kg_mode="off",
        )
        result = await run_full_load_to_db(pool, config, validate_after=False)
        async with pool.acquire() as conn:
            pid = await conn.fetchval(
                "SELECT lock_holder_pid FROM pipeline_run WHERE run_id = $1",
                result.run_id,
            )
        assert pid == os.getpid()
    finally:
        await pool.close()
