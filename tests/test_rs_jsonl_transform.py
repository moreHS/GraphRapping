"""Tests: rs.jsonl → RawReviewRecord transform."""
from src.loaders.rs_jsonl_loader import load_reviews_from_rs_jsonl


def test_own_record_transform():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json", max_count=1)
    assert len(reviews) == 1
    r = reviews[0]
    assert r.text  # non-empty
    assert r.source_review_key  # id preserved
    assert r.clct_site_nm  # channel mapped to site name


def test_ner_label_mapping():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json", max_count=10)
    for r in reviews:
        for ner in r.ner:
            # Labels should be mapped to GraphRapping format
            assert ner["entity_group"] in ("AGE", "VOL", "COL", "BRD", "CAT", ""), \
                f"Unmapped NER label: {ner['entity_group']}"


def test_bee_spans_preserved():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json", max_count=5)
    has_bee = any(len(r.bee) > 0 for r in reviews)
    assert has_bee, "At least some reviews should have BEE spans"


def test_channel_to_site():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json")
    sites = {r.clct_site_nm for r in reviews}
    # Should have mapped channel codes to site names
    assert len(sites) > 1  # Multiple channels


def test_own_vs_extn_fields():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json")
    # Own source reviews (first 10) should have author_key from demographics
    own_reviews = [r for r in reviews[:10] if r.author_key]
    assert len(own_reviews) > 0


def test_relation_empty_but_present():
    reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json", max_count=5)
    for r in reviews:
        assert isinstance(r.relation, list)  # Empty list is fine
