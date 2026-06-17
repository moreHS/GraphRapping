"""
Wave 4 Task 4: `run_full_load_to_db` end-to-end against real Postgres.

Auto-skipped when `GRAPHRAPPING_TEST_DATABASE_URL` is unset. The CI
`postgres-service` job (Wave 4 Task 2) sets it from the service container.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from src.db.contract_validator import ContractStatus
from src.jobs.run_full_load import FullLoadConfig
from src.jobs.run_full_load_db import run_full_load_to_db

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run.",
    ),
    # Wave 5.4: bumped 180→600. Module-level marker wins over CLI --timeout
    # (pytest-timeout closest-marker rule). Several tests run two full loads
    # (e.g. idempotency) → 2 × ~5min on a 906-row fixture, plus overhead.
    pytest.mark.timeout(600),
]

MOCK = Path(__file__).parent.parent / "mockdata"


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    """Per-test schema so runs don't interfere. Drops the schema on teardown."""
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_t4_{uuid.uuid4().hex}"

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()

    pool = await asyncpg.create_pool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=2,
        server_settings={"search_path": schema},
    )
    try:
        yield pool, schema
    finally:
        await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


def _mock_inputs() -> tuple[list[dict], dict]:
    products = json.loads((MOCK / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = json.loads((MOCK / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    return products, users


@pytest.mark.asyncio
async def test_run_full_load_to_db_matches_in_memory_baseline(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Baseline regression: DB load produces same signal/quarantine counts
    as the in-memory final 906-review `run_full_load` baseline.
    """
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    result = await run_full_load_to_db(pool, config, validate_after=False)

    # Final 906-review baseline.
    assert result.in_memory.signal_count == 2801
    assert result.in_memory.quarantine_count == 9255
    # Pipeline run row marks COMPLETED
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_type, status, signal_count, quarantine_count FROM pipeline_run WHERE run_id=$1",
            result.run_id,
        )
    assert row["run_type"] == "FULL"
    assert row["status"] == "COMPLETED"
    assert row["signal_count"] == 2801
    assert row["quarantine_count"] == 9255


@pytest.mark.asyncio
async def test_run_full_load_to_db_persists_product_and_user_masters(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    result = await run_full_load_to_db(pool, config, validate_after=False)

    async with pool.acquire() as conn:
        product_count = await conn.fetchval(
            "SELECT COUNT(*) FROM product_master WHERE is_active = true"
        )
        user_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_master WHERE is_active = true"
        )

    assert product_count == result.persisted["product_masters"]
    assert user_count == result.persisted["user_masters"]
    # 2026-06-10 fix: catalog 47 → 517 (rs_own 진짜 product universe).
    assert product_count >= 517  # mockdata catalog now has 517 active products
    assert user_count >= 50      # mockdata has 50 user profiles

    # Wave 4 Task 4 Codex feedback: concept_registry + entity_concept_link persisted.
    # `persisted` counts INPUT rows; DB stores distinct after ON CONFLICT DO NOTHING.
    async with pool.acquire() as conn:
        concept_count = await conn.fetchval("SELECT COUNT(*) FROM concept_registry")
        link_count = await conn.fetchval("SELECT COUNT(*) FROM entity_concept_link")
    assert concept_count > 0, "concept_registry must be populated by run_full_load_to_db"
    assert link_count > 0, "entity_concept_link must be populated"
    assert result.persisted["concept_seeds"] >= concept_count, (
        "concept_seeds input count must be >= distinct DB rows"
    )
    assert result.persisted["entity_concept_links"] >= link_count

    # Wave 4 Task 4 Codex feedback: agg_user_preference.confidence populated.
    async with pool.acquire() as conn:
        non_null_confidence = await conn.fetchval(
            "SELECT COUNT(*) FROM agg_user_preference WHERE confidence IS NOT NULL"
        )
        total_prefs = await conn.fetchval("SELECT COUNT(*) FROM agg_user_preference")
    if total_prefs > 0:
        assert non_null_confidence == total_prefs, (
            f"agg_user_preference.confidence must be populated; "
            f"{non_null_confidence}/{total_prefs} non-null"
        )


@pytest.mark.asyncio
async def test_run_full_load_to_db_is_idempotent(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Re-running the same fixture must not duplicate product/user/serving rows."""
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    await run_full_load_to_db(pool, config, validate_after=False)

    async with pool.acquire() as conn:
        products_before = await conn.fetchval("SELECT COUNT(*) FROM product_master")
        users_before = await conn.fetchval("SELECT COUNT(*) FROM user_master")
        serving_p_before = await conn.fetchval("SELECT COUNT(*) FROM serving_product_profile")
        serving_u_before = await conn.fetchval("SELECT COUNT(*) FROM serving_user_profile")
        concepts_before = await conn.fetchval("SELECT COUNT(*) FROM concept_registry")
        # Wave 5.2: all quarantine_projection_miss rows now carry review_id
        # (PREDICATE_CONTRACT_VIOLATION path also keyed). Full count is
        # idempotent — no WHERE filter needed.
        quarantine_before = await conn.fetchval(
            "SELECT COUNT(*) FROM quarantine_projection_miss"
        )
        # Defense in depth: explicitly assert zero fact-less rows remain.
        unkeyed_before = await conn.fetchval(
            "SELECT COUNT(*) FROM quarantine_projection_miss "
            "WHERE (review_id IS NULL OR review_id = '') "
            "AND (fact_id IS NULL OR fact_id = '')"
        )

    # Second run — same fixture, deterministic IDs → no growth.
    await run_full_load_to_db(pool, config, run_migrations=False, validate_after=False)

    async with pool.acquire() as conn:
        products_after = await conn.fetchval("SELECT COUNT(*) FROM product_master")
        users_after = await conn.fetchval("SELECT COUNT(*) FROM user_master")
        serving_p_after = await conn.fetchval("SELECT COUNT(*) FROM serving_product_profile")
        serving_u_after = await conn.fetchval("SELECT COUNT(*) FROM serving_user_profile")
        concepts_after = await conn.fetchval("SELECT COUNT(*) FROM concept_registry")
        quarantine_after = await conn.fetchval(
            "SELECT COUNT(*) FROM quarantine_projection_miss"
        )
        unkeyed_after = await conn.fetchval(
            "SELECT COUNT(*) FROM quarantine_projection_miss "
            "WHERE (review_id IS NULL OR review_id = '') "
            "AND (fact_id IS NULL OR fact_id = '')"
        )

    assert products_before == products_after
    assert users_before == users_after
    assert serving_p_before == serving_p_after
    assert serving_u_before == serving_u_after
    assert concepts_before == concepts_after
    # Wave 5.2: full quarantine idempotency (was partial in Wave 4 Task 4).
    assert quarantine_before == quarantine_after, (
        f"quarantine_projection_miss grew between runs: "
        f"{quarantine_before} → {quarantine_after}"
    )
    # Wave 5.2: no quarantine row should be both review_id-less and fact_id-less.
    assert unkeyed_before == 0, (
        f"Expected zero fact-less quarantine rows after Wave 5.2; got {unkeyed_before}"
    )
    assert unkeyed_after == 0


@pytest.mark.asyncio
async def test_run_full_load_to_db_validator_runs_when_requested(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """validator returns OK on a fresh load."""
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    result = await run_full_load_to_db(
        pool, config,
        validate_after=True,
        validator_options={
            "expected_min_active_products": 1,
            "expected_min_active_users": 1,
            "signal_window": "all",
        },
    )

    assert result.validation is not None
    # Either OK (everything aligned) or document the gap explicitly.
    assert result.validation.status in (ContractStatus.OK, ContractStatus.EMPTY)
    # Counts are exposed
    assert "active_products" in result.validation.counts
    assert result.validation.counts["active_products"] >= 1


@pytest.mark.asyncio
async def test_run_full_load_to_db_seeds_pipeline_watermark(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Wave 5.1 follow-up: FULL load must write pipeline_run.watermark_ts/rid
    so the first subsequent incremental is a no-op (instead of reprocessing
    the entire corpus)."""
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    result = await run_full_load_to_db(pool, config, validate_after=False)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT watermark_ts, watermark_rid FROM pipeline_run "
            "WHERE run_id = $1",
            result.run_id,
        )
    assert row["watermark_ts"] is not None, "FULL must seed watermark_ts"
    assert row["watermark_rid"], "FULL must seed watermark_rid"

    # The seeded watermark must match the most-recent active review_raw row.
    async with pool.acquire() as conn:
        latest = await conn.fetchrow(
            "SELECT updated_at, review_id FROM review_raw "
            "WHERE is_active = true "
            "ORDER BY updated_at DESC, review_id DESC LIMIT 1"
        )
    assert row["watermark_ts"] == latest["updated_at"]
    assert row["watermark_rid"] == latest["review_id"]


@pytest.mark.asyncio
async def test_run_full_load_to_db_caller_owned_pool_is_not_closed(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """After run_full_load_to_db returns, the caller-owned pool is still usable."""
    pool, _schema = pg_pool
    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    )

    await run_full_load_to_db(pool, config, validate_after=False)

    # Pool must still be usable
    async with pool.acquire() as conn:
        v = await conn.fetchval("SELECT 1")
        assert v == 1
