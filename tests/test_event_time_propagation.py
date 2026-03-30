"""Tests for event_time propagation: created_at → UTC → window_ts → aggregate."""

import pytest
from src.ingest.review_ingest import RawReviewRecord, ingest_review


class TestEventTimeParsing:
    def test_created_at_parsed_to_utc(self):
        record = RawReviewRecord(
            brnd_nm="A", prod_nm="B", text="good", clct_site_nm="src",
            created_at="2025-06-15T10:30:00",
        )
        result = ingest_review(record)
        assert result.review_raw["event_time_utc"] is not None
        assert result.review_raw["event_time_source"] == "SOURCE_CREATED"

    def test_collected_at_fallback(self):
        record = RawReviewRecord(
            brnd_nm="A", prod_nm="B", text="good", clct_site_nm="src",
            collected_at="2025-06-15",
        )
        result = ingest_review(record)
        assert result.review_raw["event_time_utc"] is not None
        assert result.review_raw["event_time_source"] == "COLLECTED_AT"

    def test_no_timestamp_uses_processing_time(self):
        record = RawReviewRecord(
            brnd_nm="A", prod_nm="B", text="good", clct_site_nm="src",
        )
        result = ingest_review(record)
        assert result.review_raw["event_time_utc"] is not None
        assert result.review_raw["event_time_source"] == "PROCESSING_TIME"

    def test_event_time_never_none(self):
        """event_time_utc must NEVER be None after ingest."""
        record = RawReviewRecord(
            brnd_nm="A", prod_nm="B", text="good", clct_site_nm="src",
            created_at="invalid-date-string",
        )
        result = ingest_review(record)
        assert result.review_raw["event_time_utc"] is not None

    def test_source_row_num_in_review_id(self):
        r1 = RawReviewRecord(brnd_nm="A", prod_nm="B", text="좋아요", clct_site_nm="src", source_row_num="1")
        r2 = RawReviewRecord(brnd_nm="A", prod_nm="B", text="좋아요", clct_site_nm="src", source_row_num="2")
        i1 = ingest_review(r1)
        i2 = ingest_review(r2)
        assert i1.review_id != i2.review_id
