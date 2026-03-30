"""Tests for projection registry: completeness + determinism."""

import pytest
from src.wrap.projection_registry import ProjectionRegistry


@pytest.fixture
def registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


class TestRegistryCompleteness:
    def test_loads_successfully(self, registry):
        assert registry.rule_count > 60

    def test_core_predicates_mapped(self, registry):
        core = [
            ("has_attribute", "Product", "BEEAttr", ""),
            ("used_on", "Product", "TemporalContext", ""),
            ("causes", "Product", "Concern", "NEG"),
            ("comparison_with", "Product", "Product", ""),
            ("addresses", "Product", "Concern", "POS"),
        ]
        for pred, subj, obj, pol in core:
            result = registry.lookup(pred, subj, obj, pol)
            assert result is not None, f"Predicate '{pred}' ({subj},{obj},{pol}) has no registry mapping"

    def test_unmapped_combo_returns_none(self, registry):
        result = registry.lookup("nonexistent_predicate", "X", "Y")
        assert result is None

    def test_completeness_check(self, registry):
        observed = [
            ("has_attribute", "Product", "BEEAttr", "POS"),
            ("used_on", "Product", "TemporalContext", ""),
            ("fake_pred", "X", "Y", ""),
        ]
        unmapped = registry.completeness_check(observed)
        assert ("fake_pred", "X", "Y", "") in unmapped
        assert ("has_attribute", "Product", "BEEAttr", "POS") not in unmapped


class TestRegistryDeterminism:
    def test_same_input_same_output(self, registry):
        r1 = registry.project("has_attribute", "Product", "BEEAttr", "POS")
        r2 = registry.project("has_attribute", "Product", "BEEAttr", "POS")
        assert r1 == r2

    def test_version_present(self, registry):
        assert registry.version == "v1"
