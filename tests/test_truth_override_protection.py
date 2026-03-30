"""Tests for truth override protection: review signals never overwrite product master."""

import pytest
from src.common.enums import SCORING_EXCLUDED_FAMILIES, SignalFamily


class TestTruthOverride:
    def test_catalog_validation_excluded_from_scoring(self):
        """CATALOG_VALIDATION signals must be excluded from scoring."""
        assert SignalFamily.CATALOG_VALIDATION in SCORING_EXCLUDED_FAMILIES

    def test_has_ingredient_maps_to_catalog_validation(self):
        """has_ingredient → CATALOG_VALIDATION (not a scoreable signal)."""
        from src.wrap.projection_registry import ProjectionRegistry
        reg = ProjectionRegistry()
        reg.load()
        result = reg.project("has_ingredient", "Product", "Ingredient")
        assert not isinstance(result, str)
        assert result.signal_family == "CATALOG_VALIDATION"

    def test_brand_of_maps_to_catalog_validation(self):
        """brand_of → CATALOG_VALIDATION."""
        from src.wrap.projection_registry import ProjectionRegistry
        reg = ProjectionRegistry()
        reg.load()
        result = reg.project("brand_of", "Product", "Brand")
        assert not isinstance(result, str)
        assert result.signal_family == "CATALOG_VALIDATION"
