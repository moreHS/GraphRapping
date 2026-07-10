"""
Phase 1.1: source identity collision contract checks.

Splits into three layers, matching the existing DB-contract test style:

- Unit tests for `validate_data`'s collision branch use monkeypatched count
  helpers, so they verify the status-decision logic without a real DB (same
  approach as `test_db_contract_validator.py`).
- Unit tests for the pipeline detection log tally (`_count_collision_detections`)
  are pure in-memory.
- Behavioural PG coverage runs the real SQL helpers against Postgres and is
  skipped unless `GRAPHRAPPING_TEST_DATABASE_URL` is set (same gate/fixture
  pattern as `test_postgres_integration.py`).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.db import contract_validator
from src.db.contract_validator import ContractStatus, validate_data
from src.db.migrate import migrate
from src.jobs.run_full_load_db import _count_collision_detections


# ---------------------------------------------------------------------------
# Unit: validate_data collision branch (monkeypatched helpers, no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_collision(monkeypatch: pytest.MonkeyPatch):
    """Stub every count helper validate_data touches, controllable via state.

    Non-collision checks are pinned clean so a test only moves the collision
    numbers it cares about.
    """
    state: dict[str, Any] = {
        "active_products": 1,
        "active_users": 1,
        "concepts": 1,
        "promoted_in_window": 1,
        "promotion_violations": 0,
        "stale": {"product_signals": 0, "user_preferences": 0},
        "mismatches": {
            "purchase_event_raw": 0,
            "agg_product_signal": 0,
            "serving_product_profile": 0,
        },
        "source_grounding": {
            "source_identity": 0,
            "promo_prefix_brand": 0,
            "source_stats_shape": 0,
        },
        "source_review_stats": {"positive_6m": 0, "avg_6m": 0},
        "collision": {
            "unmarked_structural": 0,
            "unmarked_shared_source_id": 0,
            "marked": 0,
        },
        "clean_join_leaks": 0,
    }

    async def _active(pool: Any, table: str) -> int:
        return state["active_products"] if table == "product_master" else state["active_users"]

    async def _concepts(pool: Any) -> int:
        return state["concepts"]

    async def _promoted(pool: Any, window: str) -> int:
        return state["promoted_in_window"]

    async def _promo_viol(pool: Any) -> int:
        return state["promotion_violations"]

    async def _stale(pool: Any, days: int) -> dict[str, int]:
        return state["stale"]

    async def _mismatches(pool: Any) -> dict[str, int]:
        return state["mismatches"]

    async def _source_grounding(pool: Any) -> dict[str, int]:
        return state["source_grounding"]

    async def _source_stats(pool: Any) -> dict[str, int]:
        return state["source_review_stats"]

    async def _collision(pool: Any) -> dict[str, int]:
        return state["collision"]

    async def _clean_join(pool: Any) -> int:
        return state["clean_join_leaks"]

    monkeypatch.setattr(contract_validator, "_count_active_rows", _active)
    monkeypatch.setattr(contract_validator, "_count_concepts", _concepts)
    monkeypatch.setattr(contract_validator, "_count_promoted_signals_in_window", _promoted)
    monkeypatch.setattr(contract_validator, "_count_promotion_invariant_violations", _promo_viol)
    monkeypatch.setattr(contract_validator, "_count_stale_active_violations", _stale)
    monkeypatch.setattr(contract_validator, "_count_product_id_mismatches", _mismatches)
    monkeypatch.setattr(contract_validator, "_count_source_grounding_violations", _source_grounding)
    monkeypatch.setattr(contract_validator, "_count_source_review_stats_readiness", _source_stats)
    monkeypatch.setattr(
        contract_validator, "_count_source_identity_collision_violations", _collision
    )
    monkeypatch.setattr(
        contract_validator, "_count_collision_clean_join_leaks", _clean_join
    )
    return state


@pytest.mark.asyncio
async def test_collision_checks_run_by_default_and_pass_when_clean(
    _stub_collision: dict[str, Any],
) -> None:
    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.OK
    # Additive: counts and checks are always present when enabled by default.
    assert result.counts["source_identity_collision.unmarked_structural"] == 0
    assert result.counts["source_identity_collision.marked"] == 0
    assert result.counts["source_identity_collision.clean_join_leaks"] == 0
    assert any(
        c.name == "invariant.source_identity_collision.unmarked_structural"
        for c in result.checks
    )
    assert any(
        c.name == "invariant.source_identity_collision.unmarked_shared_source_id"
        for c in result.checks
    )
    assert any(
        c.name == "report.source_identity_collision.marked" for c in result.checks
    )


@pytest.mark.asyncio
async def test_unmarked_structural_collision_is_invalid(
    _stub_collision: dict[str, Any],
) -> None:
    _stub_collision["collision"]["unmarked_structural"] = 2

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.INVALID
    check = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.unmarked_structural"
    )
    assert check.status == ContractStatus.INVALID
    assert check.actual == 2
    assert "SOURCE_KEY_COLLISION" in check.message
    assert result.counts["source_identity_collision.unmarked_structural"] == 2


@pytest.mark.asyncio
async def test_unmarked_shared_source_id_collision_is_invalid(
    _stub_collision: dict[str, Any],
) -> None:
    _stub_collision["collision"]["unmarked_shared_source_id"] = 1

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.INVALID
    check = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.unmarked_shared_source_id"
    )
    assert check.status == ContractStatus.INVALID
    assert check.actual == 1
    assert "shared source_product_id" in check.message


@pytest.mark.asyncio
async def test_marked_collision_passes_and_is_reported(
    _stub_collision: dict[str, Any],
) -> None:
    """A correctly-marked collision must not fail; its count is reported."""
    _stub_collision["collision"]["marked"] = 1

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.OK
    report = next(
        c for c in result.checks
        if c.name == "report.source_identity_collision.marked"
    )
    assert report.status == ContractStatus.OK
    assert report.actual == 1
    assert result.counts["source_identity_collision.marked"] == 1


@pytest.mark.asyncio
async def test_collision_product_in_clean_join_target_is_invalid(
    _stub_collision: dict[str, Any],
) -> None:
    _stub_collision["clean_join_leaks"] = 3

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.INVALID
    check = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.clean_join"
    )
    assert check.status == ContractStatus.INVALID
    assert check.actual == 3
    assert "product_review_stats" in check.message


@pytest.mark.asyncio
async def test_collision_checks_skipped_when_disabled(
    _stub_collision: dict[str, Any],
) -> None:
    """Opt-out keeps old semantics: even a real unmarked collision is ignored."""
    _stub_collision["collision"]["unmarked_structural"] = 99
    _stub_collision["clean_join_leaks"] = 99

    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        enforce_source_identity_collision=False,
    )

    assert result.status == ContractStatus.OK
    assert "source_identity_collision.unmarked_structural" not in result.counts
    assert not any(
        c.name.startswith("invariant.source_identity_collision") for c in result.checks
    )


@pytest.mark.asyncio
async def test_collision_and_marked_coexist(
    _stub_collision: dict[str, Any],
) -> None:
    """Marked rows reported while an unmarked sibling still fails."""
    _stub_collision["collision"]["marked"] = 1
    _stub_collision["collision"]["unmarked_structural"] = 1

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.INVALID
    report = next(
        c for c in result.checks
        if c.name == "report.source_identity_collision.marked"
    )
    assert report.actual == 1
    unmarked = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.unmarked_structural"
    )
    assert unmarked.status == ContractStatus.INVALID


# ---------------------------------------------------------------------------
# Unit: pipeline detection log tally (pure in-memory)
# ---------------------------------------------------------------------------


def test_count_collision_detections_counts_marked_and_unmarked() -> None:
    product_masters = {
        "1": {  # clean
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_product_id": "1",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
        "35119": {  # marked collision
            "source_channel": "031,036",
            "source_key_type": "source_key_collision",
            "source_product_id": "35119",
            "source_truth_quality": "SOURCE_KEY_COLLISION",
        },
        "99": {  # unmarked structural collision (comma channel, not marked)
            "source_channel": "031,048",
            "source_key_type": "ecp_onln_prd_srno",
            "source_product_id": "99",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
    }

    counts = _count_collision_detections(product_masters)

    assert counts == {
        "marked": 1,
        "unmarked_structural": 1,
        "unmarked_shared_source_id": 0,
        "total_products": 3,
    }


def test_count_collision_detections_flags_marker_prefix_source_product_id() -> None:
    product_masters = {
        "x": {
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_product_id": "source_key_collision:x",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
    }

    counts = _count_collision_detections(product_masters)

    assert counts["unmarked_structural"] == 1
    assert counts["marked"] == 0


def test_count_collision_detections_flags_shared_source_product_id() -> None:
    """Two distinct product_ids claiming one source_product_id under different
    (source_channel, source_key_type) identities, at least one unmarked, is a
    shared-source_product_id collision group — mirrors the validator's SQL
    GROUP BY check so the full-load log is not blind to this class."""
    product_masters = {
        "A": {
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_product_id": "shared",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
        "B": {
            "source_channel": "036",
            "source_key_type": "chn_prd_cd",
            "source_product_id": "shared",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
    }

    counts = _count_collision_detections(product_masters)

    assert counts["unmarked_shared_source_id"] == 1
    # Neither row has a structural signature (no comma channel / marker).
    assert counts["unmarked_structural"] == 0
    assert counts["total_products"] == 2


def test_count_collision_detections_empty() -> None:
    assert _count_collision_detections({}) == {
        "marked": 0,
        "unmarked_structural": 0,
        "unmarked_shared_source_id": 0,
        "total_products": 0,
    }


# ---------------------------------------------------------------------------
# Behavioural PG coverage (skipped unless GRAPHRAPPING_TEST_DATABASE_URL set)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

pg_only = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_collision_{uuid.uuid4().hex}"

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
        await migrate(pool)
        yield pool, schema
    finally:
        if pool is not None:
            await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


async def _insert_product(
    pool: asyncpg.Pool,
    *,
    product_id: str,
    source_channel: str | None,
    source_key_type: str | None,
    source_product_id: str | None,
    source_truth_quality: str,
    is_active: bool = True,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_master (
                product_id, product_name, source_channel, source_key_type,
                source_product_id, source_truth_quality, is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            product_id,
            f"product {product_id}",
            source_channel,
            source_key_type,
            source_product_id,
            source_truth_quality,
            is_active,
        )


async def _insert_review_stats(pool: asyncpg.Pool, *, product_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_review_stats (
                product_id, source_channel, source_key_type,
                source_review_count_6m, source_review_score_count_6m,
                source_avg_rating_6m, source_review_count_all,
                source_review_score_count_all, source_avg_rating_all, source
            )
            VALUES ($1, '031', 'ecp_onln_prd_srno', 10, 10, 4.5, 20, 20, 4.5, 'test')
            """,
            product_id,
        )


@pg_only
@pytest.mark.asyncio
async def test_pg_clean_catalog_has_no_collision_violation(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _ = pg_pool
    await _insert_product(
        pool, product_id="1", source_channel="031",
        source_key_type="ecp_onln_prd_srno", source_product_id="1",
        source_truth_quality="SOURCE_GROUNDED",
    )

    violations = await contract_validator._count_source_identity_collision_violations(pool)
    leaks = await contract_validator._count_collision_clean_join_leaks(pool)

    assert violations == {
        "unmarked_structural": 0,
        "unmarked_shared_source_id": 0,
        "marked": 0,
    }
    assert leaks == 0


@pg_only
@pytest.mark.asyncio
async def test_pg_marked_collision_passes_and_is_counted(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """The real 35119 shape: pre-collapsed, comma channel, correctly marked."""
    pool, _ = pg_pool
    await _insert_product(
        pool, product_id="35119", source_channel="031,036",
        source_key_type="source_key_collision", source_product_id="35119",
        source_truth_quality="SOURCE_KEY_COLLISION",
    )

    violations = await contract_validator._count_source_identity_collision_violations(pool)

    # Marked, so no unmarked violation; count is reported.
    assert violations["unmarked_structural"] == 0
    assert violations["unmarked_shared_source_id"] == 0
    assert violations["marked"] == 1

    result = await contract_validator.validate_data(pool)
    assert result.status == ContractStatus.OK
    assert result.counts["source_identity_collision.marked"] == 1


@pg_only
@pytest.mark.asyncio
async def test_pg_unmarked_structural_collision_is_invalid(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """A comma-joined source_channel without the marker is a silent inflow."""
    pool, _ = pg_pool
    await _insert_product(
        pool, product_id="35119", source_channel="031,036",
        source_key_type="ecp_onln_prd_srno", source_product_id="35119",
        source_truth_quality="SOURCE_GROUNDED",  # NOT marked
    )

    violations = await contract_validator._count_source_identity_collision_violations(pool)
    assert violations["unmarked_structural"] == 1

    result = await contract_validator.validate_data(pool)
    assert result.status == ContractStatus.INVALID
    check = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.unmarked_structural"
    )
    assert check.status == ContractStatus.INVALID


@pg_only
@pytest.mark.asyncio
async def test_pg_unmarked_shared_source_product_id_is_invalid(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Two product_ids claiming one source_product_id across two identities."""
    pool, _ = pg_pool
    await _insert_product(
        pool, product_id="A", source_channel="031",
        source_key_type="ecp_onln_prd_srno", source_product_id="shared",
        source_truth_quality="SOURCE_GROUNDED",
    )
    await _insert_product(
        pool, product_id="B", source_channel="036",
        source_key_type="chn_prd_cd", source_product_id="shared",
        source_truth_quality="SOURCE_GROUNDED",
    )

    violations = await contract_validator._count_source_identity_collision_violations(pool)
    assert violations["unmarked_shared_source_id"] == 1

    result = await contract_validator.validate_data(pool)
    assert result.status == ContractStatus.INVALID


@pg_only
@pytest.mark.asyncio
async def test_pg_collision_product_in_review_stats_is_invalid(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """A marked collision product must not own a product_review_stats row."""
    pool, _ = pg_pool
    await _insert_product(
        pool, product_id="35119", source_channel="031,036",
        source_key_type="source_key_collision", source_product_id="35119",
        source_truth_quality="SOURCE_KEY_COLLISION",
    )
    await _insert_review_stats(pool, product_id="35119")

    leaks = await contract_validator._count_collision_clean_join_leaks(pool)
    assert leaks == 1

    result = await contract_validator.validate_data(pool)
    assert result.status == ContractStatus.INVALID
    check = next(
        c for c in result.checks
        if c.name == "invariant.source_identity_collision.clean_join"
    )
    assert check.status == ContractStatus.INVALID
