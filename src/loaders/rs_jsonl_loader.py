"""
S3 rs.jsonl → RawReviewRecord loader.

Converts the operational review analysis pipeline output (rs.jsonl format)
to GraphRapping's RawReviewRecord format for KG processing.

rs.jsonl schema: see mockdata/SCHEMA_RS_JSONL.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from src.ingest.review_ingest import RawReviewRecord


# NER label mapping: rs.jsonl → GraphRapping entity_group
_NER_LABEL_MAP = {
    "AGE": "AGE",
    "CAPACITY": "VOL",
    "BASE_COLOR": "COL",
    "BRAND": "BRD",
    "CATEGORY": "CAT",
}

# Sentiment mapping: rs.jsonl → GraphRapping
_SENTIMENT_MAP = {
    "긍정": "긍정",
    "부정": "부정",
    "중립": "중립",
    "복합": "중립",  # 복합 → treat as neutral for NER/BEE
}


def load_reviews_from_rs_jsonl(
    file_path: str | Path,
    max_count: int | None = None,
) -> list[RawReviewRecord]:
    """Load reviews from an rs.jsonl file (or JSON array of rs records)."""
    return list(stream_reviews_from_rs_jsonl(file_path, max_count))


def stream_reviews_from_rs_jsonl(
    file_path: str | Path,
    max_count: int | None = None,
) -> Iterator[RawReviewRecord]:
    """Stream rs.jsonl records and convert to RawReviewRecord."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if content.startswith("["):
        data = json.loads(content)
    else:
        data = [json.loads(line) for line in content.splitlines() if line.strip()]

    for idx, record in enumerate(data):
        if max_count is not None and idx >= max_count:
            break
        yield _convert_rs_record(record, idx)


def _convert_rs_record(record: dict[str, Any], row_index: int) -> RawReviewRecord:
    """Convert a single rs.jsonl record to RawReviewRecord."""
    # NER spans → ner format
    ner = []
    for span in record.get("ner_spans", []):
        mapped_label = _NER_LABEL_MAP.get(span.get("label", ""), span.get("label", ""))
        ner.append({
            "word": span.get("text", ""),
            "entity_group": mapped_label,
            "start": span.get("start"),
            "end": span.get("end"),
            "sentiment": None,  # rs.jsonl NER spans don't carry sentiment
        })

    # BEE spans → bee format
    bee = []
    for span in record.get("bee_spans", []):
        bee.append({
            "word": span.get("text", ""),
            "entity_group": span.get("label", ""),
            "start": span.get("start"),
            "end": span.get("end"),
            "sentiment": _SENTIMENT_MAP.get(span.get("sentiment", ""), span.get("sentiment", "")),
        })

    # Relation (future — currently empty in rs.jsonl)
    relation = []
    for item in record.get("relation", []):
        relation.append({
            "subject": item.get("subject", {}),
            "object": item.get("object", {}),
            "relation": item.get("relation", ""),
            "source_type": item.get("source_type"),
        })

    # Determine brand name
    brnd_nm = record.get("brnd_nm", "")
    if not brnd_nm:
        # Fallback: extn/glb may have rspn_sal_lcns_nm
        brnd_nm = record.get("rspn_sal_lcns_nm", "")

    # Determine collection site from channel
    channel = record.get("channel", "")
    clct_site_nm = _channel_to_site(channel)

    return RawReviewRecord(
        brnd_nm=brnd_nm,
        clct_site_nm=clct_site_nm,
        prod_nm=record.get("prd_nm", ""),
        text=record.get("text", ""),
        ner=ner,
        bee=bee,
        relation=relation,
        created_at=record.get("date"),
        collected_at=record.get("date"),
        source_review_key=record.get("id"),
        author_key=_build_author_key(record),
        source_row_num=str(row_index),
    )


def _channel_to_site(channel: str) -> str:
    """Map channel code to collection site name."""
    site_map = {
        "031": "아모레퍼시픽",
        "036": "이니스프리",
        "039": "오설록",
        "048": "아리따움",
        "navershopping": "네이버쇼핑",
        "ssg": "SSG",
        "oliveyoung": "올리브영",
        "kakao": "카카오",
        "amazon": "Amazon",
        "sephora": "Sephora",
    }
    return site_map.get(channel, channel)


def _build_author_key(record: dict) -> str | None:
    """Build author key from available demographics (own source only)."""
    age = record.get("age_sctn_cd")
    sex = record.get("sex_cd")
    if age and sex and age != "None" and sex != "None":
        return f"demo_{sex}_{age}"
    return None
