"""Tests for product matcher (exact→norm→alias→fuzzy→quarantine)."""

from difflib import SequenceMatcher

import pytest
from src.link.product_matcher import (
    match_product,
    ProductIndex,
    MatchStatus,
    FUZZY_AUTO_ACCEPT,
    FUZZY_MANUAL_REVIEW,
)
from src.common.text_normalize import normalize_text, strip_brand_prefixes


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

    def test_missing_brand_does_not_crash(self, index):
        r = match_product(None, "Nonexistent Product XYZ", index)
        assert r.match_status == MatchStatus.QUARANTINE
        assert r.matched_product_id is None


class TestKoreanAwareFuzzyMatch:
    """Phase 3.3: jamo decomposition + space/symbol normalization for Korean
    fuzzy matching. Thresholds (0.93/0.80) and quarantine routing are
    unchanged — only the fuzzy *score* computed against Korean text is
    affected, and only ever upward (max of the plain and Korean-aware
    comparison), so ASCII/foreign-brand matching stays byte-for-byte
    identical (see test_ascii_only_fuzzy_unaffected).
    """

    def test_spacing_variant_reaches_auto_accept(self):
        """'수분진정크림' vs '수분진정 크림' differ only by one space.
        Plain syllable-level SequenceMatcher scores this ~0.923 — above
        manual-review but below the 0.93 auto-accept bar. Space-insensitive
        jamo comparison should resolve it to a perfect match.
        """
        products = [{"product_id": "P1", "product_name": "수분진정크림", "brand_name": "브랜드"}]
        idx = ProductIndex.build(products)
        r = match_product("브랜드", "수분진정 크림", idx)
        assert r.match_status == MatchStatus.FUZZY
        assert r.match_method == "fuzzy_auto"
        assert r.matched_product_id == "P1"
        assert r.match_score >= FUZZY_AUTO_ACCEPT

    def test_bracket_delimiter_variant_reaches_auto_accept(self):
        """Bracket *delimiters* used as pure separators ("[대용량] 수분진정크림"
        vs "대용량 수분진정크림") must not depress the score below
        auto-accept once punctuation/space noise is stripped — the enclosed
        text itself is identical, only the brackets differ.
        """
        products = [{"product_id": "P1", "product_name": "대용량 수분진정크림", "brand_name": "브랜드"}]
        idx = ProductIndex.build(products)
        r = match_product("브랜드", "[대용량] 수분진정크림", idx)
        assert r.match_status == MatchStatus.FUZZY
        assert r.match_method == "fuzzy_auto"
        assert r.matched_product_id == "P1"

    def test_bracket_content_difference_is_not_auto_accepted(self):
        """Regression guard: bracket *contents* often carry real variant
        identity in the product catalog (리필/본품, 대용량 등). Unlike the
        pure-delimiter case above, differing bracket content must NOT be
        stripped away and must NOT collapse into an auto-accept match.

        (A first draft of this fix stripped whole bracketed spans the way
        strip_brand_prefixes does for "(...)"; on the mockdata fixture that
        turned genuinely different products with differing bracket content
        into ties, and wrong auto-accepts rose from 24 to 122 out of 906
        reviews. This test locks in the safer, content-preserving design.)
        """
        products = [
            {"product_id": "REFILL", "product_name": "[리필] 세라마이드 크림", "brand_name": "브랜드"},
            {"product_id": "ORIGINAL", "product_name": "[본품] 세라마이드 크림", "brand_name": "브랜드"},
        ]
        idx = ProductIndex.build(products)
        r = match_product("브랜드", "[리필]세라마이드 크림 100ml", idx)
        assert r.match_method != "fuzzy_auto"
        assert r.match_status == MatchStatus.QUARANTINE

    def test_batchim_level_typo_gets_partial_credit_short_of_auto_accept(self):
        """A single-jamo (batchim) typo within one syllable ("밤" vs "밥")
        should score meaningfully higher under jamo decomposition than
        plain syllable-level comparison allows (~0.833), while staying in
        the manual-review band rather than jumping to auto-accept.
        """
        products = [{"product_id": "P1", "product_name": "수분밤 크림", "brand_name": "브랜드"}]
        idx = ProductIndex.build(products)
        r = match_product("브랜드", "수분밥 크림", idx)
        assert r.match_status == MatchStatus.QUARANTINE
        assert r.match_method == "fuzzy_manual_review"
        # Only reachable via jamo decomposition; syllable-level tops out ~0.833.
        assert r.match_score >= 0.90

    def test_space_and_symbol_noise_recovers_from_no_match_to_review_band(self):
        """Real mockdata-derived example: the catalog stores a
        space/paren-decorated name while the review text is a clean
        compact one. Baseline (syllable-level only) scores this ~0.743 —
        below the 0.80 floor, i.e. silently dropped as `no_match`. With
        Korean-aware normalization it clears the manual-review floor
        instead, surfacing the candidate rather than losing it.
        """
        products = [{"product_id": "P1", "product_name": "워터뱅크블루히알루로닉세럼 (대용량)", "brand_name": "라네즈"}]
        idx = ProductIndex.build(products)
        r = match_product("라네즈", "워터뱅크 블루 히알루로닉 세럼", idx)
        assert r.match_status == MatchStatus.QUARANTINE
        assert r.match_method == "fuzzy_manual_review"
        assert r.matched_product_id == "P1"
        assert r.match_score >= FUZZY_MANUAL_REVIEW

    def test_ascii_only_fuzzy_unaffected(self, index):
        """Pure-ASCII queries must not engage the Korean-aware path at all:
        the score must equal the plain SequenceMatcher ratio, unchanged.
        """
        r = match_product("LANEIGE", "Lip Sleeping Mask Intense", index)
        expected = SequenceMatcher(
            None,
            normalize_text("Lip Sleeping Mask Intense"),
            normalize_text("Lip Sleeping Mask"),
        ).ratio()
        assert r.matched_product_id == "P001"
        assert r.match_score == pytest.approx(expected)

    def test_ascii_only_candidate_not_boosted_by_korean_query(self):
        """Regression: gating the Korean-aware blend on the query alone is
        not enough. Before the candidate-side `_has_hangul` gate, a
        Hangul-containing query with embedded ASCII tokens ("365days
        step3") could realign against a pure-ASCII candidate after
        punctuation/space stripping and NFD normalization, more than
        doubling the score (0.130 plain -> 0.273 "Korean-aware") for a
        comparison that is semantically meaningless (no Hangul on the
        candidate side to justify jamo decomposition at all).

        With the fix, an ASCII-only candidate's score must equal the plain
        SequenceMatcher ratio exactly, regardless of the query's script.
        """
        products = [
            {"product_id": "ASCII1", "product_name": "No.5 Essence Toner Pad 70ea", "brand_name": "브랜드"},
        ]
        idx = ProductIndex.build(products)
        r = match_product("브랜드", "키즈 365days step3 칫솔", idx)
        expected = SequenceMatcher(
            None,
            normalize_text("키즈 365days step3 칫솔"),
            normalize_text("No.5 Essence Toner Pad 70ea"),
        ).ratio()
        assert r.match_score == pytest.approx(expected)
        assert r.match_score == pytest.approx(0.13043478260869565)
