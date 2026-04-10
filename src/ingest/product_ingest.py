"""
Product master ingest + concept registry seeding.

Loads product master data and seeds concept_registry with
Brand, Category, Ingredient concepts extracted from product DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.ids import make_product_iri, make_concept_iri
from src.common.text_normalize import normalize_text
from src.common.enums import ConceptType, EntityType


@dataclass
class ProductRecord:
    product_id: str
    product_name: str
    brand_id: str | None = None
    brand_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    country_of_origin: str | None = None
    main_benefits: list[str] | None = None
    price: float | None = None
    ingredients: list[str] | None = None
    volume: str | None = None
    shade: str | None = None
    variant_family_id: str | None = None


@dataclass
class ConceptSeed:
    concept_id: str
    concept_type: str
    canonical_name: str
    canonical_name_norm: str
    source_system: str = "product_db"
    source_key: str | None = None


@dataclass
class EntityConceptLink:
    entity_iri: str
    concept_id: str
    link_type: str
    confidence: float = 1.0
    source: str = "product_db"


def ingest_product(record: ProductRecord) -> dict[str, Any]:
    """Process a single product record.

    Returns dict with:
        product_master: row dict for product_master table
        canonical_entity: row dict for canonical_entity table
        concepts: list of ConceptSeed to upsert into concept_registry
        links: list of EntityConceptLink for entity_concept_link
    """
    product_iri = make_product_iri(record.product_id)

    # Product master row
    master_row = {
        "product_id": record.product_id,
        "product_name": record.product_name,
        "brand_id": record.brand_id,
        "brand_name": record.brand_name,
        "category_id": record.category_id,
        "category_name": record.category_name,
        "country_of_origin": record.country_of_origin,
        "main_benefits": record.main_benefits or [],
        "price": record.price,
        "ingredients": record.ingredients or [],
        "volume": record.volume,
        "shade": record.shade,
        "variant_family_id": record.variant_family_id,
    }

    # Canonical entity for this product
    entity_row = {
        "entity_iri": product_iri,
        "entity_type": EntityType.PRODUCT,
        "canonical_name": record.product_name,
        "canonical_name_norm": normalize_text(record.product_name),
        "source_system": "product_db",
        "source_key": record.product_id,
    }

    concepts: list[ConceptSeed] = []
    links: list[EntityConceptLink] = []

    # Seed Brand concept
    if record.brand_name:
        brand_cid = _make_concept_id(ConceptType.BRAND, record.brand_id or record.brand_name)
        concepts.append(ConceptSeed(
            concept_id=brand_cid,
            concept_type=ConceptType.BRAND,
            canonical_name=record.brand_name,
            canonical_name_norm=normalize_text(record.brand_name),
            source_key=record.brand_id,
        ))
        links.append(EntityConceptLink(
            entity_iri=product_iri,
            concept_id=brand_cid,
            link_type="HAS_BRAND",
        ))

    # Seed Category concept
    if record.category_name:
        cat_cid = _make_concept_id(ConceptType.CATEGORY, record.category_id or record.category_name)
        concepts.append(ConceptSeed(
            concept_id=cat_cid,
            concept_type=ConceptType.CATEGORY,
            canonical_name=record.category_name,
            canonical_name_norm=normalize_text(record.category_name),
            source_key=record.category_id,
        ))
        links.append(EntityConceptLink(
            entity_iri=product_iri,
            concept_id=cat_cid,
            link_type="IN_CATEGORY",
        ))

    # Seed Ingredient concepts
    for ing in record.ingredients or []:
        ing_norm = normalize_text(ing)
        ing_cid = _make_concept_id(ConceptType.INGREDIENT, ing_norm)
        concepts.append(ConceptSeed(
            concept_id=ing_cid,
            concept_type=ConceptType.INGREDIENT,
            canonical_name=ing,
            canonical_name_norm=ing_norm,
        ))
        links.append(EntityConceptLink(
            entity_iri=product_iri,
            concept_id=ing_cid,
            link_type="HAS_INGREDIENT",
        ))

    # Seed Goal concepts from main_benefits (canonical via goal alias map)
    from src.common.concept_resolver import resolve_goal_id
    for benefit in record.main_benefits or []:
        benefit_norm = resolve_goal_id(benefit)
        benefit_cid = _make_concept_id(ConceptType.GOAL, benefit_norm)
        concepts.append(ConceptSeed(
            concept_id=benefit_cid,
            concept_type=ConceptType.GOAL,
            canonical_name=benefit,
            canonical_name_norm=benefit_norm,
        ))
        links.append(EntityConceptLink(
            entity_iri=product_iri,
            concept_id=benefit_cid,
            link_type="HAS_MAIN_BENEFIT",
        ))

    # Seed Country concept
    if record.country_of_origin:
        country_cid = _make_concept_id(ConceptType.COUNTRY, normalize_text(record.country_of_origin))
        concepts.append(ConceptSeed(
            concept_id=country_cid,
            concept_type=ConceptType.COUNTRY,
            canonical_name=record.country_of_origin,
            canonical_name_norm=normalize_text(record.country_of_origin),
        ))
        links.append(EntityConceptLink(
            entity_iri=product_iri,
            concept_id=country_cid,
            link_type="FROM_COUNTRY",
        ))

    return {
        "product_master": master_row,
        "canonical_entity": entity_row,
        "concepts": concepts,
        "links": links,
    }


def _make_concept_id(concept_type: ConceptType, key: str) -> str:
    return make_concept_iri(concept_type.value, normalize_text(key))
