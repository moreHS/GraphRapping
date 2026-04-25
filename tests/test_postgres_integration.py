from __future__ import annotations

import json
import os
import uuid
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

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)


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
        "serving_product_profile",
        {"variant_family_id", "representative_product_name"},
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
            SELECT variant_family_id, representative_product_name, top_bee_attr_ids
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
    assert _json_value(user["owned_product_ids"]) == [product_id]
    assert _json_value(user["owned_family_ids"]) == ["family-hydration"]
    assert _json_value(user["repurchased_family_ids"]) == ["family-hydration"]


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
