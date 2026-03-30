"""
ReviewPersistBundle: per-review artifact bundle for DB write.

Contains all artifacts from process_review() ready for atomic persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.canonical.canonical_fact_builder import CanonicalEntity, CanonicalFact
from src.wrap.signal_emitter import WrappedSignal
from src.qa.quarantine_handler import QuarantineEntry


@dataclass
class ReviewPersistBundle:
    """All artifacts from processing a single review, ready for DB write."""
    # Layer 1
    review_raw: dict[str, Any]
    review_catalog_link: dict[str, Any]
    ner_rows: list[dict[str, Any]]
    bee_rows: list[dict[str, Any]]
    rel_rows: list[dict[str, Any]]
    # Layer 2
    canonical_entities: list[CanonicalEntity]
    canonical_facts: list[CanonicalFact]
    # Layer 2.5
    wrapped_signals: list[WrappedSignal]
    signal_evidence_rows: list[dict[str, Any]]
    # QA
    quarantine_entries: list[QuarantineEntry]
    # Meta
    review_id: str = ""
    review_version: int = 1
    matched_product_id: str | None = None
    dirty_product_ids: set[str] = field(default_factory=set)
    dirty_user_ids: set[str] = field(default_factory=set)
    invalid_facts: list[dict] = field(default_factory=list)
