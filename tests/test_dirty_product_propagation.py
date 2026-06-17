"""
Sub-task 3 (P0-4) dirty product propagation tests.

Pure-Python unit tests for the normalizer + Postgres integration tests for
the actual persist_review_bundle / handle_tombstone wiring.

Postgres tests require GRAPHRAPPING_TEST_DATABASE_URL (auto-skipped otherwise).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.canonical.canonical_fact_builder import CanonicalEntity, CanonicalFact
from src.common.enums import ObjectRefKind
from src.common.ids import make_concept_iri, make_product_iri
from src.db.migrate import migrate
from src.db.persist import persist_review_bundle
from src.db.persist_bundle import ReviewPersistBundle
from src.db.repos import signal_repo
from src.db.repos.signal_repo import normalize_dst_to_raw_product_id
from src.db.unit_of_work import UnitOfWork
from src.wrap.signal_emitter import WrappedSignal


# ---------------------------------------------------------------------------
# Pure-Python unit tests for the normalizer (no DB required)
# ---------------------------------------------------------------------------

def test_normalize_dst_strips_product_prefix() -> None:
    assert normalize_dst_to_raw_product_id("product:P1") == "P1"


def test_normalize_dst_strips_concept_product_prefix() -> None:
    assert normalize_dst_to_raw_product_id("concept:Product:P1") == "P1"


def test_normalize_dst_passes_raw_through() -> None:
    assert normalize_dst_to_raw_product_id("P1") == "P1"
    assert normalize_dst_to_raw_product_id("PRD_41") == "PRD_41"


def test_normalize_dst_skips_non_product_iris() -> None:
    """Non-product IRIs must NOT leak into the dirty set."""
    assert normalize_dst_to_raw_product_id("mention:rv:0") is None
    assert normalize_dst_to_raw_product_id("placeholder:reviewer:P1") is None
    assert normalize_dst_to_raw_product_id("concept:Brand:b1") is None
    assert normalize_dst_to_raw_product_id("concept:Category:cat_1") is None


def test_normalize_dst_skips_empty() -> None:
    assert normalize_dst_to_raw_product_id("") is None


def test_normalize_dst_skips_prefix_only() -> None:
    """Guard against degenerate input like "product:" with empty suffix."""
    assert normalize_dst_to_raw_product_id("product:") is None
    assert normalize_dst_to_raw_product_id("concept:Product:") is None


def test_normalize_dst_idempotent_for_raw_outputs() -> None:
    """Calling twice on the same input yields the same output."""
    for src in ("product:P1", "concept:Product:P1", "P1"):
        first = normalize_dst_to_raw_product_id(src)
        # raw outputs are stable: passing them in again yields themselves
        assert normalize_dst_to_raw_product_id(first) == first


# ---------------------------------------------------------------------------
# Postgres integration tests (skipped without GRAPHRAPPING_TEST_DATABASE_URL)
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
    schema = f"graphrapping_p04_{uuid.uuid4().hex}"

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


# ----- bundle builders ------------------------------------------------------


def _base_review_raw(review_id: str, target: str) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "source": "test_p0_4",
        "source_review_key": review_id,
        "source_site": "local",
        "brand_name_raw": "B",
        "product_name_raw": "Prod",
        "review_text": "...",
        "reviewer_proxy_id": f"reviewer_proxy:{review_id}",
        "identity_stability": "REVIEW_LOCAL",
        "event_time_utc": datetime(2026, 4, 25, tzinfo=timezone.utc),
        "event_time_raw_text": None,
        "event_tz": None,
        "event_time_source": "PROCESSING_TIME",
        "raw_payload": {"contract": "p0_4"},
    }


def _base_link(review_id: str, target: str) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "source_brand": "B",
        "source_product_name": "Prod",
        "matched_product_id": target,
        "match_status": "NORM",
        "match_score": 1.0,
        "match_method": "norm_exact",
    }


def _make_target_only_bundle(review_id: str, target: str) -> ReviewPersistBundle:
    """Target product only — no comparison/co-use signals."""
    attr_iri = make_concept_iri("BEEAttr", "hydration")
    fact = CanonicalFact(
        fact_id=f"fact:{review_id}:attr",
        review_id=review_id,
        subject_iri=make_product_iri(target),
        predicate="has_attribute",
        object_iri=attr_iri,
        object_ref_kind=ObjectRefKind.CONCEPT.value,
        subject_type="Product",
        object_type="BEEAttr",
        polarity="POS",
        confidence=0.8,
        source_modalities=["BEE"],
    )
    sig = WrappedSignal(
        signal_id=f"signal:{review_id}:attr",
        review_id=review_id,
        user_id=None,
        target_product_id=target,
        source_fact_ids=[fact.fact_id],
        signal_family="BEE_ATTR",
        edge_type="HAS_BEE_ATTR_SIGNAL",
        dst_type="BEEAttr",
        dst_id=attr_iri,
        dst_ref_kind=ObjectRefKind.CONCEPT.value,
        bee_attr_id=attr_iri,
        polarity="POS",
        weight=0.8,
        registry_version="p0_4_test",
        window_ts=datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    return ReviewPersistBundle(
        review_raw=_base_review_raw(review_id, target),
        review_catalog_link=_base_link(review_id, target),
        ner_rows=[], bee_rows=[], rel_rows=[],
        canonical_entities=[CanonicalEntity(
            entity_iri=make_product_iri(target),
            entity_type="Product",
            canonical_name="Prod",
            canonical_name_norm="prod",
        )],
        canonical_facts=[fact],
        wrapped_signals=[sig],
        signal_evidence_rows=[{
            "signal_id": sig.signal_id,
            "fact_id": fact.fact_id,
            "evidence_rank": 0,
            "contribution": 1.0,
        }],
        quarantine_entries=[],
        review_id=review_id,
        matched_product_id=target,
    )


def _make_comparison_bundle(
    review_id: str, target: str, comparison: str,
) -> ReviewPersistBundle:
    """target + one COMPARED_WITH_SIGNAL signal."""
    bundle = _make_target_only_bundle(review_id, target)
    cmp_sig = WrappedSignal(
        signal_id=f"signal:{review_id}:cmp",
        review_id=review_id,
        user_id=None,
        target_product_id=target,
        source_fact_ids=[],
        signal_family="COMPARISON",
        edge_type="COMPARED_WITH_SIGNAL",
        dst_type="Product",
        dst_id=make_product_iri(comparison),  # "product:<comparison>"
        dst_ref_kind=ObjectRefKind.ENTITY.value,
        polarity="NEU",
        weight=1.0,
        registry_version="p0_4_test",
        window_ts=datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    bundle.wrapped_signals.append(cmp_sig)
    return bundle


def _make_comparison_and_couse_bundle(
    review_id: str, target: str, comparison: str, coused: str,
) -> ReviewPersistBundle:
    bundle = _make_comparison_bundle(review_id, target, comparison)
    co_sig = WrappedSignal(
        signal_id=f"signal:{review_id}:co",
        review_id=review_id,
        user_id=None,
        target_product_id=target,
        source_fact_ids=[],
        signal_family="COUSED_PRODUCT",
        edge_type="USED_WITH_PRODUCT_SIGNAL",
        dst_type="Product",
        dst_id=make_product_iri(coused),
        dst_ref_kind=ObjectRefKind.ENTITY.value,
        polarity="NEU",
        weight=1.0,
        registry_version="p0_4_test",
        window_ts=datetime(2026, 4, 25, tzinfo=timezone.utc),
    )
    bundle.wrapped_signals.append(co_sig)
    return bundle


def _make_dst_id_variants_bundle(
    review_id: str, target: str, dst_ids: list[str],
) -> ReviewPersistBundle:
    """target + multiple COMPARED_WITH_SIGNAL signals with diverse dst_id formats."""
    bundle = _make_target_only_bundle(review_id, target)
    for i, dst_id in enumerate(dst_ids):
        sig = WrappedSignal(
            signal_id=f"signal:{review_id}:variant_{i}",
            review_id=review_id,
            user_id=None,
            target_product_id=target,
            source_fact_ids=[],
            signal_family="COMPARISON",
            edge_type="COMPARED_WITH_SIGNAL",
            dst_type="Product",
            dst_id=dst_id,
            dst_ref_kind=ObjectRefKind.ENTITY.value,
            polarity="NEU",
            weight=1.0,
            registry_version="p0_4_test",
            window_ts=datetime(2026, 4, 25, tzinfo=timezone.utc),
        )
        bundle.wrapped_signals.append(sig)
    return bundle


# ----- TC1: persist_review_bundle reprocess removes old comparison ---------


@pgmark
async def test_persist_review_bundle_includes_old_comparison_dirty(pg_pool) -> None:
    """Reprocess: previously had comparison P_other; v2 drops it.
    P_other must still appear in dirty set so its aggregate refreshes."""
    pool, _ = pg_pool
    await migrate(pool)

    review_id = f"review:tc1:{uuid.uuid4().hex}"

    bundle_v1 = _make_comparison_bundle(review_id, target="P_target", comparison="P_other")
    stats_v1 = await persist_review_bundle(pool, bundle_v1)
    assert "P_target" in stats_v1["dirty_product_ids"]
    assert "P_other" in stats_v1["dirty_product_ids"], (
        "NEW comparison product must be in dirty_product_ids"
    )

    # Reprocess: comparison removed
    bundle_v2 = _make_target_only_bundle(review_id, target="P_target")
    stats_v2 = await persist_review_bundle(pool, bundle_v2)
    assert "P_other" in stats_v2["dirty_product_ids"], (
        "Removing a comparison must still flag the formerly-compared product as dirty"
    )
    assert "P_target" in stats_v2["dirty_product_ids"]


# ----- TC2: handle_tombstone includes both comparison and co-use -----------


@pgmark
async def test_handle_tombstone_includes_comparison_and_couse(pg_pool) -> None:
    """Tombstone must dirty target + comparison + co-use."""
    pool, _ = pg_pool
    await migrate(pool)

    review_id = f"review:tc2:{uuid.uuid4().hex}"
    bundle = _make_comparison_and_couse_bundle(
        review_id, target="P_target", comparison="P_cmp", coused="P_co",
    )
    await persist_review_bundle(pool, bundle)

    from src.jobs.run_incremental_pipeline import handle_tombstone
    dirty = await handle_tombstone(pool, review_id, matched_product_id="P_target")
    assert "P_target" in dirty
    assert "P_cmp" in dirty, "Comparison product must be in tombstone dirty"
    assert "P_co" in dirty, "Co-used product must be in tombstone dirty"


# ----- TC3: single-target regression --------------------------------------


@pgmark
async def test_single_target_review_dirty_unchanged(pg_pool) -> None:
    """Single-target review (no comparison/co-use) — dirty set equals
    {target_product_id} so existing pipelines aren't disrupted."""
    pool, _ = pg_pool
    await migrate(pool)

    review_id = f"review:tc3:{uuid.uuid4().hex}"
    bundle = _make_target_only_bundle(review_id, target="P_solo")
    stats = await persist_review_bundle(pool, bundle)
    assert set(stats["dirty_product_ids"]) == {"P_solo"}


# ----- TC4: helper normalizes diverse dst_id formats -----------------------


@pgmark
async def test_helper_normalizes_diverse_dst_id_formats(pg_pool) -> None:
    """Helper must strip 'product:' / 'concept:Product:', pass raw through,
    and SKIP non-product IRIs (mention:, concept:Brand:, etc.)."""
    pool, _ = pg_pool
    await migrate(pool)

    review_id = f"review:tc4:{uuid.uuid4().hex}"
    bundle = _make_dst_id_variants_bundle(
        review_id,
        target="P_target",
        dst_ids=[
            "product:P_alpha",
            "concept:Product:P_beta",
            "P_gamma",
            "mention:rv:0",            # non-product → must skip
            "concept:Brand:b1",        # non-product → must skip
        ],
    )
    await persist_review_bundle(pool, bundle)

    async with UnitOfWork(pool) as uow:
        dirty = await signal_repo.get_dirty_product_ids_for_review(uow, review_id)

    assert "P_target" in dirty
    assert "P_alpha" in dirty
    assert "P_beta" in dirty
    assert "P_gamma" in dirty
    # Non-product IRIs must NOT leak
    assert "mention:rv:0" not in dirty
    assert "concept:Brand:b1" not in dirty
    assert "b1" not in dirty
    # All return values are raw product_ids (no colons)
    assert not any(":" in d for d in dirty), (
        f"helper must return raw product_id only (no colons), got {dirty}"
    )
