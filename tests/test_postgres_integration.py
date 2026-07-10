from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.canonical.canonical_fact_builder import CanonicalEntity, CanonicalFact, FactProvenance
from src.common.enums import ObjectRefKind
from src.common.ids import make_concept_iri, make_product_iri
from src.db.migrate import DDL_ORDER, migrate
from src.db.persist import persist_aggregates, persist_review_bundle
from src.db.persist_bundle import ReviewPersistBundle
from src.db.repos.review_repo import load_full_review_snapshot
from src.db.unit_of_work import UnitOfWork
from src.wrap.signal_emitter import WrappedSignal


TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

# Wave 4 Task 2: PG-bound tests can exceed the 30s repo-wide pytest timeout
# during schema setup / parallel migrations. Bump to 120s for this module.
pytestmark = [
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
    ),
    pytest.mark.timeout(120),
]


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_it_{uuid.uuid4().hex}"

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


async def test_migrate_creates_schema_and_records_all_ddl(pg_pool: tuple[asyncpg.Pool, str]) -> None:
    pool, schema = pg_pool

    applied = await migrate(pool)

    assert applied == DDL_ORDER
    async with pool.acquire() as conn:
        versions = await conn.fetch("SELECT version FROM schema_migrations")
    assert {row["version"] for row in versions} == set(DDL_ORDER)

    assert await _has_columns(pool, schema, "rel_raw", {"raw_sentiment", "obj_keywords"})
    assert await _has_columns(
        pool,
        schema,
        "canonical_fact",
        {"evidence_kind", "fact_status", "target_linked"},
    )
    assert await _has_columns(
        pool,
        schema,
        "wrapped_signal",
        {"evidence_kind", "fact_status", "source_confidence"},
    )
    assert await _has_columns(
        pool,
        schema,
        "product_master",
        {
            "source_product_id", "source_channel", "source_key_type",
            "representative_product_name", "source_truth_source",
            "source_truth_quality", "source_review_count", "source_review_score",
        },
    )
    assert await _has_columns(
        pool,
        schema,
        "review_raw",
        {"source_product_id", "source_channel", "source_key_type", "source_rating"},
    )
    assert await _has_columns(
        pool,
        schema,
        "review_catalog_link",
        {"source_product_id", "source_channel", "source_key_type"},
    )
    assert await _has_columns(
        pool,
        schema,
        "product_review_stats",
        {
            "source_review_count_6m", "source_review_score_count_6m",
            "source_avg_rating_6m", "source_review_count_all",
            "source_review_score_count_all", "source_avg_rating_all",
        },
    )
    assert await _has_columns(
        pool,
        schema,
        "serving_product_profile",
        {
            "variant_family_id", "representative_product_name",
            "source_product_id", "source_review_count_6m",
            "source_avg_rating_all", "source_review_stats_source",
        },
    )
    assert await _has_columns(
        pool,
        schema,
        "serving_user_profile",
        {"owned_family_ids", "repurchased_family_ids"},
    )


async def test_review_bundle_persist_and_snapshot_round_trip(pg_pool: tuple[asyncpg.Pool, str]) -> None:
    pool, _schema = pg_pool
    await migrate(pool)
    bundle = _make_review_bundle(uuid.uuid4().hex)

    stats = await persist_review_bundle(pool, bundle)

    assert stats["review_id"] == bundle.review_id
    assert stats["review_version"] == 1
    assert stats["signal_count"] == 1
    assert stats["dirty_product_ids"] == [bundle.matched_product_id]

    async with UnitOfWork(pool) as uow:
        snapshot, has_child_rows = await load_full_review_snapshot(uow, bundle.review_id)

    assert has_child_rows is True
    assert snapshot is not None
    assert snapshot["source_product_id"] == bundle.review_raw["source_product_id"]
    assert snapshot["source_channel"] == "031"
    assert snapshot["source_key_type"] == "ecp_onln_prd_srno"
    assert snapshot["source_rating"] == pytest.approx(4.5)
    assert snapshot["ner"][0]["word"] == "Review Target"
    assert snapshot["bee"][0]["word"] == "hydrating finish"
    assert snapshot["relation"][0]["object"]["sentiment"] == "positive"
    assert snapshot["relation"][0]["object"]["keywords"] == ["moisture"]

    async with pool.acquire() as conn:
        raw_payload = await conn.fetchval(
            "SELECT raw_payload FROM review_raw WHERE review_id=$1",
            bundle.review_id,
        )
        fact = await conn.fetchrow(
            """
            SELECT evidence_kind, fact_status, target_linked, attribution_source, confidence
            FROM canonical_fact
            WHERE fact_id=$1
            """,
            bundle.canonical_facts[0].fact_id,
        )
        signal = await conn.fetchrow(
            """
            SELECT evidence_kind, fact_status, source_confidence, target_linked, attribution_source
            FROM wrapped_signal
            WHERE signal_id=$1
            """,
            bundle.wrapped_signals[0].signal_id,
        )
        evidence_count = await conn.fetchval(
            "SELECT COUNT(*) FROM signal_evidence WHERE signal_id=$1",
            bundle.wrapped_signals[0].signal_id,
        )

    assert _json_value(raw_payload)["contract"] == "postgres-integration"
    assert fact["evidence_kind"] == "BEE_SYNTHETIC"
    assert fact["fact_status"] == "CANONICAL_PROMOTED"
    assert fact["target_linked"] is True
    assert fact["attribution_source"] == "relation"
    assert fact["confidence"] == pytest.approx(0.8)
    assert signal["evidence_kind"] == "BEE_SYNTHETIC"
    assert signal["fact_status"] == "CANONICAL_PROMOTED"
    assert signal["source_confidence"] == pytest.approx(0.8)
    assert signal["target_linked"] is True
    assert signal["attribution_source"] == "relation"
    assert evidence_count == 1


async def test_serving_profiles_round_trip_new_fields(pg_pool: tuple[asyncpg.Pool, str]) -> None:
    pool, _schema = pg_pool
    await migrate(pool)
    suffix = uuid.uuid4().hex
    product_id = f"product-{suffix}"
    user_id = f"user-{suffix}"

    stats = await persist_aggregates(
        pool,
        agg_rows=[],
        serving_products=[
            {
                "product_id": product_id,
                "source_product_id": product_id,
                "source_channel": "031",
                "source_key_type": "ecp_onln_prd_srno",
                "brand_id": "brand-1",
                "brand_name": "Brand One",
                "category_id": "cat-cream",
                "category_name": "Cream",
                "variant_family_id": "family-hydration",
                "representative_product_name": "Hydration Cream",
                "main_benefit_ids": ["benefit-moisture"],
                "ingredient_ids": ["ingredient-ceramide"],
                "brand_concept_ids": ["concept:Brand:brand-1"],
                "top_bee_attr_ids": [{"id": "concept:BEEAttr:hydration", "score": 1.0}],
                "review_count_all": 3,
                "source_review_count_6m": 7,
                "source_review_score_count_6m": 6,
                "source_avg_rating_6m": 4.5,
                "source_review_count_all": 12,
                "source_review_score_count_all": 10,
                "source_avg_rating_all": 4.25,
                "source_review_stats_source": "snowflake:f_prd_rv_hist",
            },
        ],
        serving_users=[
            {
                "user_id": user_id,
                "age_band": "30s",
                "preferred_brand_ids": ["brand-1"],
                "owned_product_ids": [product_id],
                "owned_family_ids": ["family-hydration"],
                "repurchased_family_ids": ["family-hydration"],
            },
        ],
        user_pref_rows=[],
    )

    assert stats == {"agg_rows": 0, "serving_products": 1, "serving_users": 1, "user_prefs": 0}

    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            """
            SELECT variant_family_id, representative_product_name, top_bee_attr_ids,
                   source_product_id, source_review_count_6m, source_avg_rating_all,
                   source_review_stats_source
            FROM serving_product_profile
            WHERE product_id=$1
            """,
            product_id,
        )
        user = await conn.fetchrow(
            """
            SELECT owned_product_ids, owned_family_ids, repurchased_family_ids
            FROM serving_user_profile
            WHERE user_id=$1
            """,
            user_id,
        )

    assert product["variant_family_id"] == "family-hydration"
    assert product["representative_product_name"] == "Hydration Cream"
    assert _json_value(product["top_bee_attr_ids"])[0]["id"] == "concept:BEEAttr:hydration"
    assert product["source_product_id"] == product_id
    assert product["source_review_count_6m"] == 7
    assert float(product["source_avg_rating_all"]) == pytest.approx(4.25)
    assert product["source_review_stats_source"] == "snowflake:f_prd_rv_hist"
    assert _json_value(user["owned_product_ids"]) == [product_id]
    assert _json_value(user["owned_family_ids"]) == ["family-hydration"]
    assert _json_value(user["repurchased_family_ids"]) == ["family-hydration"]


async def test_stale_agg_signals_marked_inactive_then_revived(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """P3-8: rows older than threshold flip is_active=false; re-upsert revives."""
    from src.db.repos.mart_repo import (
        mark_stale_agg_signals_inactive,
        upsert_agg_product_signal,
        upsert_agg_user_preference,
    )
    from src.db.unit_of_work import UnitOfWork

    pool, _schema = pg_pool
    await migrate(pool)
    suffix = uuid.uuid4().hex

    fresh_pid = f"fresh-{suffix}"
    stale_pid = f"stale-{suffix}"
    fresh_uid = f"fresh-user-{suffix}"
    stale_uid = f"stale-user-{suffix}"

    async with UnitOfWork(pool) as uow:
        # Fresh agg_product_signal (last_seen_at = now)
        await upsert_agg_product_signal(uow, {
            "target_product_id": fresh_pid,
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr",
            "dst_node_id": "moisture",
            "window_type": "all",
            "review_cnt": 3, "pos_cnt": 3, "neg_cnt": 0, "neu_cnt": 0,
            "support_count": 3, "score": 1.0,
            "last_seen_at": datetime.now(timezone.utc),
        })
        # Stale agg_product_signal (last_seen_at = 200 days ago)
        await upsert_agg_product_signal(uow, {
            "target_product_id": stale_pid,
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr",
            "dst_node_id": "sticky",
            "window_type": "all",
            "review_cnt": 1, "pos_cnt": 1, "neg_cnt": 0, "neu_cnt": 0,
            "support_count": 1, "score": 1.0,
        })
        # User prefs
        await upsert_agg_user_preference(uow, {
            "user_id": fresh_uid, "preference_edge_type": "PREFERS_BRAND",
            "dst_node_id": "b1", "weight": 1.0,
        })
        await upsert_agg_user_preference(uow, {
            "user_id": stale_uid, "preference_edge_type": "PREFERS_BRAND",
            "dst_node_id": "b2", "weight": 1.0,
        })

    # Backdate the stale rows so they fall outside the 90-day window.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agg_product_signal SET last_seen_at = now() - interval '200 days' "
            "WHERE target_product_id = $1", stale_pid)
        await conn.execute(
            "UPDATE agg_user_preference SET updated_at = now() - interval '200 days' "
            "WHERE user_id = $1", stale_uid)

    async with UnitOfWork(pool) as uow:
        counts = await mark_stale_agg_signals_inactive(uow, threshold_days=90)

    assert counts["product_signals"] >= 1
    assert counts["user_preferences"] >= 1

    async with pool.acquire() as conn:
        stale_active = await conn.fetchval(
            "SELECT is_active FROM agg_product_signal WHERE target_product_id=$1",
            stale_pid,
        )
        fresh_active = await conn.fetchval(
            "SELECT is_active FROM agg_product_signal WHERE target_product_id=$1",
            fresh_pid,
        )
        stale_user_active = await conn.fetchval(
            "SELECT is_active FROM agg_user_preference WHERE user_id=$1",
            stale_uid,
        )
        fresh_user_active = await conn.fetchval(
            "SELECT is_active FROM agg_user_preference WHERE user_id=$1",
            fresh_uid,
        )

    assert stale_active is False, "stale agg_product_signal should be soft-deleted"
    assert fresh_active is True, "fresh agg_product_signal must remain active"
    assert stale_user_active is False, "stale agg_user_preference should be soft-deleted"
    assert fresh_user_active is True, "fresh agg_user_preference must remain active"

    # Revival: re-upsert flips is_active back to true (without resetting timestamps).
    async with UnitOfWork(pool) as uow:
        await upsert_agg_product_signal(uow, {
            "target_product_id": stale_pid,
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr",
            "dst_node_id": "sticky",
            "window_type": "all",
            "review_cnt": 2, "pos_cnt": 2, "neg_cnt": 0, "neu_cnt": 0,
            "support_count": 2, "score": 1.0,
            "last_seen_at": datetime.now(timezone.utc),
        })
        await upsert_agg_user_preference(uow, {
            "user_id": stale_uid, "preference_edge_type": "PREFERS_BRAND",
            "dst_node_id": "b2", "weight": 0.9,
        })

    async with pool.acquire() as conn:
        revived_signal = await conn.fetchval(
            "SELECT is_active FROM agg_product_signal WHERE target_product_id=$1",
            stale_pid,
        )
        revived_user = await conn.fetchval(
            "SELECT is_active FROM agg_user_preference WHERE user_id=$1",
            stale_uid,
        )
    assert revived_signal is True, "re-upsert must reactivate stale agg_product_signal"
    assert revived_user is True, "re-upsert must reactivate stale agg_user_preference"


async def test_search_endpoint_db_mode_over_real_serving_store(
    pg_pool: tuple[asyncpg.Pool, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 4.2 concept search served through the REAL DBServingStore.

    Seeds serving_product_profile, then calls /api/search (server.search_get) in
    db mode and asserts concept resolution + overlap over live DB rows (not a
    fake store). The ingredient axis is exercised with intentionally misaligned
    raw/concept lists — ingredient_ids (TEXT[]) and ingredient_concept_ids
    (JSONB) differ in length and order — so this also guards the concept-suffix
    (not positional) ingredient matching end-to-end through JSONB decode.
    """
    from src.web import server
    from src.web.serving_store import DBServingStore
    from src.web.state import DemoState

    pool, _schema = pg_pool
    await migrate(pool)
    suffix = uuid.uuid4().hex
    product_id = f"product-{suffix}"

    stats = await persist_aggregates(
        pool,
        agg_rows=[],
        serving_products=[
            {
                "product_id": product_id,
                "brand_id": "brand-sulwhasoo",
                "brand_name": "설화수",
                "brand_concept_ids": ["concept:Brand:설화수"],
                "category_id": "cat-cushion",
                "category_name": "쿠션",
                "category_concept_ids": ["concept:Category:쿠션"],
                # Misaligned on purpose: the raw master list differs in length and
                # order from the concept list and does not contain the concept's
                # ingredient name.
                "ingredient_ids": ["글리세린", "정제수"],
                "ingredient_concept_ids": ["concept:Ingredient:나이아신아마이드"],
            },
        ],
        serving_users=[],
        user_pref_rows=[],
    )
    assert stats["serving_products"] == 1

    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    monkeypatch.setattr(server, "_serving_store", DBServingStore(pool))

    payload = await server.search_get(query="설화수 나이아신아마이드", top_k=10)

    assert payload["resolved"] is True
    assert payload["result_count"] == 1
    result = payload["results"][0]
    assert result["product_id"] == product_id
    # overlap_concepts is the shared field name (mirrors /api/recommend); the
    # ingredient overlap proves concept-suffix matching survived JSONB decode.
    assert "brand:concept:Brand:설화수" in result["overlap_concepts"]
    assert "ingredient:concept:Ingredient:나이아신아마이드" in result["overlap_concepts"]
    assert result["overlap_concepts"] == result["matched_concepts"]
    assert "PRODUCT_MASTER_TRUTH" in result["eligibility"]["evidence_families"]


async def test_db_provenance_provider_resolves_real_persisted_chain(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Phase 2.5: DBProvenanceProvider batch-prefetch resolves the real
    ``signal_evidence → canonical_fact → fact_provenance → review_raw`` chain
    over a live persisted bundle — the real-PG counterpart to the fake-pool
    unit tests in test_db_provenance_provider.py, whose only coverage was the
    query routing, not the actual persisted rows.

    The fact's stored snippet is emptied so ``prefetch`` must fall back to the
    raw review text: this forces all three prefetch queries (signal_evidence,
    fact_provenance, review_raw) to run AND proves ``get_review_snippet``
    returns the real persisted ``review_raw.review_text``.
    """
    from src.rec.provenance_provider import DBProvenanceProvider

    pool, _schema = pg_pool
    await migrate(pool)
    bundle = _make_review_bundle(uuid.uuid4().hex)
    # Empty the fact's stored snippet → the provenance row now lacks a snippet,
    # so prefetch must pull review_raw text for the fallback (exercising query 3).
    fact = bundle.canonical_facts[0]
    bundle.canonical_facts[0] = replace(
        fact, provenance=[replace(p, snippet="") for p in fact.provenance]
    )
    await persist_review_bundle(pool, bundle)

    signal_id = bundle.wrapped_signals[0].signal_id
    fact_id = bundle.canonical_facts[0].fact_id
    review_id = bundle.review_id

    provider = DBProvenanceProvider(pool)
    await provider.prefetch([signal_id])

    # Layer 1: signal_evidence resolves the signal to its backing fact.
    evidence = await provider.get_signal_evidence(signal_id)
    assert [row["fact_id"] for row in evidence] == [fact_id]

    # Layer 2: fact_provenance for that fact, with the nullable review_id kept.
    prov = await provider.get_fact_provenance(fact_id)
    assert len(prov) == 1
    prov_row = prov[0]
    assert prov_row["review_id"] == review_id
    assert prov_row["raw_table"] == "bee_raw"

    # Layer 3: get_review_snippet returns the real persisted review_raw text.
    expected_text = bundle.review_raw["review_text"]
    assert await provider.get_review_snippet(review_id, None, None) == expected_text
    start, end = prov_row["start_offset"], prov_row["end_offset"]
    assert await provider.get_review_snippet(review_id, start, end) == expected_text[start:end]


async def test_fetch_product_signals_matches_semantic_path_over_real_load(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    """Phase 2.5: over a live load, ``fetch_product_signals`` returns the
    product's persisted signals and a semantic explanation path in the real
    ``axis:value:<IRI>`` shape resolves to that signal via
    ``signal_ids_by_concept_path``.

    This pins the 5th-round semantic-match fix (embedded-IRI recovery, proved
    64/64 on the in-memory dense_golden fixture) against real Postgres rows:
    the signal anchor is read back out of ``wrapped_signal`` rather than a
    hand-built dict.
    """
    from src.rec.explainer import ExplanationPath
    from src.rec.provenance_provider import (
        fetch_product_signals,
        signal_ids_by_concept_path,
    )

    pool, _schema = pg_pool
    await migrate(pool)
    bundle = _make_review_bundle(uuid.uuid4().hex)
    await persist_review_bundle(pool, bundle)

    product_id = bundle.matched_product_id
    signal = bundle.wrapped_signals[0]

    signals_by_product = await fetch_product_signals(pool, [product_id])
    assert product_id in signals_by_product
    fetched = signals_by_product[product_id]
    fetched_signal = next(s for s in fetched if s["signal_id"] == signal.signal_id)
    # The BEEAttr concept IRI survived the round-trip as the signal's anchor.
    assert fetched_signal["dst_id"] == signal.dst_id
    assert fetched_signal["bee_attr_id"] == signal.bee_attr_id

    # A semantic overlap path embeds the IRI as ``axis:value:<IRI>`` (the shape
    # find_semantic_matches/explain emit). _concept_path_match_key must recover
    # the trailing IRI so it normalizes to the same key as the persisted anchor.
    semantic_path = ExplanationPath(
        concept_type="semantic_bee_attr",
        concept_id=f"moisture:moist:{signal.bee_attr_id}",
        user_edge="PREFERS_BEE_ATTR",
        product_edge="HAS_BEE_ATTR_SIGNAL",
        contribution=1.0,
    )
    mapping = signal_ids_by_concept_path([semantic_path], fetched)
    assert mapping == {0: [signal.signal_id]}


async def _has_columns(
    pool: asyncpg.Pool,
    schema: str,
    table_name: str,
    expected_columns: set[str],
) -> bool:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=$1 AND table_name=$2
            """,
            schema,
            table_name,
        )
    return expected_columns.issubset({row["column_name"] for row in rows})


def _make_review_bundle(suffix: str) -> ReviewPersistBundle:
    review_id = f"review:postgres:{suffix}"
    product_id = f"product-{suffix}"
    product_iri = make_product_iri(product_id)
    attr_iri = make_concept_iri("BEEAttr", "hydration")
    fact_id = f"fact:postgres:{suffix}"
    signal_id = f"signal:postgres:{suffix}"
    event_time = datetime(2026, 4, 25, tzinfo=timezone.utc)

    fact = CanonicalFact(
        fact_id=fact_id,
        review_id=review_id,
        subject_iri=product_iri,
        predicate="has_attribute",
        object_iri=attr_iri,
        object_ref_kind=ObjectRefKind.CONCEPT.value,
        subject_type="Product",
        object_type="BEEAttr",
        polarity="POS",
        confidence=0.8,
        source_modalities=["BEE"],
        extraction_version="postgres-integration",
        registry_version="postgres-integration",
        provenance=[
            FactProvenance(
                raw_table="bee_raw",
                raw_row_id="1",
                review_id=review_id,
                snippet="hydrating finish",
                start_offset=14,
                end_offset=31,
                source_modality="BEE",
                source_domain="review",
                source_kind="raw",
            ),
        ],
        negated=False,
        intensity=1.0,
        evidence_kind="BEE_SYNTHETIC",
        fact_status="CANONICAL_PROMOTED",
        target_linked=True,
        attribution_source="relation",
    )

    signal = WrappedSignal(
        signal_id=signal_id,
        review_id=review_id,
        user_id=None,
        target_product_id=product_id,
        source_fact_ids=[fact_id],
        signal_family="BEE_ATTR",
        edge_type="HAS_BEE_ATTR_SIGNAL",
        dst_type="BEEAttr",
        dst_id=attr_iri,
        dst_ref_kind=ObjectRefKind.CONCEPT.value,
        bee_attr_id=attr_iri,
        keyword_id=None,
        polarity="POS",
        negated=False,
        intensity=1.0,
        evidence_kind="BEE_SYNTHETIC",
        fact_status="CANONICAL_PROMOTED",
        source_confidence=0.8,
        target_linked=True,
        attribution_source="relation",
        weight=0.8,
        registry_version="postgres-integration",
        window_ts=event_time,
    )

    return ReviewPersistBundle(
        review_id=review_id,
        matched_product_id=product_id,
        review_raw={
            "review_id": review_id,
            "source": "postgres-integration",
            "source_review_key": suffix,
            "source_product_id": product_id,
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "source_rating": 4.5,
            "source_site": "local",
            "brand_name_raw": "Brand One",
            "product_name_raw": "Hydration Cream",
            "review_text": "Review Target has a hydrating finish.",
            "reviewer_proxy_id": f"reviewer:{suffix}",
            "identity_stability": "REVIEW_LOCAL",
            "event_time_utc": event_time,
            "event_time_raw_text": event_time.isoformat(),
            "event_tz": "UTC",
            "event_time_source": "SOURCE_CREATED",
            "raw_payload": {"contract": "postgres-integration"},
        },
        review_catalog_link={
            "review_id": review_id,
            "source_brand": "Brand One",
            "source_product_name": "Hydration Cream",
            "source_product_id": product_id,
            "source_channel": "031",
            "source_key_type": "ecp_onln_prd_srno",
            "matched_product_id": product_id,
            "match_status": "EXACT",
            "match_score": 1.0,
            "match_method": "integration-fixture",
        },
        ner_rows=[
            {
                "review_id": review_id,
                "mention_text": "Review Target",
                "entity_group": "PRD",
                "start_offset": 0,
                "end_offset": 13,
                "raw_sentiment": "positive",
                "is_placeholder": True,
                "placeholder_type": "REVIEW_TARGET",
            },
        ],
        bee_rows=[
            {
                "review_id": review_id,
                "phrase_text": "hydrating finish",
                "bee_attr_raw": "Hydration",
                "raw_sentiment": "positive",
                "start_offset": 21,
                "end_offset": 37,
            },
        ],
        rel_rows=[
            {
                "review_id": review_id,
                "subj_text": "Review Target",
                "subj_group": "PRD",
                "subj_start": 0,
                "subj_end": 13,
                "obj_text": "hydrating finish",
                "obj_group": "BEE",
                "obj_start": 21,
                "obj_end": 37,
                "relation_raw": "has_attribute",
                "relation_canonical": "has_attribute",
                "source_type": "NER-BEE",
                "raw_sentiment": "positive",
                "obj_keywords": ["moisture"],
            },
        ],
        canonical_entities=[
            CanonicalEntity(
                entity_iri=product_iri,
                entity_type="Product",
                canonical_name="Hydration Cream",
                canonical_name_norm="hydration cream",
                source_key=product_id,
                match_confidence=1.0,
            ),
            CanonicalEntity(
                entity_iri=attr_iri,
                entity_type="BEEAttr",
                canonical_name="Hydration",
                canonical_name_norm="hydration",
                source_key="hydration",
                match_confidence=1.0,
            ),
        ],
        canonical_facts=[fact],
        wrapped_signals=[signal],
        signal_evidence_rows=[
            {
                "signal_id": signal_id,
                "fact_id": fact_id,
                "evidence_rank": 0,
                "contribution": 1.0,
            },
        ],
        quarantine_entries=[],
        dirty_product_ids={product_id},
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value
