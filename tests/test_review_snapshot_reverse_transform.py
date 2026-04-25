from src.db.repos.review_repo import (
    _db_bee_to_record_item,
    _db_ner_to_record_item,
    _db_rel_to_record_item,
)
from src.ingest.review_ingest import RawReviewRecord, ingest_review


def test_db_ner_row_converts_to_raw_review_ner_item():
    item = _db_ner_to_record_item({
        "mention_text": "크림",
        "entity_group": "PRD",
        "start_offset": 3,
        "end_offset": 5,
        "raw_sentiment": "중립",
    })

    assert item == {
        "word": "크림",
        "entity_group": "PRD",
        "start": 3,
        "end": 5,
        "sentiment": "중립",
    }


def test_db_bee_row_converts_to_raw_review_bee_item():
    item = _db_bee_to_record_item({
        "phrase_text": "촉촉해요",
        "bee_attr_raw": "보습력",
        "start_offset": 10,
        "end_offset": 14,
        "raw_sentiment": "긍정",
    })

    assert item == {
        "word": "촉촉해요",
        "entity_group": "보습력",
        "start": 10,
        "end": 14,
        "sentiment": "긍정",
    }


def test_db_rel_row_converts_to_nested_raw_review_relation_item():
    item = _db_rel_to_record_item({
        "subj_text": "크림",
        "subj_group": "PRD",
        "subj_start": 0,
        "subj_end": 2,
        "obj_text": "촉촉해요",
        "obj_group": "보습력",
        "obj_start": 5,
        "obj_end": 9,
        "relation_raw": "has_attribute",
        "source_type": "NER-BeE",
        "raw_sentiment": "긍정",
        "obj_keywords": '["수분감", "보습"]',
    })

    assert item == {
        "subject": {"word": "크림", "entity_group": "PRD", "start": 0, "end": 2},
        "object": {
            "word": "촉촉해요",
            "entity_group": "보습력",
            "start": 5,
            "end": 9,
            "sentiment": "긍정",
            "keywords": ["수분감", "보습"],
        },
        "relation": "has_attribute",
        "source_type": "NER-BeE",
    }


def test_ingest_review_preserves_relation_object_sentiment_and_keywords():
    record = RawReviewRecord(
        brnd_nm="A",
        prod_nm="B",
        text="크림이 촉촉해요",
        clct_site_nm="src",
        relation=[
            {
                "subject": {"word": "크림", "entity_group": "PRD"},
                "object": {
                    "word": "촉촉해요",
                    "entity_group": "보습력",
                    "sentiment": "긍정",
                    "keywords": ["수분감"],
                },
                "relation": "has_attribute",
                "source_type": "NER-BeE",
            },
        ],
    )

    ingested = ingest_review(record)

    assert ingested.rel_rows[0]["raw_sentiment"] == "긍정"
    assert ingested.rel_rows[0]["obj_keywords"] == ["수분감"]
