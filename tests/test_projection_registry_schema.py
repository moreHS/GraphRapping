"""Tests for projection_registry.csv schema integrity."""
import csv
import tempfile
from pathlib import Path

import pytest

from src.common.config_loader import load_csv, CONFIGS_DIR


def test_all_rows_match_header_column_count():
    """Every row in projection_registry.csv must have exactly as many fields as the header."""
    rows = load_csv("projection_registry.csv")
    for i, row in enumerate(rows):
        assert None not in row, f"Row {i + 2} has more columns than header: {row}"


def test_malformed_csv_raises():
    """A CSV with extra columns must raise ValueError."""
    content = "a,b,c\n1,2,3,EXTRA\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", dir=CONFIGS_DIR, delete=False) as f:
        f.write(content)
        tmp_name = Path(f.name).name
    try:
        with pytest.raises(ValueError, match="more columns than header"):
            load_csv(tmp_name)
    finally:
        (CONFIGS_DIR / tmp_name).unlink()


def test_same_entity_and_no_relationship_present():
    """same_entity and no_relationship predicates must be in the registry."""
    rows = load_csv("projection_registry.csv")
    predicates = {r["input_predicate"] for r in rows}
    assert "same_entity" in predicates
    assert "no_relationship" in predicates


def test_column_count_consistent():
    """All rows must have the same number of keys."""
    rows = load_csv("projection_registry.csv")
    if not rows:
        pytest.skip("Empty CSV")
    expected = len(rows[0])
    for i, row in enumerate(rows):
        assert len(row) == expected, f"Row {i + 2} has {len(row)} keys, expected {expected}"
