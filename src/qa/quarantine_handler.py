"""
Unified quarantine handler for 5 quarantine types.

All mapping failures go to explicit quarantine, never silent drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.enums import QuarantineStatus


@dataclass
class QuarantineEntry:
    table: str
    data: dict[str, Any]


class QuarantineHandler:
    """Collects quarantine entries during pipeline processing.

    Entries are buffered and flushed to DB in batch.
    """

    def __init__(self) -> None:
        self._buffer: list[QuarantineEntry] = []

    def quarantine_product_match(
        self,
        review_id: str,
        source_brand: str,
        source_product_name: str,
        attempted_score: float = 0.0,
        attempted_method: str = "",
        reason: str = "",
    ) -> None:
        self._buffer.append(QuarantineEntry(
            table="quarantine_product_match",
            data={
                "review_id": review_id,
                "source_brand": source_brand,
                "source_product_name": source_product_name,
                "attempted_match_score": attempted_score,
                "attempted_match_method": attempted_method,
                "reason": reason,
                "status": QuarantineStatus.PENDING,
            },
        ))

    def quarantine_placeholder(
        self,
        review_id: str,
        mention_text: str,
        entity_group: str = "",
        placeholder_type: str = "",
        reason: str = "",
    ) -> None:
        self._buffer.append(QuarantineEntry(
            table="quarantine_placeholder",
            data={
                "review_id": review_id,
                "mention_text": mention_text,
                "entity_group": entity_group,
                "placeholder_type": placeholder_type,
                "reason": reason,
                "status": QuarantineStatus.PENDING,
            },
        ))

    def quarantine_unknown_keyword(
        self,
        surface_text: str,
        bee_attr_raw: str = "",
        review_id: str = "",
        context_text: str = "",
        reason: str = "surface form not in dictionary",
    ) -> None:
        self._buffer.append(QuarantineEntry(
            table="quarantine_unknown_keyword",
            data={
                "review_id": review_id,
                "surface_text": surface_text,
                "bee_attr_raw": bee_attr_raw,
                "context_text": context_text,
                "reason": reason,
                "status": QuarantineStatus.PENDING,
            },
        ))

    def quarantine_projection_miss(
        self,
        predicate: str,
        subject_type: str = "",
        object_type: str = "",
        polarity: str = "",
        registry_version: str = "",
        fact_id: str = "",
        review_id: str = "",
        reason: str = "no projection registry mapping",
    ) -> None:
        self._buffer.append(QuarantineEntry(
            table="quarantine_projection_miss",
            data={
                "fact_id": fact_id,
                "review_id": review_id,
                "predicate": predicate,
                "subject_type": subject_type,
                "object_type": object_type,
                "polarity": polarity,
                "registry_version": registry_version,
                "reason": reason,
                "status": QuarantineStatus.PENDING,
            },
        ))

    def quarantine_untyped_entity(
        self,
        mention_text: str,
        expected_types: list[str],
        context_predicate: str = "",
        review_id: str = "",
        reason: str = "entity type classification failed",
    ) -> None:
        self._buffer.append(QuarantineEntry(
            table="quarantine_untyped_entity",
            data={
                "review_id": review_id,
                "mention_text": mention_text,
                "expected_types": expected_types,
                "context_predicate": context_predicate,
                "reason": reason,
                "status": QuarantineStatus.PENDING,
            },
        ))

    def quarantine_invalid_fact(
        self,
        fact_payload: dict,
    ) -> None:
        """Quarantine a fact that violated predicate contract."""
        self._buffer.append(QuarantineEntry(
            table="quarantine_projection_miss",
            data={
                "predicate": fact_payload.get("predicate", ""),
                "subject_type": fact_payload.get("subject_type", ""),
                "object_type": fact_payload.get("object_type", ""),
                "reason": f"PREDICATE_CONTRACT_VIOLATION: {fact_payload.get('reason', '')}",
                "status": QuarantineStatus.PENDING,
            },
        ))

    def extend(self, entries: list[QuarantineEntry]) -> None:
        """Append external quarantine entries to this handler's buffer."""
        self._buffer.extend(entries)

    @property
    def pending_count(self) -> int:
        return len(self._buffer)

    def pending_by_table(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._buffer:
            counts[entry.table] = counts.get(entry.table, 0) + 1
        return counts

    def flush(self) -> list[QuarantineEntry]:
        """Return and clear all buffered entries."""
        entries = list(self._buffer)
        self._buffer.clear()
        return entries
