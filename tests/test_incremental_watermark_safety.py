"""
Sub-task 4 (P0-5) incremental watermark safety tests.

Pure-Python unit tests for `_compute_watermark()` + 1 Postgres integration
test verifying the actual `pipeline_run` row reflects early-stop semantics.

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrate import migrate
from src.jobs.run_incremental_pipeline import _compute_watermark


def _t(day: int) -> datetime:
    """Helper: build a fixed UTC timestamp for the given April 2026 day."""
    return datetime(2026, 4, day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Unit tests: _compute_watermark()
# ---------------------------------------------------------------------------

def test_no_skip_uses_last_processed_review() -> None:
    r1 = {"review_id": "rv1", "updated_at": _t(1)}
    r2 = {"review_id": "rv2", "updated_at": _t(2)}
    ts, rid = _compute_watermark(
        changed=[r1, r2],
        skipped_reviews=set(),
        last_processed_review=r2,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    assert (ts, rid) == (r2["updated_at"], "rv2")


def test_no_skip_falls_back_to_changed_last_when_no_last_processed() -> None:
    """No skip + no last_processed_review (e.g. all tombstones not tracked) →
    uses changed[-1] as before."""
    r1 = {"review_id": "rv1", "updated_at": _t(1)}
    r2 = {"review_id": "rv2", "updated_at": _t(2)}
    ts, rid = _compute_watermark(
        changed=[r1, r2],
        skipped_reviews=set(),
        last_processed_review=None,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    assert (ts, rid) == (r2["updated_at"], "rv2")


def test_all_skipped_keeps_previous_watermark() -> None:
    prev_ts = datetime(2026, 3, 31, tzinfo=timezone.utc)  # earlier than any in `changed`
    r1 = {"review_id": "rv1", "updated_at": _t(1)}
    r2 = {"review_id": "rv2", "updated_at": _t(2)}
    ts, rid = _compute_watermark(
        changed=[r1, r2],
        skipped_reviews={"rv1", "rv2"},
        last_processed_review=None,
        previous_wm_ts=prev_ts,
        previous_wm_rid="rv0",
    )
    assert (ts, rid) == (prev_ts, "rv0")


def test_early_skip_holds_at_earliest_skipped() -> None:
    r1 = {"review_id": "rv1", "updated_at": _t(1)}
    r2 = {"review_id": "rv2", "updated_at": _t(2)}  # skip
    r3 = {"review_id": "rv3", "updated_at": _t(3)}  # success
    ts, rid = _compute_watermark(
        changed=[r1, r2, r3],
        skipped_reviews={"rv2"},
        last_processed_review=r3,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    # Only r1 is a successful candidate strictly before rv2 → wm = rv1
    assert (ts, rid) == (r1["updated_at"], "rv1")


def test_first_review_skipped_keeps_previous_watermark() -> None:
    prev_ts = datetime(2026, 3, 31, tzinfo=timezone.utc)
    r1 = {"review_id": "rv1", "updated_at": _t(1)}  # skip
    r2 = {"review_id": "rv2", "updated_at": _t(2)}  # success
    ts, rid = _compute_watermark(
        changed=[r1, r2],
        skipped_reviews={"rv1"},
        last_processed_review=r2,
        previous_wm_ts=prev_ts,
        previous_wm_rid="rv0",
    )
    # No candidates before rv1 → preserve previous
    assert (ts, rid) == (prev_ts, "rv0")


def test_success_tombstone_skip_mixed_holds_at_earliest_skip() -> None:
    r1 = {"review_id": "rv1", "updated_at": _t(1)}  # success
    r2 = {"review_id": "rv2", "updated_at": _t(2)}  # tombstone (success-class)
    r3 = {"review_id": "rv3", "updated_at": _t(3)}  # skip
    r4 = {"review_id": "rv4", "updated_at": _t(4)}  # success
    ts, rid = _compute_watermark(
        changed=[r1, r2, r3, r4],
        skipped_reviews={"rv3"},
        last_processed_review=r4,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    # earliest skipped = r3 cursor; candidates = {r1, r2}; max = r2
    assert (ts, rid) == (r2["updated_at"], "rv2")


def test_first_run_first_review_skipped_returns_none() -> None:
    """First run + first review skipped → preserve (None, None) — caller writes
    NULL to pipeline_run, no fake (run_start, '') watermark."""
    r1 = {"review_id": "rv1", "updated_at": _t(1)}
    ts, rid = _compute_watermark(
        changed=[r1],
        skipped_reviews={"rv1"},
        last_processed_review=None,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    assert (ts, rid) == (None, None)


def test_same_updated_at_sort_by_review_id() -> None:
    """When multiple reviews share the same updated_at, cursor ordering by
    review_id (lexicographic) must hold."""
    same_ts = _t(1)
    r1 = {"review_id": "rv_a", "updated_at": same_ts}  # success
    r2 = {"review_id": "rv_b", "updated_at": same_ts}  # skip
    r3 = {"review_id": "rv_c", "updated_at": same_ts}  # success but cursor > rv_b
    ts, rid = _compute_watermark(
        changed=[r1, r2, r3],
        skipped_reviews={"rv_b"},
        last_processed_review=r3,
        previous_wm_ts=None,
        previous_wm_rid=None,
    )
    # earliest skipped cursor = (same_ts, "rv_b")
    # candidates strictly before that = {r1 ("rv_a")}; r3 ("rv_c") is greater
    assert (ts, rid) == (same_ts, "rv_a")


# ---------------------------------------------------------------------------
# Postgres integration: TC8 (mandatory per parent plan)
# ---------------------------------------------------------------------------

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
    schema = f"graphrapping_p05_{uuid.uuid4().hex}"

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


@pgmark
async def test_run_incremental_holds_watermark_on_skip(pg_pool) -> None:
    """End-to-end: 2 reviews inserted; rv_skip has no child rows. After
    run_incremental, pipeline_run.watermark_rid stays at rv_ok (skip cursor
    is NOT passed)."""
    from src.jobs.run_incremental_pipeline import run_incremental
    from src.link.product_matcher import ProductIndex
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.wrap.projection_registry import ProjectionRegistry

    pool, _ = pg_pool
    await migrate(pool)

    ts_ok = datetime(2026, 4, 1, tzinfo=timezone.utc)
    ts_skip = datetime(2026, 4, 2, tzinfo=timezone.utc)
    rv_ok = "rv_ok"
    rv_skip = "rv_skip"

    async with pool.acquire() as conn:
        # rv_ok: with 1 NER child row (has_child_rows = True)
        await conn.execute("""
            INSERT INTO review_raw (review_id, source, source_site,
                brand_name_raw, product_name_raw, review_text,
                event_time_utc, raw_payload, review_version, is_active,
                created_at, updated_at)
            VALUES ($1, 'test', 'test', '', '', 'ok body',
                $2, '{}'::jsonb, 1, true, $2, $2)
        """, rv_ok, ts_ok)
        await conn.execute("""
            INSERT INTO ner_raw (review_id, review_version, mention_text, entity_group,
                start_offset, end_offset, raw_sentiment, is_placeholder, placeholder_type)
            VALUES ($1, 1, 'X', 'PRD', 0, 1, NULL, false, NULL)
        """, rv_ok)

        # rv_skip: NO child rows → load_full_review_snapshot returns has_child_rows=False
        await conn.execute("""
            INSERT INTO review_raw (review_id, source, source_site,
                brand_name_raw, product_name_raw, review_text,
                event_time_utc, raw_payload, review_version, is_active,
                created_at, updated_at)
            VALUES ($1, 'test', 'test', '', '', 'skip body',
                $2, '{}'::jsonb, 1, true, $2, $2)
        """, rv_skip, ts_skip)

    # Minimal collaborators for run_incremental.
    bee = BEENormalizer()
    bee.load_dictionaries()
    rel = RelationCanonicalizer()
    rel.load()
    reg = ProjectionRegistry()
    reg.load()
    pidx = ProductIndex.build([])

    result = await run_incremental(
        pool=pool,
        product_index=pidx,
        product_masters={},
        concept_links={},
        bee_normalizer=bee,
        relation_canonicalizer=rel,
        projection_registry=reg,
        predicate_contracts={},
    )

    # rv_skip must be reported skipped; watermark must not pass it.
    assert result["skipped_count"] == 1, result
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT watermark_ts, watermark_rid FROM pipeline_run WHERE run_id = $1",
            result["run_id"],
        )
    assert row is not None
    assert row["watermark_rid"] == rv_ok, (
        f"watermark must stay at rv_ok, got {row['watermark_rid']!r}"
    )
    assert row["watermark_ts"] == ts_ok
    # Stricter: watermark_ts is strictly less than the skipped review's ts.
    assert row["watermark_ts"] < ts_skip
