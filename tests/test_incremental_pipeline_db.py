"""
Wave 5.1: `run_incremental_to_db` end-to-end against real Postgres.

Validates the FULL-load watermark seed (Wave 5.1 Task 4 follow-up) and the
incremental wrap's invariants:
  - FULL → first incremental: 0 reviews processed, watermark preserved
  - Synthetic new review inserted: incremental advances watermark + emits signals
  - Same incremental twice: idempotent
  - Validator opt-in propagates

Auto-skipped when GRAPHRAPPING_TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.common.config_loader import load_predicate_contracts
from src.db.contract_validator import ContractStatus
from src.db.unit_of_work import UnitOfWork
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
    # Wave 5.4: module marker wins over CLI; bumped to 600s for the same
    # safety margin used by test_full_load_db (single full-load seed + an
    # incremental call can approach the prior 360s cap on CI).
    pytest.mark.timeout(600),
]

MOCK = Path(__file__).parent.parent / "mockdata"


def _mock_inputs() -> tuple[list[dict], dict]:
    products = json.loads((MOCK / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = json.loads((MOCK / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    return products, users


def _build_incremental_context(products: list[dict]) -> dict[str, Any]:
    """Rebuild the product_index / normalizers / registry needed by
    `run_incremental_to_db`. Mirrors the final 906-review full-load baseline,
    scoped to what the incremental wrap needs (no review loader / no
    quarantine).
    """
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
async def pg_pool_with_full_load():
    """Per-test schema seeded with the final 906-review full load.
    Yields (pool, schema_name, products, users)."""
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_t51_{uuid.uuid4().hex}"

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()

    pool = await asyncpg.create_pool(
        TEST_DATABASE_URL, min_size=1, max_size=2,
        server_settings={"search_path": schema},
    )

    products, users = _mock_inputs()
    config = FullLoadConfig(
        review_json_path=str(MOCK / "review_triples_raw.json"),
        product_es_records=products, user_profiles=users, kg_mode="off",
    )
    await run_full_load_to_db(pool, config, validate_after=False)

    try:
        yield pool, schema, products, users
    finally:
        await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


@pytest.mark.asyncio
async def test_full_load_seeds_watermark(pg_pool_with_full_load) -> None:
    """Wave 5.1 Task 4 follow-up: FULL load writes pipeline_run watermark
    so the first incremental run after it is a no-op."""
    pool, _schema, _products, _users = pg_pool_with_full_load

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_type, status, watermark_ts, watermark_rid "
            "FROM pipeline_run ORDER BY run_id DESC LIMIT 1"
        )
    assert row["run_type"] == "FULL"
    assert row["status"] == "COMPLETED"
    assert row["watermark_ts"] is not None, "FULL load must seed watermark_ts"
    assert row["watermark_rid"], "FULL load must seed watermark_rid"


@pytest.mark.asyncio
async def test_first_incremental_after_full_is_noop(pg_pool_with_full_load) -> None:
    """FULL seeded the watermark → first incremental sees no changed reviews."""
    pool, _schema, products, _users = pg_pool_with_full_load
    ctx = _build_incremental_context(products)

    result = await run_incremental_to_db(pool, **ctx, validate_after=False)

    assert result.in_memory["status"] == "COMPLETED"
    assert result.in_memory["review_count"] == 0
    assert result.in_memory["signal_count"] == 0
    assert result.persisted["review_count"] == 0


@pytest.mark.asyncio
async def test_incremental_processes_newly_inserted_review(pg_pool_with_full_load) -> None:
    """Insert a synthetic review (review_raw + child rows) → incremental
    picks it up, advances watermark, emits signals."""
    pool, _schema, products, _users = pg_pool_with_full_load
    ctx = _build_incremental_context(products)

    # Capture pre-incremental state
    async with pool.acquire() as conn:
        wm_before = await conn.fetchrow(
            "SELECT watermark_ts, watermark_rid FROM pipeline_run "
            "WHERE status='COMPLETED' ORDER BY run_id DESC LIMIT 1"
        )

    # Insert a synthetic review with all 4 child rows so
    # load_full_review_snapshot returns non-empty.
    new_review_id = "wave5-test-review-001"
    new_ts = datetime.now(timezone.utc)
    sample_product = next(iter(ctx["product_masters"].values()))
    sample_brand = sample_product.get("brand_name") or "TestBrand"
    sample_prod_name = sample_product.get("product_name") or "TestProduct"

    async with UnitOfWork(pool) as uow:
        await uow.execute(
            """
            INSERT INTO review_raw (review_id, source, review_text,
                brand_name_raw, product_name_raw,
                event_time_utc, event_time_source, raw_payload,
                review_version, is_active, created_at, updated_at)
            VALUES ($1, 'synthetic', $2, $3, $4, $5, 'SOURCE_CREATED',
                    $6, 1, true, $5, $5)
            """,
            new_review_id, "테스트 리뷰: 발색이 정말 좋아요.",
            sample_brand, sample_prod_name, new_ts,
            json.dumps({
                "text": "테스트 리뷰: 발색이 정말 좋아요.",
                "brnd_nm": sample_brand,
                "prod_nm": sample_prod_name,
            }),
        )
        # Minimal child rows so load_full_review_snapshot returns
        # has_child_rows=True (watermark would otherwise be held).
        await uow.execute(
            """
            INSERT INTO ner_raw (review_id, review_version, mention_text,
                entity_group, start_offset, end_offset)
            VALUES ($1, 1, $2, 'PRD', 0, $3)
            """,
            new_review_id, sample_prod_name, len(sample_prod_name),
        )
        await uow.execute(
            """
            INSERT INTO bee_raw (review_id, review_version, phrase_text,
                bee_attr_raw, raw_sentiment, start_offset, end_offset)
            VALUES ($1, 1, '발색이 정말 좋아요', 'color_quality', 'POS', 6, 14)
            """,
            new_review_id,
        )
        await uow.execute(
            """
            INSERT INTO rel_raw (review_id, review_version,
                subj_text, subj_group, subj_start, subj_end,
                obj_text, obj_group, obj_start, obj_end,
                relation_raw, source_type)
            VALUES ($1, 1, $2, 'PRD', 0, $3, '발색', 'BEEAttr', 6, 8,
                    'has_attribute', 'NER-BeE')
            """,
            new_review_id, sample_prod_name, len(sample_prod_name),
        )

    result = await run_incremental_to_db(pool, **ctx, validate_after=False)

    # Review must be picked up
    assert result.in_memory["review_count"] >= 1, (
        f"Expected at least 1 review processed, got {result.in_memory}"
    )

    # Watermark must advance to exactly the new review (Codex 1차 recommendation:
    # `>= wm_before` proves forward progress but not exact cursor placement).
    async with pool.acquire() as conn:
        wm_after = await conn.fetchrow(
            "SELECT watermark_ts, watermark_rid FROM pipeline_run "
            "WHERE status='COMPLETED' AND run_type='INCREMENTAL' "
            "ORDER BY run_id DESC LIMIT 1"
        )
    assert wm_after is not None
    assert wm_after["watermark_rid"] == new_review_id, (
        f"Incremental watermark_rid {wm_after['watermark_rid']!r} != "
        f"inserted review {new_review_id!r}"
    )
    assert wm_after["watermark_ts"] == new_ts, (
        f"Incremental watermark_ts {wm_after['watermark_ts']!r} != "
        f"inserted updated_at {new_ts!r}"
    )
    # Forward progress is implied by the equality + prior assertion that
    # new_ts (datetime.now(utc)) > the FULL-seeded watermark.
    assert (wm_after["watermark_ts"], wm_after["watermark_rid"]) > (
        wm_before["watermark_ts"], wm_before["watermark_rid"]
    )


@pytest.mark.asyncio
async def test_incremental_is_idempotent(pg_pool_with_full_load) -> None:
    """Running the same no-op incremental twice doesn't grow tables."""
    pool, _schema, products, _users = pg_pool_with_full_load
    ctx = _build_incremental_context(products)

    await run_incremental_to_db(pool, **ctx, validate_after=False)

    async with pool.acquire() as conn:
        signals_before = await conn.fetchval("SELECT COUNT(*) FROM wrapped_signal")
        agg_before = await conn.fetchval("SELECT COUNT(*) FROM agg_product_signal")
        quarantine_before = await conn.fetchval("SELECT COUNT(*) FROM quarantine_projection_miss")

    await run_incremental_to_db(pool, **ctx, validate_after=False)

    async with pool.acquire() as conn:
        signals_after = await conn.fetchval("SELECT COUNT(*) FROM wrapped_signal")
        agg_after = await conn.fetchval("SELECT COUNT(*) FROM agg_product_signal")
        quarantine_after = await conn.fetchval("SELECT COUNT(*) FROM quarantine_projection_miss")

    assert signals_before == signals_after
    assert agg_before == agg_after
    assert quarantine_before == quarantine_after


@pytest.mark.asyncio
async def test_incremental_validator_opt_in(pg_pool_with_full_load) -> None:
    """validate_after=True returns a ContractValidationResult."""
    pool, _schema, products, _users = pg_pool_with_full_load
    ctx = _build_incremental_context(products)

    result = await run_incremental_to_db(
        pool, **ctx,
        validate_after=True,
        validator_options={
            "expected_min_active_products": 1,
            "expected_min_active_users": 1,
            "signal_window": "all",
        },
    )

    assert result.validation is not None
    assert result.validation.status in (ContractStatus.OK, ContractStatus.EMPTY)
    assert "active_products" in result.validation.counts
