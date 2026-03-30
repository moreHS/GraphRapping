"""
Elasticsearch product index → ProductRecord[] + ProductIndex + concept_links loader.

Reads from ES 'amore-prod-mstr' index and builds all product-side inputs
required by run_batch(): product_masters, product_index, concept_links.

MVP: price/ingredients/main_benefits are empty defaults (enrich later from separate source).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ingest.product_ingest import ProductRecord, ingest_product
from src.link.product_matcher import ProductIndex
from src.common.text_normalize import normalize_text
from src.common.ids import make_product_iri


@dataclass
class ProductLoadResult:
    """All product-side artifacts needed by run_batch()."""
    product_masters: dict[str, dict] = field(default_factory=dict)
    product_index: ProductIndex | None = None
    concept_links: dict[str, list[dict]] = field(default_factory=dict)
    concept_seeds: list[dict] = field(default_factory=list)
    canonical_entities: list[dict] = field(default_factory=list)
    product_count: int = 0


def load_products_from_es_records(
    es_records: list[dict[str, Any]],
    sale_status_filter: str = "판매중",
) -> ProductLoadResult:
    """Convert ES product records to GraphRapping product artifacts.

    Args:
        es_records: List of ES _source dicts from amore-prod-mstr index
        sale_status_filter: Only include products with this SALE_STATUS

    Field mapping:
      ONLINE_PROD_SERIAL_NUMBER → product_id
      prd_nm → product_name
      BRAND_NAME → brand_name, brand_id=normalize(BRAND_NAME)
      CTGR_SS_NAME → category_name, category_id=normalize(CTGR_SS_NAME)
      SALE_STATUS → filter (판매중 only)
      price, ingredients, main_benefits, country_of_origin → defaults (MVP)
    """
    result = ProductLoadResult()
    index_data = []

    for record in es_records:
        # Filter by sale status
        if sale_status_filter and record.get("SALE_STATUS") != sale_status_filter:
            continue

        product_id = record.get("ONLINE_PROD_SERIAL_NUMBER", "")
        if not product_id:
            continue

        brand_name = record.get("BRAND_NAME", "")
        category_name = record.get("CTGR_SS_NAME", "")

        pr = ProductRecord(
            product_id=product_id,
            product_name=record.get("prd_nm", ""),
            brand_name=brand_name,
            brand_id=normalize_text(brand_name) if brand_name else None,
            category_name=category_name,
            category_id=normalize_text(category_name) if category_name else None,
            # MVP defaults — enrich later from separate source
            price=None,
            ingredients=[],
            main_benefits=[],
            country_of_origin=None,
        )

        # Run product_ingest to generate master row + concept seeds + links
        ingest_result = ingest_product(pr)

        product_iri = make_product_iri(product_id)
        result.product_masters[product_id] = ingest_result["product_master"]
        result.concept_links[product_iri] = [
            {"concept_id": link.concept_id, "link_type": link.link_type,
             "confidence": link.confidence, "source": link.source}
            for link in ingest_result["links"]
        ]
        result.concept_seeds.extend([
            {"concept_id": c.concept_id, "concept_type": c.concept_type,
             "canonical_name": c.canonical_name, "canonical_name_norm": c.canonical_name_norm,
             "source_system": c.source_system, "source_key": c.source_key}
            for c in ingest_result["concepts"]
        ])
        result.canonical_entities.append(ingest_result["canonical_entity"])

        index_data.append({
            "product_id": product_id,
            "product_name": pr.product_name,
            "brand_name": brand_name,
        })

    result.product_index = ProductIndex.build(index_data)
    result.product_count = len(result.product_masters)
    return result


def load_products_from_json(
    file_path: str,
    sale_status_filter: str = "판매중",
) -> ProductLoadResult:
    """Load products from a JSON dump of ES records (for testing without ES access)."""
    import json
    from pathlib import Path
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else [data]
    return load_products_from_es_records(records, sale_status_filter)
