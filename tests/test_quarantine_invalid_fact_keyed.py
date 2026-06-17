"""
Wave 5.2: keyed-payload contract for the PREDICATE_CONTRACT_VIOLATION
quarantine path.

Phase 0 trace established that 100% of fact-less quarantine_projection_miss
rows (4475 in the v260605 fixture) came from
`QuarantineHandler.quarantine_invalid_fact(...)` because the dict produced by
`CanonicalFactBuilder._invalid_facts` never carried `review_id` / `fact_id`.

The fix carries `review_id` through the builder dict (already available as an
`add_fact` argument) and through the handler's entry.data. These tests pin
that contract so a future refactor can't silently drop the keys.
"""

from __future__ import annotations

from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.qa.quarantine_handler import QuarantineHandler


_CONTRACTS = {
    "HAS_BEE_ATTR": {
        "allowed_subject_types": "Product",
        "allowed_object_types": "BEEAttr",
    },
}


def test_invalid_fact_dict_includes_review_id_on_subject_violation() -> None:
    builder = CanonicalFactBuilder(predicate_contracts=_CONTRACTS)

    fact_id = builder.add_fact(
        review_id="review-001",
        subject_iri="iri:cat:Lipstick",
        predicate="HAS_BEE_ATTR",
        object_iri="iri:attr:Long-lasting",
        subject_type="Category",   # violates allowed 'Product'
        object_type="BEEAttr",
    )
    assert fact_id is None

    invalids = builder.invalid_facts
    assert len(invalids) == 1
    assert invalids[0]["review_id"] == "review-001"
    assert invalids[0]["predicate"] == "HAS_BEE_ATTR"
    assert "subject_type" in invalids[0]["reason"]


def test_invalid_fact_dict_includes_review_id_on_object_violation() -> None:
    builder = CanonicalFactBuilder(predicate_contracts=_CONTRACTS)

    fact_id = builder.add_fact(
        review_id="review-002",
        subject_iri="iri:prod:1",
        predicate="HAS_BEE_ATTR",
        object_iri="iri:cat:Lipstick",
        subject_type="Product",
        object_type="Category",   # violates allowed 'BEEAttr'
    )
    assert fact_id is None

    invalids = builder.invalid_facts
    assert len(invalids) == 1
    assert invalids[0]["review_id"] == "review-002"
    assert "object_type" in invalids[0]["reason"]


def test_quarantine_invalid_fact_propagates_review_id_and_fact_id() -> None:
    handler = QuarantineHandler()
    handler.quarantine_invalid_fact({
        "predicate": "HAS_BEE_ATTR",
        "subject_type": "Category",
        "object_type": "BEEAttr",
        "review_id": "review-003",
        "fact_id": "fact-xyz",
        "reason": "subject_type 'Category' not in allowed 'Product'",
    })

    entries = handler.flush()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.table == "quarantine_projection_miss"
    assert entry.data["review_id"] == "review-003"
    assert entry.data["fact_id"] == "fact-xyz"
    assert entry.data["reason"].startswith("PREDICATE_CONTRACT_VIOLATION:")


def test_quarantine_invalid_fact_handles_missing_keys_gracefully() -> None:
    """Backwards-compat: payload without review_id falls back to empty string,
    not KeyError. Builder fix means real callers always pass review_id, but
    the handler must remain tolerant for ad-hoc producers."""
    handler = QuarantineHandler()
    handler.quarantine_invalid_fact({
        "predicate": "HAS_BEE_ATTR",
        "reason": "test",
    })

    entry = handler.flush()[0]
    assert entry.data["review_id"] == ""
    assert entry.data["fact_id"] == ""


def test_quarantine_invalid_fact_coerces_explicit_none_to_empty_string() -> None:
    """Codex 1차 recommendation: explicit None payloads must not slip through
    as NULL. `or ""` (not `default=""`) covers both missing-key and None cases."""
    handler = QuarantineHandler()
    handler.quarantine_invalid_fact({
        "predicate": "HAS_BEE_ATTR",
        "review_id": None,
        "fact_id": None,
        "reason": "test",
    })

    entry = handler.flush()[0]
    assert entry.data["review_id"] == ""
    assert entry.data["fact_id"] == ""


def test_builder_invalid_facts_flow_into_handler_payload() -> None:
    """End-to-end: builder rejection → handler entry preserves the key.
    Mirrors run_daily_pipeline:411-412 wiring."""
    builder = CanonicalFactBuilder(predicate_contracts=_CONTRACTS)
    builder.add_fact(
        review_id="review-004",
        subject_iri="iri:cat:Lipstick",
        predicate="HAS_BEE_ATTR",
        object_iri="iri:attr:Pretty",
        subject_type="Category",
        object_type="BEEAttr",
    )

    handler = QuarantineHandler()
    for inv in builder.invalid_facts:
        handler.quarantine_invalid_fact(inv)

    assert handler.flush()[0].data["review_id"] == "review-004"
