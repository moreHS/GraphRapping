"""Tests: mock review contract — stable keys and fallback."""
import json
from src.loaders.relation_loader import load_reviews_from_json

def test_source_review_key_present():
    """v260605 refresh: all 906 reviews have stable source_review_key."""
    reviews = load_reviews_from_json("mockdata/review_triples_raw.json")
    keys_present = [r for r in reviews if r.source_review_key]
    assert len(keys_present) == 906

def test_author_key_present():
    """v260605 refresh: 906 reviews bucketed into 150 author identities
    (hashlib.sha256 modulo 150, deterministic)."""
    reviews = load_reviews_from_json("mockdata/review_triples_raw.json")
    authors = [r for r in reviews if r.author_key]
    assert len(authors) == 906
    distinct = set(r.author_key for r in authors)
    assert len(distinct) == 150

def test_fallback_without_keys():
    """Verify loader works with records that lack stable keys."""
    record = {
        "brnd_nm": "테스트",
        "clct_site_nm": "test",
        "prod_nm": "테스트상품",
        "text": "좋아요",
        "ner": [],
        "bee": [],
        "relation": [],
    }
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump([record], f, ensure_ascii=False)
        tmp_path = f.name
    try:
        reviews = load_reviews_from_json(tmp_path)
        assert len(reviews) == 1
        assert reviews[0].source_review_key is None
        assert reviews[0].author_key is None
    finally:
        os.unlink(tmp_path)
