"""
Review raw ingest.

Loads raw review JSON records into Layer 1 tables:
  review_raw, ner_raw, bee_raw, rel_raw
Also generates review_id and reviewer_proxy_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.common.ids import make_review_id, make_reviewer_proxy_id
from src.common.enums import EventTimeSource


def _parse_to_utc(raw: str) -> datetime | None:
    """Parse a datetime string to UTC. Returns None on failure."""
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


@dataclass
class RawReviewRecord:
    """Input format matching Relation project output."""
    brnd_nm: str = ""
    clct_site_nm: str = ""
    prod_nm: str = ""
    text: str = ""
    ner: list[dict[str, Any]] = field(default_factory=list)
    bee: list[dict[str, Any]] = field(default_factory=list)
    relation: list[dict[str, Any]] = field(default_factory=list)
    # Optional source keys
    source_review_key: str | None = None
    author_key: str | None = None
    created_at: str | None = None
    collected_at: str | None = None
    source_row_num: str | None = None


@dataclass
class IngestedReview:
    review_id: str
    reviewer_proxy_id: str
    identity_stability: str
    review_raw: dict[str, Any]
    ner_rows: list[dict[str, Any]]
    bee_rows: list[dict[str, Any]]
    rel_rows: list[dict[str, Any]]


def ingest_review(record: RawReviewRecord, source: str = "unknown") -> IngestedReview:
    """Transform a raw review record into Layer 1 rows.

    Args:
        record: Raw review data from extraction pipeline
        source: Source identifier (e.g. 'sephora', 'hwahae')
    """
    source_site = record.clct_site_nm or source

    # Generate deterministic IDs
    review_id = make_review_id(
        source=source_site,
        source_review_key=record.source_review_key,
        brand_name_raw=record.brnd_nm,
        product_name_raw=record.prod_nm,
        review_text=record.text,
        collected_at=record.collected_at or "",
        source_row_num=record.source_row_num or "",
    )

    reviewer_proxy_id, identity_stability = make_reviewer_proxy_id(
        source=source_site,
        author_key=record.author_key,
        review_id=review_id,
    )

    # Determine event_time — NEVER None after this block
    event_time_utc = None
    event_time_source = EventTimeSource.PROCESSING_TIME
    event_time_raw_text = None

    if record.created_at:
        event_time_raw_text = record.created_at
        event_time_utc = _parse_to_utc(record.created_at)
        if event_time_utc:
            event_time_source = EventTimeSource.SOURCE_CREATED
    if event_time_utc is None and record.collected_at:
        event_time_raw_text = event_time_raw_text or record.collected_at
        event_time_utc = _parse_to_utc(record.collected_at)
        if event_time_utc:
            event_time_source = EventTimeSource.COLLECTED_AT
    if event_time_utc is None:
        event_time_utc = datetime.now(timezone.utc)
        event_time_source = EventTimeSource.PROCESSING_TIME

    # Build review_raw row
    review_raw = {
        "review_id": review_id,
        "source": source_site,
        "source_review_key": record.source_review_key,
        "source_site": source_site,
        "brand_name_raw": record.brnd_nm,
        "product_name_raw": record.prod_nm,
        "review_text": record.text,
        "reviewer_proxy_id": reviewer_proxy_id,
        "identity_stability": identity_stability,
        "event_time_utc": event_time_utc,
        "event_time_raw_text": event_time_raw_text,
        "event_time_source": event_time_source,
        "raw_payload": {
            "brnd_nm": record.brnd_nm,
            "clct_site_nm": record.clct_site_nm,
            "prod_nm": record.prod_nm,
            "text": record.text,
        },
    }

    # Build ner_raw rows
    ner_rows = []
    for i, ner in enumerate(record.ner):
        is_placeholder = ner.get("start") is None
        placeholder_type = None
        word = ner.get("word", "")
        if word == "Review Target":
            placeholder_type = "REVIEW_TARGET"
        elif word == "Reviewer":
            placeholder_type = "REVIEWER"
        elif word.lower() in ("i", "my", "me", "it", "this"):
            placeholder_type = "PRONOUN"

        ner_rows.append({
            "review_id": review_id,
            "mention_text": word,
            "entity_group": ner.get("entity_group", ""),
            "start_offset": ner.get("start"),
            "end_offset": ner.get("end"),
            "raw_sentiment": ner.get("sentiment"),
            "is_placeholder": is_placeholder or placeholder_type is not None,
            "placeholder_type": placeholder_type,
        })

    # Build bee_raw rows
    bee_rows = []
    for bee in record.bee:
        bee_rows.append({
            "review_id": review_id,
            "phrase_text": bee.get("word", ""),
            "bee_attr_raw": bee.get("entity_group", ""),
            "raw_sentiment": bee.get("sentiment"),
            "start_offset": bee.get("start"),
            "end_offset": bee.get("end"),
        })

    # Build rel_raw rows
    rel_rows = []
    for rel in record.relation:
        subj = rel.get("subject", {})
        obj = rel.get("object", {})
        rel_rows.append({
            "review_id": review_id,
            "subj_text": subj.get("word", ""),
            "subj_group": subj.get("entity_group", ""),
            "subj_start": subj.get("start"),
            "subj_end": subj.get("end"),
            "obj_text": obj.get("word", ""),
            "obj_group": obj.get("entity_group", ""),
            "obj_start": obj.get("start"),
            "obj_end": obj.get("end"),
            "relation_raw": rel.get("relation", ""),
            "relation_canonical": None,  # filled by relation_canonicalizer
            "source_type": rel.get("source_type"),
            "obj_keywords": obj.get("keywords", []),  # NER-BeE keyword extraction source
        })

    return IngestedReview(
        review_id=review_id,
        reviewer_proxy_id=reviewer_proxy_id,
        identity_stability=identity_stability,
        review_raw=review_raw,
        ner_rows=ner_rows,
        bee_rows=bee_rows,
        rel_rows=rel_rows,
    )
