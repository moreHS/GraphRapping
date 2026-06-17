"""
Repository helpers for the review-summary serving sidecar.

Review summaries are external product-scoped text summaries. They are not graph
facts, so the repository keeps them in a mart sidecar keyed by GraphRapping's
product_id while preserving the raw ES documents as JSONB.
"""

from __future__ import annotations

import json
from typing import Any

from src.db.unit_of_work import UnitOfWork


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def upsert_review_summary_sidecar(uow: UnitOfWork, row: dict[str, Any]) -> None:
    """Upsert one product-level review-summary sidecar row."""
    await uow.execute(
        """
        INSERT INTO review_summary_sidecar (
            product_id, source_product_id, source_channel, source_key_type,
            review_source, review_channel, review_summary_category,
            match_status, long_doc_id, short_doc_id, long_doc, short_doc,
            candidate_metadata, normalized_summary, an_date, source, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT (product_id) DO UPDATE SET
            source_product_id=EXCLUDED.source_product_id,
            source_channel=EXCLUDED.source_channel,
            source_key_type=EXCLUDED.source_key_type,
            review_source=EXCLUDED.review_source,
            review_channel=EXCLUDED.review_channel,
            review_summary_category=EXCLUDED.review_summary_category,
            match_status=EXCLUDED.match_status,
            long_doc_id=EXCLUDED.long_doc_id,
            short_doc_id=EXCLUDED.short_doc_id,
            long_doc=EXCLUDED.long_doc,
            short_doc=EXCLUDED.short_doc,
            candidate_metadata=EXCLUDED.candidate_metadata,
            normalized_summary=EXCLUDED.normalized_summary,
            an_date=EXCLUDED.an_date,
            source=EXCLUDED.source,
            updated_at=EXCLUDED.updated_at
        """,
        row["product_id"],
        row["source_product_id"],
        row.get("source_channel"),
        row.get("source_key_type"),
        row.get("review_source"),
        row.get("review_channel"),
        row.get("review_summary_category"),
        row["match_status"],
        row.get("long_doc_id"),
        row.get("short_doc_id"),
        _jsonb(row.get("long_doc")),
        _jsonb(row.get("short_doc")),
        _jsonb(row.get("candidate_metadata")),
        _jsonb(row.get("normalized_summary")),
        row.get("an_date"),
        row.get("source") or "es8_summary_review",
        uow.as_of_ts,
    )


async def delete_review_summary_sidecar_outside_products(
    uow: UnitOfWork,
    product_ids: list[str],
) -> int:
    """Delete stale sidecar rows for products outside the current clean set."""
    status = await uow.execute(
        """
        DELETE FROM review_summary_sidecar
        WHERE NOT (product_id = ANY($1::text[]))
        """,
        product_ids,
    )
    parts = status.strip().split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def insert_review_summary_manifest(uow: UnitOfWork, manifest: dict[str, Any]) -> int:
    """Insert one materialization manifest and return its id."""
    manifest_id = await uow.fetchval(
        """
        INSERT INTO review_summary_manifest (
            source, long_alias, short_alias, an_date, product_count,
            clean_lookup_product_count, fetched_long_docs, fetched_short_docs,
            matched, exact_category, source_unique, product_id_unique,
            ambiguous_skipped, not_found, collision_excluded, errors, payload
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        RETURNING manifest_id
        """,
        manifest.get("source") or "es8_summary_review",
        manifest.get("long_alias"),
        manifest.get("short_alias"),
        manifest.get("an_date"),
        manifest.get("product_count", 0),
        manifest.get("clean_lookup_product_count", 0),
        manifest.get("fetched_long_docs", 0),
        manifest.get("fetched_short_docs", 0),
        manifest.get("matched", 0),
        manifest.get("exact_category", 0),
        manifest.get("source_unique", 0),
        manifest.get("product_id_unique", 0),
        manifest.get("ambiguous_skipped", 0),
        manifest.get("not_found", 0),
        manifest.get("collision_excluded", 0),
        manifest.get("errors", 0),
        _jsonb(manifest.get("payload")),
    )
    return int(manifest_id)
