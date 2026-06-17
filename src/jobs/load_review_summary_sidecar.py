"""
Materialize review-summary ES docs into the GraphRapping serving sidecar.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from src.db.repos.review_summary_repo import (
    delete_review_summary_sidecar_outside_products,
    insert_review_summary_manifest,
    upsert_review_summary_sidecar,
)
from src.db.unit_of_work import UnitOfWork
from src.loaders.review_summary_sidecar_loader import (
    build_lookup_products,
    build_sidecar_rows,
)


async def load_review_summary_sidecar(
    pool: asyncpg.Pool,
    long_docs: list[dict[str, Any]],
    short_docs: list[dict[str, Any]],
    *,
    long_alias: str = "summary-review-long",
    short_alias: str = "summary-review-short",
    an_date: str | None = None,
    source: str = "es8_summary_review",
) -> dict[str, Any]:
    """Build and persist the review-summary sidecar for active products."""
    product_rows = await fetch_active_product_source_rows(pool)
    lookup = build_lookup_products(product_rows)
    sidecar_rows, manifest = build_sidecar_rows(
        lookup.products,
        long_docs,
        short_docs,
        product_count=lookup.product_count,
        collision_excluded=lookup.collision_excluded,
        missing_source_identity_excluded=lookup.missing_source_identity_excluded,
        source=source,
    )
    manifest.update({
        "long_alias": long_alias,
        "short_alias": short_alias,
        "an_date": an_date or manifest.get("an_date"),
        "payload": {
            **(manifest.get("payload") or {}),
            "long_alias": long_alias,
            "short_alias": short_alias,
            "an_date": an_date,
        },
    })

    async with UnitOfWork(pool) as uow:
        deleted_stale = await delete_review_summary_sidecar_outside_products(
            uow,
            [row["product_id"] for row in sidecar_rows],
        )
        for row in sidecar_rows:
            await upsert_review_summary_sidecar(uow, row)
        manifest["payload"]["deleted_stale_sidecar_rows"] = deleted_stale
        manifest_id = await insert_review_summary_manifest(uow, manifest)

    return {
        "manifest_id": manifest_id,
        "sidecar_rows": len(sidecar_rows),
        "deleted_stale_sidecar_rows": deleted_stale,
        **manifest,
    }


async def fetch_active_product_source_rows(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Read active product/source identity rows from product master + serving."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                pm.product_id,
                pm.product_name,
                pm.brand_name,
                pm.source_truth_quality,
                COALESCE(spp.source_product_id, pm.source_product_id) AS source_product_id,
                COALESCE(spp.source_channel, pm.source_channel) AS source_channel,
                COALESCE(spp.source_key_type, pm.source_key_type) AS source_key_type
            FROM product_master pm
            JOIN serving_product_profile spp ON spp.product_id = pm.product_id
            WHERE pm.is_active = true
              AND spp.is_active = true
            ORDER BY pm.product_id
            """
        )
    return [dict(row) for row in rows]
