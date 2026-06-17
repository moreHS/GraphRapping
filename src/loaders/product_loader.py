"""
Elasticsearch product index → ProductRecord[] + ProductIndex + concept_links loader.

Reads from ES 'amore-prod-mstr' index and builds all product-side inputs
required by run_batch(): product_masters, product_index, concept_links.

Field mapping uses source-grounded product truth fields from the ES-compatible index
(SALE_PRICE, MAIN_EFFECT, MAIN_INGREDIENT, REPRESENTATIVE_PROD_CODE, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.ids import make_product_iri
from src.common.text_normalize import normalize_text
from src.ingest.product_ingest import ProductRecord, ingest_product
from src.link.product_matcher import ProductIndex
from src.loaders.product_truth_merge import merge_product_truth


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_comma_list(value: str | None) -> list[str]:
    """Parse a comma-delimited string into a list of stripped strings.

    Returns [] if value is None or empty string.
    Returns [value] if it's a non-delimited string.
    Splits by "," and strips whitespace for comma-delimited strings.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return []
    s = str(value).strip()
    if "," not in s:
        return [s]
    return [part.strip() for part in s.split(",") if part.strip()]


def _parse_price(value: Any) -> float | None:
    """Convert value to float, returning None on failure or if value is None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_optional_int(value: Any) -> int | None:
    """Convert value to int, returning None for missing/unparseable values."""
    if value is None:
        return None
    if isinstance(value, str) and (not value.strip() or value == "None"):
        return None
    try:
        parsed = int(float(value))
    except (ValueError, TypeError):
        return None
    return parsed if parsed >= 0 else None


def _parse_review_score(value: Any) -> float | None:
    """Convert source review score to float; zero/blank means missing catalog score."""
    if value is None:
        return None
    if isinstance(value, str) and (not value.strip() or value == "None"):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if parsed > 0 else None


@dataclass
class ProductLoadResult:
    """All product-side artifacts needed by run_batch()."""
    product_masters: dict[str, dict] = field(default_factory=dict)
    product_index: ProductIndex = field(default_factory=lambda: ProductIndex.build([]))
    concept_links: dict[str, list[dict]] = field(default_factory=dict)
    concept_seeds: list[dict] = field(default_factory=list)
    canonical_entities: list[dict] = field(default_factory=list)
    product_count: int = 0


def load_products_from_es_records(
    es_records: list[dict[str, Any]],
    sale_status_filter: str | None = None,
) -> ProductLoadResult:
    """Convert ES product records to GraphRapping product artifacts.

    Args:
        es_records: List of ES _source dicts from amore-prod-mstr index
        sale_status_filter: Only include products with this SALE_STATUS when set.
            The final source-grounded fixture keeps all source products by
            default because non-selling products can still be review targets.

    Field mapping (ES field → ProductRecord field):
      ONLINE_PROD_SERIAL_NUMBER → product_id
      prd_nm → product_name
      BRAND_NAME → brand_name, brand_id=normalize(BRAND_NAME)
      CTGR_SS_NAME → category_name, category_id=normalize(CTGR_SS_NAME)
      SALE_STATUS → optional caller filter (default: keep all source products)
      SALE_PRICE → price (float, None on failure)
      MAIN_INGREDIENT → ingredients (comma-split list)
      MAIN_EFFECT → main_benefits (comma-split list)
      COUNTRY_OF_ORIGIN → country_of_origin
      REPRESENTATIVE_PROD_CODE → variant_family_id
    """
    result = ProductLoadResult()
    index_data = []

    for record in es_records:
        # Filter by sale status
        if sale_status_filter and record.get("SALE_STATUS") != sale_status_filter:
            continue

        product_id_raw = record.get("ONLINE_PROD_SERIAL_NUMBER")
        if product_id_raw is None or str(product_id_raw).strip() == "":
            continue
        product_id = str(product_id_raw)

        raw_brand_name = record.get("BRAND_NAME", "")
        category_name = record.get("CTGR_SS_NAME", "")
        source_truth = merge_product_truth({
            "product_id": product_id,
            "product_name": record.get("prd_nm", ""),
            "brand_name": raw_brand_name,
            "brand_id": normalize_text(raw_brand_name) if raw_brand_name else None,
            "source_product_id": product_id,
            "source_channel": record.get("SOURCE_CHANNEL") or record.get("channel"),
            "source_key_type": record.get("SOURCE_KEY_TYPE") or "ecp_onln_prd_srno",
            "representative_product_name": record.get("REPRESENTATIVE_PROD_NAME"),
            "source_truth_source": record.get("SOURCE_TRUTH_SOURCE") or "product_catalog_es",
            "source_truth_quality": record.get("SOURCE_TRUTH_QUALITY"),
        })
        brand_name = source_truth.get("brand_name")

        pr = ProductRecord(
            product_id=product_id,
            product_name=record.get("prd_nm", ""),
            brand_name=brand_name,
            brand_id=normalize_text(brand_name) if brand_name else None,
            category_name=category_name,
            category_id=normalize_text(category_name) if category_name else None,
            price=_parse_price(record.get("SALE_PRICE")),
            ingredients=_parse_comma_list(record.get("MAIN_INGREDIENT", "")),
            main_benefits=_parse_comma_list(record.get("MAIN_EFFECT", "")),
            country_of_origin=record.get("COUNTRY_OF_ORIGIN"),
            variant_family_id=record.get("REPRESENTATIVE_PROD_CODE"),
        )

        # Run product_ingest to generate master row + concept seeds + links
        ingest_result = ingest_product(pr)

        product_iri = make_product_iri(product_id)
        master = ingest_result["product_master"]
        master.update({
            "source_product_id": source_truth.get("source_product_id"),
            "source_channel": source_truth.get("source_channel"),
            "source_key_type": source_truth.get("source_key_type"),
            "representative_product_name": source_truth.get("representative_product_name"),
            "source_truth_source": source_truth.get("source_truth_source"),
            "source_truth_quality": source_truth.get("source_truth_quality"),
            "source_review_count": _parse_optional_int(record.get("REVIEW_COUNT")),
            "source_review_score": _parse_review_score(record.get("REVIEW_SCORE")),
        })
        master["_es_meta"] = {
            "REPRESENTATIVE_PROD_NAME": record.get("REPRESENTATIVE_PROD_NAME"),
            "REVIEW_COUNT": record.get("REVIEW_COUNT"),
            "REVIEW_SCORE": record.get("REVIEW_SCORE"),
            "SAP_CODE": record.get("SAP_CODE"),
            "ONLINE_PROD_CODE": record.get("ONLINE_PROD_CODE"),
        }
        master = merge_product_truth(master)
        result.product_masters[product_id] = master
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
            "brand_name": brand_name or "",
        })

    result.product_index = ProductIndex.build(index_data)
    result.product_count = len(result.product_masters)
    return result


def load_products_from_json(
    file_path: str,
    sale_status_filter: str | None = None,
) -> ProductLoadResult:
    """Load products from a JSON dump of ES records (for testing without ES access)."""
    import json
    from pathlib import Path
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else [data]
    return load_products_from_es_records(records, sale_status_filter)
