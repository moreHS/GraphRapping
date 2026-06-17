from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrate import migrate
from src.db.repos.product_repo import load_product_review_stats, upsert_product_review_stats
from src.db.unit_of_work import UnitOfWork


TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")


class FakeUow:
    def __init__(self) -> None:
        self.as_of_ts = datetime(2026, 6, 15, tzinfo=timezone.utc)
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result


def test_upsert_product_review_stats_uses_composite_source_key() -> None:
    uow = FakeUow()

    row = {
        "product_id": "61289",
        "source_channel": "031",
        "source_key_type": "ecp_onln_prd_srno",
        "source_review_count_6m": 12,
        "source_review_score_count_6m": 10,
        "source_avg_rating_6m": 4.25,
        "source_review_min_date_6m": date(2026, 1, 1),
        "source_review_max_date_6m": date(2026, 6, 1),
        "source_review_count_all": 100,
        "source_review_score_count_all": 90,
        "source_avg_rating_all": 4.1,
        "source_review_min_date_all": date(2025, 1, 1),
        "source_review_max_date_all": date(2026, 6, 1),
    }

    import asyncio

    asyncio.run(upsert_product_review_stats(uow, row))  # type: ignore[arg-type]

    query, args = uow.executed[0]
    assert "INSERT INTO product_review_stats" in query
    assert "ON CONFLICT (product_id, source_channel, source_key_type) DO UPDATE SET" in query
    assert args[:3] == ("61289", "031", "ecp_onln_prd_srno")
    assert args[3:6] == (12, 10, 4.25)
    assert args[8:11] == (100, 90, 4.1)
    assert args[13] == "snowflake:f_prd_rv_hist"
    assert args[14] == uow.as_of_ts


def test_upsert_product_review_stats_normalizes_missing_source_key_parts() -> None:
    uow = FakeUow()

    import asyncio

    asyncio.run(upsert_product_review_stats(uow, {"product_id": "P1"}))  # type: ignore[arg-type]

    _query, args = uow.executed[0]
    assert args[:3] == ("P1", "unknown", "unknown")
    assert args[3] == 0
    assert args[4] == 0
    assert args[8] == 0
    assert args[9] == 0


def test_load_product_review_stats_is_product_id_first_with_source_key_preference() -> None:
    uow = FakeUow()
    uow.fetchrow_result = {
        "product_id": "61289",
        "source_channel": "unknown",
        "source_key_type": "unknown",
        "source_review_count_all": 9,
    }

    import asyncio

    result = asyncio.run(
        load_product_review_stats(
            uow,  # type: ignore[arg-type]
            "61289",
            "031",
            "ecp_onln_prd_srno",
        )
    )

    query, args = uow.fetchrow_calls[0]
    where_block = query.split("ORDER BY", maxsplit=1)[0]
    assert "FROM product_review_stats" in query
    assert "WHERE product_id = $1" in query
    assert "AND source_channel = $2" not in where_block
    assert "AND source_key_type = $3" not in where_block
    assert "ORDER BY" in query
    assert "source_channel = $2" in query
    assert "source_key_type = $3" in query
    assert args == ("61289", "031", "ecp_onln_prd_srno")
    assert result == uow.fetchrow_result


def test_load_product_review_stats_falls_back_when_source_key_mismatches() -> None:
    """Incremental callers can pass stale/missing source keys; product_id must
    still find persisted stats for the same product."""
    uow = FakeUow()
    uow.fetchrow_result = {
        "product_id": "P1",
        "source_channel": "036",
        "source_key_type": "chn_prd_cd",
        "source_review_count_all": 15,
    }

    import asyncio

    result = asyncio.run(
        load_product_review_stats(
            uow,  # type: ignore[arg-type]
            "P1",
            None,
            None,
        )
    )

    query, args = uow.fetchrow_calls[0]
    assert "WHERE product_id = $1" in query
    assert "LIMIT 1" in query
    assert args == ("P1", "unknown", "unknown")
    assert result == uow.fetchrow_result


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_prs_{uuid.uuid4().hex}"

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()

    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=1,
            server_settings={"search_path": schema},
        )
        yield pool, schema
    finally:
        if pool is not None:
            await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


@pytest.mark.skipif(TEST_DATABASE_URL is None, reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run.")
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_product_review_stats_postgres_upsert_is_idempotent(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _schema = pg_pool
    await migrate(pool)

    async with UnitOfWork(pool) as uow:
        await uow.execute(
            """
            INSERT INTO product_master (product_id, product_name, updated_at)
            VALUES ($1, $2, $3)
            """,
            "61289",
            "Black Cushion Duo",
            uow.as_of_ts,
        )
        await upsert_product_review_stats(uow, {
            "product_id": "61289",
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_review_count_6m": 2,
            "source_review_score_count_6m": 1,
            "source_avg_rating_6m": Decimal("4.000"),
            "source_review_count_all": 3,
            "source_review_score_count_all": 2,
            "source_avg_rating_all": Decimal("4.500"),
        })

    async with UnitOfWork(pool) as uow:
        await upsert_product_review_stats(uow, {
            "product_id": "61289",
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_review_count_6m": 5,
            "source_review_score_count_6m": 4,
            "source_avg_rating_6m": Decimal("4.250"),
            "source_review_count_all": 8,
            "source_review_score_count_all": 7,
            "source_avg_rating_all": Decimal("4.125"),
        })
        loaded = await load_product_review_stats(
            uow,
            "61289",
            "stale-channel",
            "stale-key",
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM product_review_stats")

    assert count == 1
    assert loaded is not None
    assert loaded["source_review_count_6m"] == 5
    assert loaded["source_review_score_count_6m"] == 4
    assert loaded["source_avg_rating_6m"] == Decimal("4.250")
    assert loaded["source_review_count_all"] == 8
    assert loaded["source_review_score_count_all"] == 7
    assert loaded["source_avg_rating_all"] == Decimal("4.125")
