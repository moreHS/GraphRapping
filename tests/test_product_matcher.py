"""Tests for product matcher (exact→norm→alias→fuzzy→quarantine)."""

import pytest
from src.link.product_matcher import match_product, ProductIndex, MatchStatus


@pytest.fixture
def index():
    products = [
        {"product_id": "P001", "product_name": "Lip Sleeping Mask", "brand_name": "LANEIGE"},
        {"product_id": "P002", "product_name": "Kill Cover Fixer Cushion", "brand_name": "CLIO"},
        {"product_id": "P003", "product_name": "Water Sleeping Mask", "brand_name": "LANEIGE"},
    ]
    idx = ProductIndex.build(products)
    idx.add_alias("laneige|립 슬리핑 마스크", "P001")
    return idx


class TestExactNormMatch:
    def test_exact_match(self, index):
        r = match_product("LANEIGE", "Lip Sleeping Mask", index)
        assert r.match_status == MatchStatus.NORM
        assert r.matched_product_id == "P001"
        assert r.match_score == 1.0

    def test_case_insensitive(self, index):
        r = match_product("laneige", "lip sleeping mask", index)
        assert r.match_status == MatchStatus.NORM
        assert r.matched_product_id == "P001"


class TestAliasMatch:
    def test_korean_alias(self, index):
        r = match_product("LANEIGE", "립 슬리핑 마스크", index)
        assert r.match_status == MatchStatus.ALIAS
        assert r.matched_product_id == "P001"


class TestFuzzyMatch:
    def test_close_match(self, index):
        r = match_product("LANEIGE", "Lip Sleeping Mask Intense", index)
        assert r.matched_product_id == "P001"
        assert r.match_score > 0.7

    def test_brand_filter(self, index):
        """Fuzzy should not cross brands."""
        r = match_product("CLIO", "Lip Sleeping Mask", index)
        # CLIO brand filtered, so no LANEIGE match
        assert r.matched_product_id != "P001" or r.match_status == MatchStatus.QUARANTINE


class TestQuarantine:
    def test_no_match(self, index):
        r = match_product("Unknown Brand", "Nonexistent Product XYZ", index)
        assert r.match_status == MatchStatus.QUARANTINE
        assert r.matched_product_id is None
