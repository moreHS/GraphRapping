"""
S3 rs.jsonl → RawReviewRecord loader.

Contract: Accepts raw operational pipeline output (rs.jsonl format from S3)
and converts to GraphRapping's RawReviewRecord. Performs NER label mapping
and sentiment normalization during conversion.

Difference from relation_loader:
  - relation_loader expects pre-canonicalized relation[] with 65 canonical predicates
  - rs_jsonl_loader expects raw NER/BEE spans from the operational pipeline
  - Both produce the same RawReviewRecord output type

See mockdata/SCHEMA_RS_JSONL.md for the full input schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, cast

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

_BEE_LABELS = {
    "가루날림", "광감", "구성", "단품_디자인", "맛", "무너짐", "뭉침", "밀착력",
    "발림성", "발색력", "백탁현상", "번짐", "보습력", "부작용/손상", "사용감",
    "색상", "성분", "세정력", "용량", "유통기한", "제형", "지속력", "커버력",
    "컬링/볼륨", "패키지/용기_디자인", "펄감", "편리성", "표현력", "품질",
    "향", "활용성", "효과", "휴대성", "흡수력", "배송", "서비스", "판촉",
    "인지가격", "충성도",
}


def load_reviews_from_rs_jsonl(
    file_path: str | Path,
    max_count: int | None = None,
) -> list[RawReviewRecord]:
    """Load reviews from an rs.jsonl file (or JSON array of rs records)."""
    return list(stream_reviews_from_rs_jsonl(file_path, max_count))


def load_reviews_from_rs_jsonl_with_report(
    file_path: str | Path,
    max_count: int | None = None,
) -> tuple[list[RawReviewRecord], dict[str, int]]:
    """Load rs.jsonl reviews and return loader-level contract stats."""
    records = _read_rs_records(file_path, max_count)
    reviews = [_convert_rs_record(record, idx) for idx, record in enumerate(records)]
    return reviews, summarize_rs_jsonl_contract(records)


def stream_reviews_from_rs_jsonl(
    file_path: str | Path,
    max_count: int | None = None,
) -> Iterator[RawReviewRecord]:
    """Stream rs.jsonl records and convert to RawReviewRecord."""
    for idx, record in enumerate(_read_rs_records(file_path, max_count)):
        yield _convert_rs_record(record, idx)


def _read_rs_records(
    file_path: str | Path,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Read rs.jsonl records from JSON array or line-delimited JSON."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if content.startswith("["):
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array in {path}")
        records = cast(list[dict[str, Any]], data)
    else:
        records = [json.loads(line) for line in content.splitlines() if line.strip()]

    if max_count is not None:
        return records[:max_count]
    return records


def summarize_rs_jsonl_contract(records: list[dict[str, Any]]) -> dict[str, int]:
    """Summarize relation-readiness of raw rs.jsonl records.

    This is input-contract observability only. Final BEE attribution still
    happens in the review processing pipeline using relation rows.
    """
    total_records = len(records)
    bee_span_count = 0
    relation_row_count = 0
    relation_ready_review_count = 0
    relation_pending_review_count = 0
    ner_bee_relation_count = 0

    for record in records:
        bee_spans = record.get("bee_spans", []) or []
        relations = record.get("relation", []) or []
        bee_span_count += len(bee_spans)
        relation_row_count += len(relations)

        if relations:
            relation_ready_review_count += 1
        elif bee_spans:
            relation_pending_review_count += 1

        ner_bee_relation_count += sum(
            1 for relation in relations if _is_ner_bee_relation(relation)
        )

    return {
        "total_records": total_records,
        "bee_span_count": bee_span_count,
        "relation_row_count": relation_row_count,
        "relation_ready_review_count": relation_ready_review_count,
        "relation_pending_review_count": relation_pending_review_count,
        "ner_bee_relation_count": ner_bee_relation_count,
        "bee_without_relation_count": max(bee_span_count - ner_bee_relation_count, 0),
    }


def _is_ner_bee_relation(relation: dict[str, Any]) -> bool:
    if relation.get("source_type") == "NER-BeE":
        return True
    obj = relation.get("object", {}) or {}
    return obj.get("entity_group") in _BEE_LABELS


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
