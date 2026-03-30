"""Tests for DATE splitter (4-way classification)."""

import pytest
from src.normalize.date_splitter import split_date
from src.common.enums import DateSubType


class TestTemporalContext:
    def test_morning(self):
        r = split_date("아침")
        assert r.kind == DateSubType.TEMPORAL_CONTEXT
        assert r.context_type == "day_part"

    def test_after_cleansing(self):
        r = split_date("세안 후")
        assert r.kind == DateSubType.TEMPORAL_CONTEXT
        assert r.context_type == "routine_step"

    def test_summer(self):
        r = split_date("여름")
        assert r.kind == DateSubType.TEMPORAL_CONTEXT
        assert r.context_type == "season"

    def test_afternoon(self):
        r = split_date("오후")
        assert r.kind == DateSubType.TEMPORAL_CONTEXT
        assert r.context_type == "day_part"


class TestFrequency:
    def test_daily(self):
        r = split_date("매일")
        assert r.kind == DateSubType.FREQUENCY

    def test_once_a_day(self):
        r = split_date("하루에 1번")
        assert r.kind == DateSubType.FREQUENCY

    def test_twice_a_week(self):
        r = split_date("주 2회")
        assert r.kind == DateSubType.FREQUENCY


class TestDuration:
    def test_two_weeks(self):
        r = split_date("2주째")
        assert r.kind == DateSubType.DURATION

    def test_one_month(self):
        r = split_date("한 달 동안")
        assert r.kind == DateSubType.DURATION

    def test_three_days(self):
        r = split_date("3일째")
        assert r.kind == DateSubType.DURATION


class TestAbsoluteDate:
    def test_year(self):
        r = split_date("2024년 여름 세일")
        assert r.kind == DateSubType.ABSOLUTE_DATE

    def test_month_day(self):
        r = split_date("3월 1일")
        assert r.kind == DateSubType.ABSOLUTE_DATE

    def test_iso_date(self):
        r = split_date("2024-03-15")
        assert r.kind == DateSubType.ABSOLUTE_DATE
