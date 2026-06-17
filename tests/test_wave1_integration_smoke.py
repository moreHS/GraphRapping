"""
Sub-task 6: Wave 1 integration smoke tests.

Each sub-task 1A~5 added its own axis test. This module validates that all
five P0 fixes work TOGETHER in a single run, catching cross-fix regressions
that single-axis tests would miss.

Three scenarios:
  TC1 (default env): batch chain — P0-1 (purchase wiring) + P0-2 (predicate
      contracts) + P0-3 (kg_mode env switch) co-active.
  TC2 (Postgres): incremental + tombstone — P0-4 (dirty helper / comparison
      target) + P0-5 (watermark early-stop).
  TC3 (Postgres): SQL prefilter — P0-6 (avoided across raw + concept).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrate import migrate
from src.db.repos.mart_repo import sql_prefilter_candidates
from src.db.unit_of_work import UnitOfWork
from src.ingest.purchase_ingest import PurchaseEvent
from src.jobs.run_full_load import FullLoadConfig, run_full_load


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = PROJECT_ROOT / "mockdata"

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

# Wave 4 Task 2: bump pytest timeout for the whole module so PG schema setup
# does not trip the repo-wide 30s default.
pytestmark = pytest.mark.timeout(120)

pgmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)


# ---------------------------------------------------------------------------
# TC1: Batch chain — P0-1 + P0-2 + P0-3 in one run_full_load() round-trip
# ---------------------------------------------------------------------------

def test_wave1_batch_chain_smoke(monkeypatch) -> None:
    """Single run_full_load with mock data + purchase events asserts:

    - P0-1: serving_user.owned_product_ids contains the purchased target.
    - P0-3: env-driven kg_mode switch yields observable output difference.
    - P0-2: PREDICATE_CONTRACT_VIOLATION quarantine entries appear in
      the KG-on path (per CSV measurement: ≥1).

    Also defends against a future fixture change where pre-existing OWNS_*
    facts would mask the wiring (precondition assertion at the top).
    """
    # Conftest autouse already clears env, but be explicit.
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)

    products = json.loads(
        (MOCK_DIR / "product_catalog_es.json").read_text(encoding="utf-8")
    )
    users = json.loads(
        (MOCK_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8")
    )

    target_user_id = next(iter(users.keys()))
    target_product_id = next(
        p["ONLINE_PROD_SERIAL_NUMBER"]
        for p in products
        if p.get("SALE_STATUS") == "판매중"
    )

    # Precondition (P0-1 fixture defense): mock user must not already carry
    # owned_product_ids at the input level — otherwise our wiring assertion
    # could pass even if purchase_events weren't forwarded.
    assert "owned_product_ids" not in users[target_user_id], (
        f"mock fixture changed: {target_user_id} now has owned_product_ids; "
        "P0-1 wiring assertion would be vacuous"
    )

    purchase_events = {
        target_user_id: [
            PurchaseEvent(
                purchase_event_id=f"e1_{target_user_id}",
                user_id=target_user_id,
                product_id=target_product_id,
                purchased_at="2026-04-01",
            ),
        ],
    }

    # KG-off baseline run
    r_off = run_full_load(FullLoadConfig(
        review_json_path=str(MOCK_DIR / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        purchase_events_by_user=purchase_events,
        kg_mode="off",
    ))

    # P0-1: target product flowed into serving_user.owned_product_ids
    u_off = next(u for u in r_off.serving_users if u["user_id"] == target_user_id)
    owned_pids = {
        e["id"].replace("product:", "") for e in u_off["owned_product_ids"]
    }
    assert target_product_id in owned_pids, (
        f"P0-1 wiring broken: owned_product_ids missing {target_product_id}; "
        f"got {sorted(owned_pids)}"
    )

    # KG-off baseline: no PREDICATE_CONTRACT_VIOLATION expected (legacy path
    # tends not to produce Category/Ingredient -> BEEAttr facts).
    off_violations = [
        e for e in r_off.batch_result.get("quarantine_entries", [])
        if e.table == "quarantine_projection_miss"
        and "PREDICATE_CONTRACT_VIOLATION" in (e.data.get("reason") or "")
    ]

    # KG-on run
    r_on = run_full_load(FullLoadConfig(
        review_json_path=str(MOCK_DIR / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        purchase_events_by_user=purchase_events,
        kg_mode="on",
    ))

    # P0-3: kg_mode switch must produce observable output difference.
    assert (r_off.signal_count, r_off.quarantine_count) != (
        r_on.signal_count, r_on.quarantine_count,
    ), (
        f"P0-3 wiring broken: kg_mode off vs on yielded identical counts "
        f"({r_off.signal_count}, {r_off.quarantine_count})"
    )

    # P0-2: BOTH KG-off and KG-on must surface ≥1 PREDICATE_CONTRACT_VIOLATION.
    # The final 906-review fixture carries 'Reviewer' / 'Review Target'
    # placeholders that the canonicalizer maps to ReviewerProxy, which triggers
    # contract violations in both paths.
    on_violations = [
        e for e in r_on.batch_result.get("quarantine_entries", [])
        if e.table == "quarantine_projection_miss"
        and "PREDICATE_CONTRACT_VIOLATION" in (e.data.get("reason") or "")
    ]
    assert len(off_violations) >= 1, (
        "P0-2 wiring broken: KG-off produced 0 PREDICATE_CONTRACT_VIOLATION "
        "entries; expected >=1 from final 906-review placeholder mapping."
    )
    assert len(on_violations) >= 1, (
        "P0-2 wiring broken: KG-on path produced 0 PREDICATE_CONTRACT_VIOLATION "
        "entries; expected >=1 from final 906-review CSV measurement"
    )


# ---------------------------------------------------------------------------
# Postgres fixture (shared for TC2/TC3)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_w1_{uuid.uuid4().hex}"

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


# ---------------------------------------------------------------------------
# TC2: Incremental + tombstone chain — P0-4 + P0-5
# ---------------------------------------------------------------------------

@pgmark
async def test_wave1_incremental_chain_smoke(pg_pool) -> None:
    """End-to-end PG smoke for incremental path:

    - P0-5: rv_skip (no child rows) causes watermark to hold at rv_ok cursor.
    - P0-4: tombstoning a review with COMPARED_WITH_SIGNAL returns dirty set
      containing both target and the comparison dst.
    """
    from src.jobs.run_incremental_pipeline import handle_tombstone, run_incremental
    from src.link.product_matcher import ProductIndex
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.wrap.projection_registry import ProjectionRegistry

    pool, _ = pg_pool
    await migrate(pool)

    ts_ok = datetime(2026, 4, 1, tzinfo=timezone.utc)
    ts_skip = datetime(2026, 4, 2, tzinfo=timezone.utc)

    async with pool.acquire() as conn:
        # rv_ok with NER child row → has_child_rows=True
        await conn.execute(
            """
            INSERT INTO review_raw (review_id, source, source_site,
                brand_name_raw, product_name_raw, review_text,
                event_time_utc, raw_payload, review_version, is_active,
                created_at, updated_at)
            VALUES ($1, 'test', 'test', '', '', 'ok',
                $2, '{}'::jsonb, 1, true, $2, $2)
            """,
            "rv_ok", ts_ok,
        )
        await conn.execute(
            """
            INSERT INTO ner_raw (review_id, review_version, mention_text, entity_group,
                start_offset, end_offset, raw_sentiment, is_placeholder, placeholder_type)
            VALUES ($1, 1, 'X', 'PRD', 0, 1, NULL, false, NULL)
            """,
            "rv_ok",
        )
        # rv_skip with no child rows → skip path
        await conn.execute(
            """
            INSERT INTO review_raw (review_id, source, source_site,
                brand_name_raw, product_name_raw, review_text,
                event_time_utc, raw_payload, review_version, is_active,
                created_at, updated_at)
            VALUES ($1, 'test', 'test', '', '', 'skip',
                $2, '{}'::jsonb, 1, true, $2, $2)
            """,
            "rv_skip", ts_skip,
        )

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
        kg_mode="off",
    )

    # P0-5: skipped_count=1, watermark must NOT pass rv_skip
    assert result["skipped_count"] == 1
    assert result["watermark"]["review_id"] == "rv_ok"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT watermark_ts, watermark_rid FROM pipeline_run WHERE run_id = $1",
            result["run_id"],
        )
    assert row is not None
    assert row["watermark_rid"] == "rv_ok"
    assert row["watermark_ts"] < ts_skip

    # P0-4: tombstone a review with COMPARED_WITH_SIGNAL → dirty includes
    # both target and comparison.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO review_raw (review_id, source, source_site,
                brand_name_raw, product_name_raw, review_text,
                event_time_utc, raw_payload, review_version, is_active,
                created_at, updated_at)
            VALUES ($1, 'test', 'test', '', '', 'cmp',
                $2, '{}'::jsonb, 1, true, $2, $2)
            """,
            "rv_cmp", ts_ok,
        )
        await conn.execute(
            """
            INSERT INTO wrapped_signal (
                signal_id, review_id, target_product_id, source_fact_ids,
                signal_family, edge_type, dst_type, dst_id, dst_ref_kind,
                polarity, weight, registry_version)
            VALUES ($1, $2, $3, '{}',
                'COMPARISON', 'COMPARED_WITH_SIGNAL', 'Product', $4, 'ENTITY',
                'NEU', 1.0, 'test')
            """,
            "sig_cmp", "rv_cmp", "P_target", "product:P_other",
        )

    dirty = await handle_tombstone(pool, "rv_cmp", matched_product_id="P_target")
    assert "P_target" in dirty
    assert "P_other" in dirty, (
        "P0-4 wiring broken: tombstone dirty set missing comparison target"
    )


# ---------------------------------------------------------------------------
# TC3: SQL prefilter chain — P0-6 (avoided across raw + concept-IRI)
# ---------------------------------------------------------------------------

@pgmark
async def test_wave1_sql_prefilter_chain_smoke(pg_pool) -> None:
    """SQL prefilter must handle BOTH avoided ID domains and still admit clean
    products that match the preferred brand."""
    pool, _ = pg_pool
    await migrate(pool)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO serving_product_profile
                (product_id, ingredient_ids, ingredient_concept_ids,
                 brand_concept_ids, category_concept_ids, main_benefit_concept_ids)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, NULL, NULL)
            """,
            "P_clean",
            ["safe"],
            json.dumps(["concept:Ingredient:safe"]),
            json.dumps(["concept:Brand:b1"]),
        )
        await conn.execute(
            """
            INSERT INTO serving_product_profile
                (product_id, ingredient_ids, ingredient_concept_ids,
                 brand_concept_ids, category_concept_ids, main_benefit_concept_ids)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, NULL, NULL)
            """,
            "P_bad_concept",
            [],
            json.dumps(["concept:Ingredient:badx"]),
            json.dumps(["concept:Brand:b1"]),
        )
        await conn.execute(
            """
            INSERT INTO serving_product_profile
                (product_id, ingredient_ids, ingredient_concept_ids,
                 brand_concept_ids, category_concept_ids, main_benefit_concept_ids)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, NULL, NULL)
            """,
            "P_bad_raw",
            ["badx"],
            json.dumps([]),
            json.dumps(["concept:Brand:b1"]),
        )

    async with UnitOfWork(pool) as uow:
        result = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=["concept:Ingredient:badx", "badx"],
            preferred_concept_ids=["concept:Brand:b1"],
        )

    result_set = set(result)
    assert "P_clean" in result_set
    assert "P_bad_concept" not in result_set, (
        "P0-6 wiring broken: concept-IRI avoided did not exclude"
    )
    assert "P_bad_raw" not in result_set, (
        "P0-6 wiring broken: raw avoided did not exclude"
    )
