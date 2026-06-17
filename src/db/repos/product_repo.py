"""
Product repository: product_master + concept_registry + entity_concept_link + canonical_entity.
"""

from __future__ import annotations

from typing import Any

from src.db.unit_of_work import UnitOfWork

_UNKNOWN_SOURCE_KEY = "unknown"


async def upsert_product_master(uow: UnitOfWork, product: dict[str, Any]) -> None:
    """Upsert a product_master row.

    Wave 4 Task 5: conflict update refreshes ALL consumer-relevant columns
    so re-loads see fresh truth (`country_of_origin`, `main_benefits`,
    `volume`, `shade`, `variant_family_id`, `is_active` were silently
    sticking to first-insert values before this fix).
    """
    await uow.execute("""
        INSERT INTO product_master (product_id, product_name, brand_id, brand_name,
            category_id, category_name, country_of_origin, main_benefits, price,
            ingredients, volume, shade, variant_family_id,
            source_product_id, source_channel, source_key_type,
            representative_product_name, source_truth_source, source_truth_quality,
            source_truth_updated_at, source_review_count, source_review_score,
            is_active, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
        ON CONFLICT (product_id) DO UPDATE SET
            product_name=EXCLUDED.product_name,
            brand_id=EXCLUDED.brand_id,
            brand_name=EXCLUDED.brand_name,
            category_id=EXCLUDED.category_id,
            category_name=EXCLUDED.category_name,
            country_of_origin=EXCLUDED.country_of_origin,
            main_benefits=EXCLUDED.main_benefits,
            price=EXCLUDED.price,
            ingredients=EXCLUDED.ingredients,
            volume=EXCLUDED.volume,
            shade=EXCLUDED.shade,
            variant_family_id=EXCLUDED.variant_family_id,
            source_product_id=EXCLUDED.source_product_id,
            source_channel=EXCLUDED.source_channel,
            source_key_type=EXCLUDED.source_key_type,
            representative_product_name=EXCLUDED.representative_product_name,
            source_truth_source=EXCLUDED.source_truth_source,
            source_truth_quality=EXCLUDED.source_truth_quality,
            source_truth_updated_at=EXCLUDED.source_truth_updated_at,
            source_review_count=EXCLUDED.source_review_count,
            source_review_score=EXCLUDED.source_review_score,
            is_active=EXCLUDED.is_active,
            updated_at=EXCLUDED.updated_at
    """,
        product["product_id"], product["product_name"],
        product.get("brand_id"), product.get("brand_name"),
        product.get("category_id"), product.get("category_name"),
        product.get("country_of_origin"), product.get("main_benefits", []),
        product.get("price"), product.get("ingredients", []),
        product.get("volume"), product.get("shade"),
        product.get("variant_family_id"),
        product.get("source_product_id") or product["product_id"],
        product.get("source_channel"),
        product.get("source_key_type"),
        product.get("representative_product_name"),
        product.get("source_truth_source"),
        product.get("source_truth_quality"),
        product.get("source_truth_updated_at") or uow.as_of_ts,
        product.get("source_review_count"),
        product.get("source_review_score"),
        product.get("is_active", True),
        uow.as_of_ts,
    )


async def load_product_master(
    uow: UnitOfWork,
    product_id: str,
) -> dict[str, Any] | None:
    """Load active product_master truth by product_id for DB-grounded rebuilds."""
    row = await uow.fetchrow(
        """
        SELECT *
        FROM product_master
        WHERE product_id = $1
          AND is_active = true
        """,
        product_id,
    )
    return dict(row) if row is not None else None


async def upsert_product_review_stats(uow: UnitOfWork, row: dict[str, Any]) -> None:
    """Upsert source-grounded product review stats by source identity."""
    await uow.execute("""
        INSERT INTO product_review_stats (
            product_id, source_channel, source_key_type,
            source_review_count_6m, source_review_score_count_6m,
            source_avg_rating_6m, source_review_min_date_6m, source_review_max_date_6m,
            source_review_count_all, source_review_score_count_all,
            source_avg_rating_all, source_review_min_date_all, source_review_max_date_all,
            source, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (product_id, source_channel, source_key_type) DO UPDATE SET
            source_review_count_6m=EXCLUDED.source_review_count_6m,
            source_review_score_count_6m=EXCLUDED.source_review_score_count_6m,
            source_avg_rating_6m=EXCLUDED.source_avg_rating_6m,
            source_review_min_date_6m=EXCLUDED.source_review_min_date_6m,
            source_review_max_date_6m=EXCLUDED.source_review_max_date_6m,
            source_review_count_all=EXCLUDED.source_review_count_all,
            source_review_score_count_all=EXCLUDED.source_review_score_count_all,
            source_avg_rating_all=EXCLUDED.source_avg_rating_all,
            source_review_min_date_all=EXCLUDED.source_review_min_date_all,
            source_review_max_date_all=EXCLUDED.source_review_max_date_all,
            source=EXCLUDED.source,
            updated_at=EXCLUDED.updated_at
    """,
        row["product_id"],
        _source_key_value(row.get("source_channel")),
        _source_key_value(row.get("source_key_type")),
        row.get("source_review_count_6m", 0),
        row.get("source_review_score_count_6m", 0),
        row.get("source_avg_rating_6m"),
        row.get("source_review_min_date_6m"),
        row.get("source_review_max_date_6m"),
        row.get("source_review_count_all", 0),
        row.get("source_review_score_count_all", 0),
        row.get("source_avg_rating_all"),
        row.get("source_review_min_date_all"),
        row.get("source_review_max_date_all"),
        row.get("source") or row.get("source_review_stats_source") or "snowflake:f_prd_rv_hist",
        uow.as_of_ts,
    )


async def load_product_review_stats(
    uow: UnitOfWork,
    product_id: str,
    source_channel: str | None,
    source_key_type: str | None,
) -> dict[str, Any] | None:
    """Load source-grounded product review stats by product_id.

    Exact source-key rows are preferred, but stale/missing source keys from an
    incremental caller must not make the product lose stats entirely.
    """
    row = await uow.fetchrow(
        """
        SELECT product_id, source_channel, source_key_type,
            source_review_count_6m, source_review_score_count_6m,
            source_avg_rating_6m, source_review_min_date_6m, source_review_max_date_6m,
            source_review_count_all, source_review_score_count_all,
            source_avg_rating_all, source_review_min_date_all, source_review_max_date_all,
            source, updated_at
        FROM product_review_stats
        WHERE product_id = $1
        ORDER BY
            CASE
                WHEN source_channel = $2 AND source_key_type = $3 THEN 0
                ELSE 1
            END,
            CASE
                WHEN source_review_count_all > 0 OR source_review_count_6m > 0 THEN 0
                ELSE 1
            END,
            updated_at DESC,
            source_channel,
            source_key_type
        LIMIT 1
        """,
        product_id,
        _source_key_value(source_channel),
        _source_key_value(source_key_type),
    )
    return dict(row) if row is not None else None


def _source_key_value(value: Any) -> str:
    if value is None:
        return _UNKNOWN_SOURCE_KEY
    text = str(value).strip()
    return text or _UNKNOWN_SOURCE_KEY


async def upsert_concept_seeds(uow: UnitOfWork, concepts: list[dict]) -> None:
    for c in concepts:
        await uow.execute("""
            INSERT INTO concept_registry (concept_id, concept_type, canonical_name,
                canonical_name_norm, source_system, source_key)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (concept_id) DO NOTHING
        """,
            c["concept_id"], c["concept_type"], c["canonical_name"],
            c["canonical_name_norm"], c.get("source_system", "product_db"),
            c.get("source_key"),
        )


async def upsert_entity_concept_links(uow: UnitOfWork, links: list[dict]) -> None:
    for link in links:
        await uow.execute("""
            INSERT INTO entity_concept_link (entity_iri, concept_id, link_type, confidence, source)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (entity_iri, concept_id, link_type) DO NOTHING
        """,
            link["entity_iri"], link["concept_id"], link["link_type"],
            link.get("confidence", 1.0), link.get("source", "product_db"),
        )


async def upsert_product_entity(uow: UnitOfWork, entity: dict) -> None:
    await uow.execute("""
        INSERT INTO canonical_entity (entity_iri, entity_type, canonical_name,
            canonical_name_norm, source_system, source_key, match_confidence, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (entity_iri) DO UPDATE SET
            canonical_name = CASE WHEN EXCLUDED.match_confidence > COALESCE(canonical_entity.match_confidence, 0)
                THEN EXCLUDED.canonical_name ELSE canonical_entity.canonical_name END,
            match_confidence = GREATEST(COALESCE(EXCLUDED.match_confidence, 0), COALESCE(canonical_entity.match_confidence, 0)),
            updated_at = EXCLUDED.updated_at
    """,
        entity["entity_iri"], entity["entity_type"], entity["canonical_name"],
        entity["canonical_name_norm"], entity.get("source_system"),
        entity.get("source_key"), entity.get("match_confidence"),
        uow.as_of_ts,
    )
