"""
Product repository: product_master + concept_registry + entity_concept_link + canonical_entity.
"""

from __future__ import annotations

from typing import Any

from src.db.unit_of_work import UnitOfWork


async def upsert_product_master(uow: UnitOfWork, product: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO product_master (product_id, product_name, brand_id, brand_name,
            category_id, category_name, country_of_origin, main_benefits, price,
            ingredients, volume, shade, variant_family_id, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        ON CONFLICT (product_id) DO UPDATE SET
            product_name=EXCLUDED.product_name, brand_id=EXCLUDED.brand_id,
            brand_name=EXCLUDED.brand_name, category_id=EXCLUDED.category_id,
            category_name=EXCLUDED.category_name, price=EXCLUDED.price,
            ingredients=EXCLUDED.ingredients, updated_at=EXCLUDED.updated_at
    """,
        product["product_id"], product["product_name"],
        product.get("brand_id"), product.get("brand_name"),
        product.get("category_id"), product.get("category_name"),
        product.get("country_of_origin"), product.get("main_benefits", []),
        product.get("price"), product.get("ingredients", []),
        product.get("volume"), product.get("shade"),
        product.get("variant_family_id"), uow.as_of_ts,
    )


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
