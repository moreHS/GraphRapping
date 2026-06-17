"""
Sub-task 5 (P0-6) SQL prefilter avoided ingredient filter tests.

Before this sub-task, `sql_prefilter_candidates()` had two bugs:
  1. When `preferred_concept_ids` was empty, the avoided filter was skipped
     entirely (preferred-empty users had no avoided protection).
  2. The avoided filter only checked raw `ingredient_ids`, so concept-IRI
     avoided IDs (the default domain from `personal_agent_adapter`) didn't
     match — SQL/Python diverged.

This test module verifies the rewritten SQL:
  - Avoided filter applies in BOTH preferred-empty and preferred-present paths.
  - Avoided matches in EITHER raw `ingredient_ids` OR concept `ingredient_concept_ids`.
  - SQL and Python hard-filter results agree for the same input.

All tests require GRAPHRAPPING_TEST_DATABASE_URL (auto-skipped otherwise).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrate import migrate
from src.db.repos.mart_repo import sql_prefilter_candidates
from src.db.unit_of_work import UnitOfWork


TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

# Wave 4 Task 2: bump pytest timeout for PG-bound tests.
pytestmark = pytest.mark.timeout(120)

pgmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_p06_{uuid.uuid4().hex}"

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
        yield pool, schema  # type: ignore[misc]
    finally:
        if pool is not None:
            await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        finally:
            await admin.close()


async def _insert_product(
    pool: asyncpg.Pool,
    product_id: str,
    *,
    ingredient_ids: list[str] | None = None,
    ingredient_concept_ids: list[str] | None = None,
    brand_concept_ids: list[str] | None = None,
    category_concept_ids: list[str] | None = None,
    main_benefit_concept_ids: list[str] | None = None,
) -> None:
    """Insert a serving_product_profile row directly with the given ingredient
    and concept arrays. JSONB columns receive JSON-encoded strings."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO serving_product_profile (
                product_id,
                ingredient_ids,
                ingredient_concept_ids,
                brand_concept_ids,
                category_concept_ids,
                main_benefit_concept_ids
            ) VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb)
            """,
            product_id,
            ingredient_ids or [],
            json.dumps(ingredient_concept_ids or []),
            json.dumps(brand_concept_ids or []),
            json.dumps(category_concept_ids or []),
            json.dumps(main_benefit_concept_ids or []),
        )


# ---------------------------------------------------------------------------
# TC1: preferred empty + avoided present → avoided excluded
# ---------------------------------------------------------------------------

@pgmark
async def test_avoided_filter_applies_when_no_preferred(pg_pool) -> None:
    """P0-6 bug #1: previously the avoided filter was skipped when preferred
    was empty. Now it must always apply."""
    pool, _ = pg_pool
    await migrate(pool)

    await _insert_product(pool, "P_clean",
                          ingredient_concept_ids=["concept:Ingredient:safe"])
    await _insert_product(pool, "P_bad",
                          ingredient_concept_ids=["concept:Ingredient:badx"])

    async with UnitOfWork(pool) as uow:
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["concept:Ingredient:badx"],
            preferred_concept_ids=[],
        )

    assert "P_clean" in result
    assert "P_bad" not in result, (
        "preferred empty but avoided ingredient must still exclude P_bad"
    )


# ---------------------------------------------------------------------------
# TC2: avoided as concept IRI excluded + clean preferred match included
# ---------------------------------------------------------------------------

@pgmark
async def test_avoided_concept_iri_excluded_clean_included(pg_pool) -> None:
    """P0-6 bug #2: avoided IDs in concept-IRI domain must match against
    `ingredient_concept_ids`. Clean product with same brand match still passes."""
    pool, _ = pg_pool
    await migrate(pool)

    await _insert_product(pool, "P_clean",
                          ingredient_concept_ids=["concept:Ingredient:safe"],
                          brand_concept_ids=["concept:Brand:b1"])
    await _insert_product(pool, "P_bad",
                          ingredient_concept_ids=["concept:Ingredient:badx"],
                          brand_concept_ids=["concept:Brand:b1"])

    async with UnitOfWork(pool) as uow:
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["concept:Ingredient:badx"],
            preferred_concept_ids=["concept:Brand:b1"],
        )

    assert "P_bad" not in result, "concept-IRI avoided must exclude P_bad"
    assert "P_clean" in result, "clean product with matching brand must pass"


# ---------------------------------------------------------------------------
# TC3: avoided as raw → ingredient_ids match + clean inclusion
# ---------------------------------------------------------------------------

@pgmark
async def test_avoided_raw_excluded_clean_included(pg_pool) -> None:
    """Avoided IDs in raw domain match against `ingredient_ids` (TEXT[]).
    Two-axis OR keeps the legacy raw-only flow working."""
    pool, _ = pg_pool
    await migrate(pool)

    await _insert_product(pool, "P_clean",
                          ingredient_ids=["safe"],
                          brand_concept_ids=["concept:Brand:b1"])
    await _insert_product(pool, "P_bad",
                          ingredient_ids=["badx"],
                          brand_concept_ids=["concept:Brand:b1"])

    async with UnitOfWork(pool) as uow:
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["badx"],
            preferred_concept_ids=["concept:Brand:b1"],
        )

    assert "P_bad" not in result
    assert "P_clean" in result


# ---------------------------------------------------------------------------
# TC4: empty avoided + empty preferred → all products pass (exact set)
# ---------------------------------------------------------------------------

@pgmark
async def test_empty_avoided_and_preferred_returns_all(pg_pool) -> None:
    """No filters → exactly the inserted products are returned (schema is
    isolated per test so the set must equal, not just be a superset)."""
    pool, _ = pg_pool
    await migrate(pool)

    await _insert_product(pool, "P1",
                          ingredient_concept_ids=["concept:Ingredient:safe"])
    await _insert_product(pool, "P2",
                          ingredient_concept_ids=["concept:Ingredient:other"])

    async with UnitOfWork(pool) as uow:
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=[],
            preferred_concept_ids=[],
        )

    assert set(result) == {"P1", "P2"}


# ---------------------------------------------------------------------------
# TC4b: NULL ingredient columns must not crash the SQL (Codex review #1)
# ---------------------------------------------------------------------------

@pgmark
async def test_null_ingredient_columns_pass_through(pg_pool) -> None:
    """A product whose `ingredient_ids` and `ingredient_concept_ids` are SQL
    NULL (not empty array) must not crash the prefilter and must pass when no
    avoided overlap exists."""
    pool, _ = pg_pool
    await migrate(pool)

    async with pool.acquire() as conn:
        # Insert with NULL ingredient columns explicitly.
        await conn.execute(
            """
            INSERT INTO serving_product_profile (
                product_id,
                ingredient_ids,
                ingredient_concept_ids,
                brand_concept_ids,
                category_concept_ids,
                main_benefit_concept_ids
            ) VALUES ($1, NULL, NULL, NULL, NULL, NULL)
            """,
            "P_null_ing",
        )

    async with UnitOfWork(pool) as uow:
        # avoided present but the product has no ingredient data → NOT EXISTS true
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["concept:Ingredient:anything"],
            preferred_concept_ids=[],
        )

    assert "P_null_ing" in result, (
        "NULL ingredient columns must not raise and must pass when no overlap"
    )


# ---------------------------------------------------------------------------
# TC5: SQL/Python equivalence on avoided hard filter
# ---------------------------------------------------------------------------

@pgmark
async def test_sql_python_avoided_equivalence(pg_pool) -> None:
    """Same user (avoided=concept IRI) + same products → SQL and Python
    `generate_candidates()` agree on which products survive the avoided hard
    filter."""
    pool, _ = pg_pool
    await migrate(pool)

    products_data = [
        {
            "product_id": "P_safe",
            "ingredient_concept_ids": ["concept:Ingredient:safe"],
        },
        {
            "product_id": "P_bad",
            "ingredient_concept_ids": ["concept:Ingredient:badx"],
        },
    ]
    for p in products_data:
        await _insert_product(pool, **p)  # type: ignore[arg-type]

    # SQL path
    async with UnitOfWork(pool) as uow:
        sql_result = set(await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["concept:Ingredient:badx"],
            preferred_concept_ids=[],
        ))

    # Python path — needs serving_product_profile-shaped dicts.
    user_profile: dict[str, Any] = {
        "avoided_ingredient_ids": [{"id": "concept:Ingredient:badx"}],
    }
    python_products = [
        {
            "product_id": p["product_id"],
            "ingredient_concept_ids": p["ingredient_concept_ids"],
        }
        for p in products_data
    ]
    from src.rec.candidate_generator import generate_candidates
    py_candidates = generate_candidates(user_profile, python_products)
    py_result = {c.product_id for c in py_candidates if not c.hard_filtered}

    assert "P_bad" not in sql_result
    assert "P_bad" not in py_result
    assert "P_safe" in sql_result
    assert "P_safe" in py_result
    assert sql_result == py_result, (
        f"SQL/Python avoided hard-filter divergence: sql={sql_result} py={py_result}"
    )


# ---------------------------------------------------------------------------
# TC6: mixed-domain — raw avoided vs product with both axes (Codex review #3)
# ---------------------------------------------------------------------------

@pgmark
async def test_sql_python_mixed_domain_divergence(pg_pool) -> None:
    """Product has BOTH `ingredient_ids=["badx"]` AND
    `ingredient_concept_ids=["concept:Ingredient:other"]` (distinct values).
    Avoided is raw `["badx"]`.

    Expected behavior:
    - SQL: two-axis OR → ingredient_ids axis matches → exclude.
    - Python: raw and concept ingredient axes are unioned → exclude.
    """
    pool, _ = pg_pool
    await migrate(pool)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO serving_product_profile (
                product_id, ingredient_ids, ingredient_concept_ids,
                brand_concept_ids, category_concept_ids, main_benefit_concept_ids
            ) VALUES ($1, $2, $3::jsonb, NULL, NULL, NULL)
            """,
            "P_mixed",
            ["badx"],
            json.dumps(["concept:Ingredient:other"]),
        )

    async with UnitOfWork(pool) as uow:
        sql_result = set(await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["badx"],
            preferred_concept_ids=[],
        ))

    # SQL excludes P_mixed because raw ingredient_ids axis matches.
    assert "P_mixed" not in sql_result, "SQL must catch raw-axis match"

    # Python excludes P_mixed by checking both ingredient axes, matching SQL.
    from src.rec.candidate_generator import generate_candidates
    py_candidates = generate_candidates(
        {"avoided_ingredient_ids": [{"id": "badx"}]},
        [{
            "product_id": "P_mixed",
            "ingredient_ids": ["badx"],
            "ingredient_concept_ids": ["concept:Ingredient:other"],
        }],
    )
    py_surviving = {c.product_id for c in py_candidates if not c.hard_filtered}
    assert "P_mixed" not in py_surviving, (
        "Python must catch raw-axis matches even when ingredient_concept_ids "
        "is non-empty."
    )
