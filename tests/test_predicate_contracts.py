"""Tests for predicate contract validation."""

import pytest
from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.common.config_loader import load_csv


class TestPredicateContracts:
    @pytest.fixture
    def contracts(self):
        rows = load_csv("predicate_contracts.csv")
        return {r["predicate"]: r for r in rows}

    def test_all_65_predicates_have_contracts(self, contracts):
        from src.normalize.relation_canonicalizer import CANONICAL_PREDICATES
        for pred in CANONICAL_PREDICATES:
            assert pred in contracts, f"Predicate '{pred}' missing from predicate_contracts.csv"

    def test_invalid_subject_type_rejected(self):
        contracts = {
            "has_attribute": {"allowed_subject_types": "Product", "allowed_object_types": "BEEAttr"},
        }
        builder = CanonicalFactBuilder(predicate_contracts=contracts)
        result = builder.add_fact(
            review_id="rv1", subject_iri="user:U1",
            predicate="has_attribute", object_iri="concept:BEEAttr:x",
            subject_type="User",  # NOT allowed (should be Product)
            object_type="BEEAttr",
        )
        assert result is None  # rejected
        assert len(builder.invalid_facts) == 1
        assert "subject_type" in builder.invalid_facts[0]["reason"]

    def test_valid_types_accepted(self):
        contracts = {
            "has_attribute": {"allowed_subject_types": "Product", "allowed_object_types": "BEEAttr"},
        }
        builder = CanonicalFactBuilder(predicate_contracts=contracts)
        result = builder.add_fact(
            review_id="rv1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:x",
            subject_type="Product",
            object_type="BEEAttr",
        )
        assert result is not None
        assert len(builder.invalid_facts) == 0
