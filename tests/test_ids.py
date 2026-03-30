"""Tests for deterministic ID generation."""

import pytest
from src.common.ids import (
    make_review_id,
    make_reviewer_proxy_id,
    make_product_iri,
    make_concept_iri,
    make_mention_iri,
    make_fact_id,
    make_signal_id,
    make_qualifier_fingerprint,
)


class TestReviewId:
    def test_stable_key(self):
        rid = make_review_id("sephora", "REV_12345")
        assert rid == "review:sephora:REV_12345"

    def test_fallback_deterministic(self):
        rid1 = make_review_id("hwahae", brand_name_raw="라네즈", product_name_raw="슬리핑마스크", review_text="좋아요")
        rid2 = make_review_id("hwahae", brand_name_raw="라네즈", product_name_raw="슬리핑마스크", review_text="좋아요")
        assert rid1 == rid2
        assert rid1.startswith("review:hwahae:")

    def test_different_text_different_id(self):
        rid1 = make_review_id("src", brand_name_raw="A", review_text="good")
        rid2 = make_review_id("src", brand_name_raw="A", review_text="bad")
        assert rid1 != rid2

    def test_short_review_with_row_num(self):
        """Short reviews with same text but different source_row_num should differ."""
        rid1 = make_review_id("src", brand_name_raw="A", review_text="좋아요", source_row_num="1")
        rid2 = make_review_id("src", brand_name_raw="A", review_text="좋아요", source_row_num="2")
        assert rid1 != rid2


class TestReviewerProxyId:
    def test_stable_author(self):
        pid, stability = make_reviewer_proxy_id("sephora", "user_abc")
        assert pid == "reviewer_proxy:sephora:user_abc"
        assert stability == "STABLE"

    def test_review_local(self):
        pid, stability = make_reviewer_proxy_id("sephora", review_id="review:sephora:123")
        assert pid == "reviewer_proxy:review:sephora:123"
        assert stability == "REVIEW_LOCAL"


class TestEntityIRIs:
    def test_product_iri(self):
        assert make_product_iri("P001") == "product:P001"

    def test_concept_iri(self):
        assert make_concept_iri("Brand", "laneige") == "concept:Brand:laneige"

    def test_mention_iri(self):
        assert make_mention_iri("review:src:1", 3) == "mention:review:src:1:3"


class TestFactId:
    def test_deterministic(self):
        fid1 = make_fact_id("rv1", "product:P1", "has_attribute", "concept:BEEAttr:adhesion", "POS")
        fid2 = make_fact_id("rv1", "product:P1", "has_attribute", "concept:BEEAttr:adhesion", "POS")
        assert fid1 == fid2

    def test_different_polarity_different_id(self):
        fid1 = make_fact_id("rv1", "s", "p", "o", "POS")
        fid2 = make_fact_id("rv1", "s", "p", "o", "NEG")
        assert fid1 != fid2

    def test_qualifier_fingerprint_affects_id(self):
        fp = make_qualifier_fingerprint([("segment", "dry_skin")])
        fid1 = make_fact_id("rv1", "s", "p", "o", "", "")
        fid2 = make_fact_id("rv1", "s", "p", "o", "", fp)
        assert fid1 != fid2

    def test_qualifier_order_independent(self):
        fp1 = make_qualifier_fingerprint([("a", "1"), ("b", "2")])
        fp2 = make_qualifier_fingerprint([("b", "2"), ("a", "1")])
        assert fp1 == fp2


class TestSignalId:
    def test_deterministic(self):
        sid1 = make_signal_id("rv1", "P1", "HAS_BEE_ATTR_SIGNAL", "adhesion", "POS", "v1")
        sid2 = make_signal_id("rv1", "P1", "HAS_BEE_ATTR_SIGNAL", "adhesion", "POS", "v1")
        assert sid1 == sid2

    def test_registry_version_matters(self):
        sid1 = make_signal_id("rv1", "P1", "E", "D", "", "v1")
        sid2 = make_signal_id("rv1", "P1", "E", "D", "", "v2")
        assert sid1 != sid2
