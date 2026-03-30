"""
Relation project JSON → RawReviewRecord[] loader.

Reads extraction output (NER+BEE+REL) from Relation project JSON files
and converts to GraphRapping's RawReviewRecord format.

Prerequisite: input file contains reviews with relation[] already in 65 canonical predicates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from src.ingest.review_ingest import RawReviewRecord


def load_reviews_from_json(
    file_path: str | Path,
    max_count: int | None = None,
) -> list[RawReviewRecord]:
    """Load all reviews from a Relation project JSON file.

    Args:
        file_path: Path to JSON file (e.g., hab_rel_sample_ko_withPRD_listkeyword.json)
        max_count: Optional limit on number of reviews to load
    """
    return list(stream_reviews_from_json(file_path, max_count))


def stream_reviews_from_json(
    file_path: str | Path,
    max_count: int | None = None,
) -> Iterator[RawReviewRecord]:
    """Stream reviews from JSON file for memory efficiency.

    Handles both JSON array format and JSONL (one object per line).
    """
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # Try JSON array first
    if content.startswith("["):
        data = json.loads(content)
    else:
        # JSONL format
        data = [json.loads(line) for line in content.splitlines() if line.strip()]

    for row_index, record in enumerate(data):
        if max_count is not None and row_index >= max_count:
            break
        yield _convert_record(record, row_index)


def _convert_record(record: dict[str, Any], row_index: int) -> RawReviewRecord:
    """Convert a single Relation project record to RawReviewRecord.

    Field mapping:
      brnd_nm → brnd_nm (as-is)
      prod_nm → prod_nm (as-is)
      text → text (as-is)
      clct_site_nm → clct_site_nm (as-is)
      drup_dt → created_at (rename)
      ner[] → ner[] (as-is)
      bee[] → bee[] (as-is)
      relation[] → relation[] (as-is, expects 65 canonical predicates)
      (row_index) → source_row_num
    """
    # NER: ensure required fields
    ner = []
    for item in record.get("ner", []):
        ner.append({
            "word": item.get("word", ""),
            "entity_group": item.get("entity_group", ""),
            "start": item.get("start"),
            "end": item.get("end"),
            "sentiment": item.get("sentiment"),
        })

    # BEE: ensure required fields
    bee = []
    for item in record.get("bee", []):
        bee.append({
            "word": item.get("word", ""),
            "entity_group": item.get("entity_group", ""),
            "start": item.get("start"),
            "end": item.get("end"),
            "sentiment": item.get("sentiment"),
        })

    # REL: use relation[] if available, otherwise empty (BEE-only mode)
    relation = []
    for item in record.get("relation", []):
        relation.append({
            "subject": item.get("subject", {}),
            "object": item.get("object", {}),
            "relation": item.get("relation", ""),
            "source_type": item.get("source_type"),
        })

    return RawReviewRecord(
        brnd_nm=record.get("brnd_nm", ""),
        clct_site_nm=record.get("clct_site_nm", ""),
        prod_nm=record.get("prod_nm", ""),
        text=record.get("text", ""),
        ner=ner,
        bee=bee,
        relation=relation,
        created_at=record.get("drup_dt"),       # drup_dt → created_at
        collected_at=record.get("drup_dt"),      # same as backup
        source_row_num=str(row_index),
        source_review_key=None,                  # no stable key in source
        author_key=None,                         # no author info
    )
