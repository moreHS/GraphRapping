"""
Review repository: review_raw + review_raw_history + ner/bee/rel_raw + review_catalog_link.

Handles versioning: ON CONFLICT → version bump + history append.
L1 child rows are append-only with review_version.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.db.unit_of_work import UnitOfWork


async def upsert_review_raw(uow: UnitOfWork, review: dict[str, Any]) -> int:
    """Insert or update review_raw. Returns the new review_version.

    If review_id exists and content changed → version bump + history.
    If same content → idempotent (no change).
    """
    existing = await uow.fetchrow(
        "SELECT review_version, review_text, raw_payload, is_active FROM review_raw WHERE review_id = $1",
        review["review_id"],
    )

    if existing is None:
        # First insert
        await uow.execute("""
            INSERT INTO review_raw (review_id, source, source_review_key, source_site,
                brand_name_raw, product_name_raw, review_text, reviewer_proxy_id,
                identity_stability, event_time_utc, event_time_raw_text, event_tz,
                event_time_source, raw_payload, review_version, is_active, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,1,true,$15,$15)
        """,
            review["review_id"], review.get("source"), review.get("source_review_key"),
            review.get("source_site"), review.get("brand_name_raw"),
            review.get("product_name_raw"), review["review_text"],
            review.get("reviewer_proxy_id"), review.get("identity_stability", "REVIEW_LOCAL"),
            review.get("event_time_utc"), review.get("event_time_raw_text"),
            review.get("event_tz"), review.get("event_time_source", "PROCESSING_TIME"),
            _jsonb_or_default(review.get("raw_payload"), {}), uow.as_of_ts,
        )
        await _append_history(uow, review["review_id"], 1, "INSERT", review, uow.as_of_ts)
        return 1

    # Check if content changed
    if (existing["review_text"] == review["review_text"]
            and existing["is_active"] == review.get("is_active", True)):
        return int(existing["review_version"])  # idempotent

    new_version = int(existing["review_version"]) + 1
    version_op = "UPDATE"
    if not review.get("is_active", True):
        version_op = "TOMBSTONE"
    elif not existing["is_active"] and review.get("is_active", True):
        version_op = "REACTIVATE"

    await uow.execute("""
        UPDATE review_raw SET
            review_text = $2, raw_payload = $3, review_version = $4,
            is_active = $5, updated_at = $6,
            event_time_utc = $7, event_time_raw_text = $8, event_time_source = $9
        WHERE review_id = $1
    """,
        review["review_id"], review["review_text"],
        _jsonb_or_default(review.get("raw_payload"), {}), new_version,
        review.get("is_active", True), uow.as_of_ts,
        review.get("event_time_utc"), review.get("event_time_raw_text"),
        review.get("event_time_source", "PROCESSING_TIME"),
    )
    await _append_history(uow, review["review_id"], new_version, version_op, review, uow.as_of_ts)
    return new_version


async def upsert_review_catalog_link(uow: UnitOfWork, link: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO review_catalog_link (review_id, source_brand, source_product_name,
            matched_product_id, match_status, match_score, match_method, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (review_id) DO UPDATE SET
            matched_product_id = EXCLUDED.matched_product_id,
            match_status = EXCLUDED.match_status,
            match_score = EXCLUDED.match_score,
            match_method = EXCLUDED.match_method
    """,
        link["review_id"], link.get("source_brand"), link.get("source_product_name"),
        link.get("matched_product_id"), link["match_status"],
        link.get("match_score"), link.get("match_method"), uow.as_of_ts,
    )


async def batch_insert_ner_raw(uow: UnitOfWork, rows: list[dict], review_version: int = 1) -> None:
    if not rows:
        return
    existing = await uow.fetchval(
        "SELECT COUNT(*) FROM ner_raw WHERE review_id=$1 AND review_version=$2",
        rows[0]["review_id"], review_version,
    )
    if existing and existing > 0:
        return
    for row in rows:
        await uow.execute("""
            INSERT INTO ner_raw (review_id, review_version, mention_text, entity_group,
                start_offset, end_offset, raw_sentiment, is_placeholder, placeholder_type)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """,
            row["review_id"], review_version, row["mention_text"], row["entity_group"],
            row.get("start_offset"), row.get("end_offset"), row.get("raw_sentiment"),
            row.get("is_placeholder", False), row.get("placeholder_type"),
        )


async def batch_insert_bee_raw(uow: UnitOfWork, rows: list[dict], review_version: int = 1) -> None:
    if not rows:
        return
    existing = await uow.fetchval(
        "SELECT COUNT(*) FROM bee_raw WHERE review_id=$1 AND review_version=$2",
        rows[0]["review_id"], review_version,
    )
    if existing and existing > 0:
        return
    for row in rows:
        await uow.execute("""
            INSERT INTO bee_raw (review_id, review_version, phrase_text, bee_attr_raw,
                raw_sentiment, start_offset, end_offset)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
        """,
            row["review_id"], review_version, row["phrase_text"], row["bee_attr_raw"],
            row.get("raw_sentiment"), row.get("start_offset"), row.get("end_offset"),
        )


async def batch_insert_rel_raw(uow: UnitOfWork, rows: list[dict], review_version: int = 1) -> None:
    if not rows:
        return
    # L1 idempotency: skip if already inserted for this version
    existing = await uow.fetchval(
        "SELECT COUNT(*) FROM rel_raw WHERE review_id=$1 AND review_version=$2",
        rows[0]["review_id"], review_version,
    )
    if existing and existing > 0:
        return
    for row in rows:
        await uow.execute("""
            INSERT INTO rel_raw (review_id, review_version, subj_text, subj_group,
                subj_start, subj_end, obj_text, obj_group, obj_start, obj_end,
                relation_raw, relation_canonical, source_type, raw_sentiment, obj_keywords)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """,
            row["review_id"], review_version, row["subj_text"], row["subj_group"],
            row.get("subj_start"), row.get("subj_end"),
            row["obj_text"], row["obj_group"],
            row.get("obj_start"), row.get("obj_end"),
            row["relation_raw"], row.get("relation_canonical"), row.get("source_type"),
            row.get("raw_sentiment"), _jsonb_or_none(row.get("obj_keywords")),
        )


async def load_ingested_review_snapshot(
    uow: UnitOfWork,
    review_id: str,
    review_version: int | None = None,
) -> dict | None:
    """Load a review + L1 child rows from DB for incremental replay.

    Returns dict with review_raw, ner_rows, bee_rows, rel_rows (with offsets).
    If review_version is None, loads the latest version.
    """
    # Load parent review
    review = await uow.fetchrow(
        "SELECT * FROM review_raw WHERE review_id = $1", review_id,
    )
    if review is None:
        return None
    review = dict(review)

    version = review_version or review.get("review_version", 1)

    # Load child rows for this version, ordered deterministically
    ner_rows = await uow.fetch(
        "SELECT * FROM ner_raw WHERE review_id=$1 AND review_version=$2 ORDER BY ner_row_id",
        review_id, version,
    )
    bee_rows = await uow.fetch(
        "SELECT * FROM bee_raw WHERE review_id=$1 AND review_version=$2 ORDER BY bee_row_id",
        review_id, version,
    )
    rel_rows = await uow.fetch(
        "SELECT * FROM rel_raw WHERE review_id=$1 AND review_version=$2 ORDER BY rel_row_id",
        review_id, version,
    )

    return {
        "review_raw": review,
        "review_id": review_id,
        "reviewer_proxy_id": review.get("reviewer_proxy_id", ""),
        "identity_stability": review.get("identity_stability", "REVIEW_LOCAL"),
        "review_version": version,
        "ner_rows": [dict(r) for r in ner_rows],
        "bee_rows": [dict(r) for r in bee_rows],
        "rel_rows": [dict(r) for r in rel_rows],
    }


async def load_full_review_snapshot(
    uow: UnitOfWork,
    review_id: str,
    review_version: int | None = None,
) -> tuple[dict | None, bool]:
    """Load a review as a RawReviewRecord-compatible dict for reprocessing.

    Returns: (snapshot_dict, has_child_rows)
      - snapshot_dict: dict with keys matching RawReviewRecord fields, or None if not found
      - has_child_rows: True if at least one ner/bee/rel row exists

    The snapshot_dict can be used to construct a RawReviewRecord for process_review().
    """
    raw = await load_ingested_review_snapshot(uow, review_id, review_version)
    if raw is None:
        return None, False

    review_raw = raw["review_raw"]
    ner_rows = raw["ner_rows"]
    bee_rows = raw["bee_rows"]
    rel_rows = raw["rel_rows"]

    has_child_rows = bool(ner_rows or bee_rows or rel_rows)

    # Transform DB rows → RawReviewRecord-compatible format
    ner_for_record = [_db_ner_to_record_item(r) for r in ner_rows]
    bee_for_record = [_db_bee_to_record_item(r) for r in bee_rows]
    rel_for_record = [_db_rel_to_record_item(r) for r in rel_rows]

    return {
        "brnd_nm": review_raw.get("brand_name_raw", ""),
        "clct_site_nm": review_raw.get("source", ""),
        "prod_nm": review_raw.get("product_name_raw", ""),
        "text": review_raw.get("review_text", ""),
        "ner": ner_for_record,
        "bee": bee_for_record,
        "relation": rel_for_record,
        "source_review_key": review_raw.get("source_review_key"),
        "author_key": None,
        "created_at": _event_time_text(review_raw),
        "collected_at": _event_time_text(review_raw),
        "review_id": review_id,
        "reviewer_proxy_id": raw.get("reviewer_proxy_id", ""),
        "review_version": raw.get("review_version", 1),
    }, has_child_rows


def _db_ner_to_record_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "word": row.get("mention_text", ""),
        "entity_group": row.get("entity_group", ""),
        "start": row.get("start_offset"),
        "end": row.get("end_offset"),
        "sentiment": row.get("raw_sentiment"),
    }


def _db_bee_to_record_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "word": row.get("phrase_text", ""),
        "entity_group": row.get("bee_attr_raw", ""),
        "start": row.get("start_offset"),
        "end": row.get("end_offset"),
        "sentiment": row.get("raw_sentiment"),
    }


def _db_rel_to_record_item(row: dict[str, Any]) -> dict[str, Any]:
    obj_keywords = _json_list(row.get("obj_keywords"))
    subject = {
        "word": row.get("subj_text", ""),
        "entity_group": row.get("subj_group", ""),
        "start": row.get("subj_start"),
        "end": row.get("subj_end"),
    }
    obj = {
        "word": row.get("obj_text", ""),
        "entity_group": row.get("obj_group", ""),
        "start": row.get("obj_start"),
        "end": row.get("obj_end"),
    }
    if row.get("raw_sentiment") is not None:
        obj["sentiment"] = row.get("raw_sentiment")
    if obj_keywords:
        obj["keywords"] = obj_keywords
    return {
        "subject": subject,
        "object": obj,
        "relation": row.get("relation_raw", ""),
        "source_type": row.get("source_type"),
    }


def _jsonb_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _jsonb_or_default(value: Any, default: Any) -> str:
    encoded = _jsonb_or_none(default if value is None else value)
    return encoded or json.dumps(default, ensure_ascii=False)


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _event_time_text(review_raw: dict[str, Any]) -> str | None:
    raw_text = review_raw.get("event_time_raw_text")
    if raw_text:
        return str(raw_text)
    event_time = review_raw.get("event_time_utc")
    if isinstance(event_time, datetime):
        return event_time.isoformat()
    return str(event_time) if event_time is not None else None


async def _append_history(
    uow: UnitOfWork, review_id: str, version: int,
    version_op: str, review: dict, as_of_ts: datetime,
) -> None:
    await uow.execute("""
        INSERT INTO review_raw_history (review_id, review_version, source, source_review_key,
            source_site, brand_name_raw, product_name_raw, review_text, reviewer_proxy_id,
            identity_stability, event_time_utc, event_time_raw_text, event_tz,
            event_time_source, raw_payload, is_active, version_op,
            review_created_at, version_created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
    """,
        review_id, version, review.get("source"), review.get("source_review_key"),
        review.get("source_site"), review.get("brand_name_raw"),
        review.get("product_name_raw"), review.get("review_text", ""),
        review.get("reviewer_proxy_id"), review.get("identity_stability", "REVIEW_LOCAL"),
        review.get("event_time_utc"), review.get("event_time_raw_text"),
        review.get("event_tz"), review.get("event_time_source", "PROCESSING_TIME"),
        _jsonb_or_default(review.get("raw_payload"), {}), review.get("is_active", True),
        version_op, as_of_ts, as_of_ts,
    )
