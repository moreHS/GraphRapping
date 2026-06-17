"""
P1-6 (Wave 2.11): projection_registry.csv must expose the optional gate
columns `allowed_evidence_kind / min_confidence / promotion_mode` that the
loader already supports. BEE_KEYWORD rule must enforce
`allowed_evidence_kind=BEE_DICT` + `min_confidence=0.6`.
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.wrap.projection_registry import ProjectionRegistry


_CSV_PATH = Path(__file__).parent.parent / "configs" / "projection_registry.csv"


def test_csv_header_includes_new_optional_columns() -> None:
    with _CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    for col in ("allowed_evidence_kind", "min_confidence", "promotion_mode"):
        assert col in headers, f"projection_registry.csv missing column {col}"


def test_all_rows_have_full_column_count() -> None:
    """Every data row must match the 17-column header to avoid load_csv None.strip errors."""
    with _CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        for i, row in enumerate(reader, start=2):
            assert len(row) == len(headers), (
                f"row {i} has {len(row)} columns, expected {len(headers)}"
            )


def test_bee_keyword_rule_enforces_dict_and_min_confidence() -> None:
    """P1-6 policy: BEE_KEYWORD requires BEE_DICT evidence + confidence ≥ 0.6."""
    reg = ProjectionRegistry()
    reg.load()
    rule = reg.lookup("HAS_KEYWORD", "BEEAttr", "Keyword", "")
    assert rule is not None, "BEE_KEYWORD rule not loaded"
    assert rule.allowed_evidence_kind == "BEE_DICT"
    assert rule.min_confidence == 0.6
    # Promotion mode unchanged (default IMMEDIATE)
    assert rule.promotion_mode in ("IMMEDIATE", "")


def test_bee_keyword_blocks_non_dict_evidence() -> None:
    """Project with evidence_kind != BEE_DICT → blocked (returns fallback action)."""
    reg = ProjectionRegistry()
    reg.load()
    result = reg.project(
        predicate="HAS_KEYWORD",
        subject_type="BEEAttr",
        object_type="Keyword",
        polarity="",
        evidence_kind="AUTO_KEYWORD",  # not BEE_DICT
        confidence=0.8,
    )
    # Rule's if_unresolved_action is empty → falls back to QUARANTINE per loader logic.
    assert result == "QUARANTINE", (
        f"BEE_KEYWORD with non-BEE_DICT evidence should fall back; got {result!r}"
    )


def test_bee_keyword_blocks_low_confidence() -> None:
    """Project with confidence < 0.6 → blocked."""
    reg = ProjectionRegistry()
    reg.load()
    result = reg.project(
        predicate="HAS_KEYWORD",
        subject_type="BEEAttr",
        object_type="Keyword",
        polarity="",
        evidence_kind="BEE_DICT",
        confidence=0.5,
    )
    assert result == "QUARANTINE", (
        f"BEE_KEYWORD with confidence<0.6 should fall back; got {result!r}"
    )


def test_bee_keyword_admits_qualified_signal() -> None:
    """BEE_DICT evidence + confidence ≥ 0.6 → projected signal."""
    reg = ProjectionRegistry()
    reg.load()
    result = reg.project(
        predicate="HAS_KEYWORD",
        subject_type="BEEAttr",
        object_type="Keyword",
        polarity="",
        evidence_kind="BEE_DICT",
        confidence=0.7,
    )
    assert hasattr(result, "signal_family"), \
        f"Qualified BEE_KEYWORD must project, got {result!r}"
    assert result.signal_family == "BEE_KEYWORD"
    assert result.edge_type == "HAS_BEE_KEYWORD_SIGNAL"


def test_other_rules_remain_unrestricted_by_new_columns() -> None:
    """Rules without min_confidence / allowed_evidence_kind keep behavior.

    Sanity: a known non-BEE rule (has_attribute Product→BEEAttr) is loaded
    without those gates blocking projection.
    """
    reg = ProjectionRegistry()
    reg.load()
    rule = reg.lookup("has_attribute", "Product", "BEEAttr", "")
    assert rule is not None
    assert rule.allowed_evidence_kind == ""
    assert rule.min_confidence == 0.0
    # Project with arbitrary evidence_kind / confidence: should pass through.
    result = reg.project(
        predicate="has_attribute",
        subject_type="Product",
        object_type="BEEAttr",
        polarity="",
        evidence_kind=None,
        confidence=0.1,  # low confidence, but rule has no min_confidence
    )
    assert hasattr(result, "signal_family"), f"unrestricted rule blocked: {result!r}"
    assert result.signal_family == "BEE_ATTR"
