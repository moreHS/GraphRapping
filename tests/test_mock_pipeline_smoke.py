"""Smoke tests for the checked-in mock data pipeline."""

import json

from src.web.state import load_demo_data


def test_mock_relation_fixture_generates_at_least_one_signal():
    products = json.load(open("mockdata/product_catalog_es.json", encoding="utf-8"))
    users = json.load(open("mockdata/user_profiles_normalized.json", encoding="utf-8"))

    state = load_demo_data(
        "mockdata/review_triples_raw.json",
        products,
        users,
        max_reviews=15,
        source="test_mock_smoke",
        review_format="relation",
    )

    matched = [
        r for r in state.batch_result.get("review_results", [])
        if r.get("matched_product_id")
    ]

    assert state.review_count == 15
    assert state.product_count == 47
    assert state.user_count == 50
    assert len(matched) > 0
    assert state.batch_result.get("total_signals", 0) > 0
