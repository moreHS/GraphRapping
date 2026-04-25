from src.loaders.rs_jsonl_loader import (
    load_reviews_from_rs_jsonl_with_report,
    summarize_rs_jsonl_contract,
)


def test_relation_empty_bee_records_are_relation_pending():
    stats = summarize_rs_jsonl_contract([
        {"id": "r1", "bee_spans": [{"text": "촉촉", "label": "보습력"}], "relation": []},
    ])

    assert stats["total_records"] == 1
    assert stats["bee_span_count"] == 1
    assert stats["relation_row_count"] == 0
    assert stats["relation_ready_review_count"] == 0
    assert stats["relation_pending_review_count"] == 1
    assert stats["bee_without_relation_count"] == 1


def test_relation_present_records_are_relation_ready():
    stats = summarize_rs_jsonl_contract([
        {
            "id": "r1",
            "bee_spans": [{"text": "촉촉", "label": "보습력"}],
            "relation": [
                {
                    "subject": {"word": "Review Target", "entity_group": "PRD"},
                    "object": {"word": "촉촉", "entity_group": "보습력"},
                    "relation": "has_attribute",
                    "source_type": "NER-BeE",
                },
            ],
        },
    ])

    assert stats["relation_row_count"] == 1
    assert stats["relation_ready_review_count"] == 1
    assert stats["relation_pending_review_count"] == 0
    assert stats["ner_bee_relation_count"] == 1
    assert stats["bee_without_relation_count"] == 0


def test_load_reviews_from_rs_jsonl_with_report_respects_max_count():
    reviews, stats = load_reviews_from_rs_jsonl_with_report(
        "mockdata/review_rs_samples.json",
        max_count=2,
    )

    assert len(reviews) == 2
    assert stats["total_records"] == 2
    assert stats["bee_span_count"] > 0
    assert stats["relation_pending_review_count"] == 2
