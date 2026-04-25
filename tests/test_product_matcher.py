"""Tests for product matcher (exact→norm→alias→fuzzy→quarantine)."""

import pytest
from src.link.product_matcher import match_product, ProductIndex, MatchStatus
from src.common.text_normalize import strip_brand_prefixes


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

    def test_brand_prefixed_catalog_matches_unprefixed_review_name(self):
        products = [
            {"product_id": "P002", "product_name": "라네즈 워터뱅크 블루 히알루로닉 세럼", "brand_name": "라네즈"},
        ]
        idx = ProductIndex.build(products)
        r = match_product("라네즈", "워터뱅크 블루 히알루로닉 세럼", idx)
        assert r.match_status == MatchStatus.NORM
        assert r.matched_product_id == "P002"
        assert r.match_method == "norm_brand_stripped"

    def test_input_brand_prefix_matches_unprefixed_catalog_name(self):
        products = [
            {"product_id": "P010", "product_name": "워터뱅크 블루 히알루로닉 세럼", "brand_name": "라네즈"},
        ]
        idx = ProductIndex.build(products)
        r = match_product("라네즈", "라네즈 워터뱅크 블루 히알루로닉 세럼", idx)
        assert r.match_status == MatchStatus.NORM
        assert r.matched_product_id == "P010"
        assert r.match_method == "norm_input_brand_stripped"

    def test_brand_stripped_key_collision_does_not_auto_match(self):
        products = [
            {"product_id": "P1", "product_name": "라네즈 워터뱅크 세럼", "brand_name": "라네즈"},
            {"product_id": "P2", "product_name": "라네즈 워터뱅크 세럼", "brand_name": "라네즈"},
        ]
        idx = ProductIndex.build(products)
        r = match_product("라네즈", "워터뱅크 세럼", idx)
        assert not (r.match_status == MatchStatus.NORM and r.match_method == "norm_brand_stripped")


class TestBrandPrefixNormalization:
    def test_strip_brand_prefix_with_known_brand(self):
        assert (
            strip_brand_prefixes("라네즈 워터뱅크 블루 히알루로닉 세럼", ["라네즈"])
            == "워터뱅크 블루 히알루로닉 세럼"
        )

    def test_strip_brand_prefix_does_not_strip_without_boundary(self):
        assert strip_brand_prefixes("라네즈워터뱅크", ["라네즈"]) == "라네즈워터뱅크"


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
