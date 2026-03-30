"""Tests for BEE normalizer: BEE_ATTR + KEYWORD + polarity/negation/intensity."""

import pytest
from src.normalize.bee_normalizer import BEENormalizer


@pytest.fixture
def normalizer():
    n = BEENormalizer()
    n.load_from_dicts(
        attr_dict={
            "밀착력": {"attr_id": "bee_attr_adhesion", "label_ko": "밀착력"},
            "보습력": {"attr_id": "bee_attr_moisturizing_power", "label_ko": "보습력"},
            "Adhesion": {"attr_id": "bee_attr_adhesion", "label_ko": "밀착력"},
        },
        keyword_map={
            "착붙": [{"keyword_id": "kw_adhesion_good", "label_ko": "밀착좋음"}],
            "안 떠요": [{"keyword_id": "kw_low_lifting", "label_ko": "들뜸없음"}],
            "촉촉": [{"keyword_id": "kw_moist", "label_ko": "촉촉함"}],
            "건조": [{"keyword_id": "kw_dry", "label_ko": "건조함"}],
        },
    )
    return n


class TestBEEAttrResolution:
    def test_korean_attr(self, normalizer):
        r = normalizer.normalize("착붙하고 좋아요", "밀착력", "긍정")
        assert r.bee_attr_id == "bee_attr_adhesion"
        assert r.bee_attr_label == "밀착력"

    def test_english_attr(self, normalizer):
        r = normalizer.normalize("good adhesion", "Adhesion", "positive")
        assert r.bee_attr_id == "bee_attr_adhesion"


class TestKeywordExtraction:
    def test_single_keyword(self, normalizer):
        r = normalizer.normalize("착붙해요", "밀착력", "긍정")
        assert "kw_adhesion_good" in r.keyword_ids
        assert "밀착좋음" in r.keyword_labels

    def test_multiple_keywords(self, normalizer):
        r = normalizer.normalize("착붙하고 안 떠요", "밀착력", "긍정")
        assert "kw_adhesion_good" in r.keyword_ids
        assert "kw_low_lifting" in r.keyword_ids


class TestPolarityNegationIntensity:
    def test_positive_sentiment(self, normalizer):
        r = normalizer.normalize("촉촉해요", "보습력", "긍정")
        assert r.polarity == "POS"
        assert r.negated is False

    def test_negation_flips_polarity(self, normalizer):
        r = normalizer.normalize("안 건조해요", "보습력", "부정")
        # negated=True + original NEG → flipped to POS
        assert r.negated is True
        assert r.polarity == "POS"

    def test_low_intensity(self, normalizer):
        r = normalizer.normalize("조금 촉촉해요", "보습력", "긍정")
        assert r.intensity == pytest.approx(0.4)

    def test_high_intensity(self, normalizer):
        r = normalizer.normalize("정말 착붙해요", "밀착력", "긍정")
        assert r.intensity == pytest.approx(1.5)

    def test_normal_intensity(self, normalizer):
        r = normalizer.normalize("착붙해요", "밀착력", "긍정")
        assert r.intensity == pytest.approx(1.0)
