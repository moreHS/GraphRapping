"""Tests for reviewer proxy isolation: proxy IRI ≠ real user IRI."""

import pytest
from src.common.ids import make_reviewer_proxy_id
from src.user.adapters.personal_agent_adapter import adapt_user_profile


class TestReviewerIsolation:
    def test_proxy_iri_format_differs_from_user(self):
        pid, _ = make_reviewer_proxy_id("src", author_key="author1")
        assert pid.startswith("reviewer_proxy:")
        assert not pid.startswith("user:")

    def test_review_local_proxy_differs_from_user(self):
        pid, stability = make_reviewer_proxy_id("src", review_id="review:src:123")
        assert stability == "REVIEW_LOCAL"
        assert "reviewer_proxy" in pid

    def test_user_facts_never_contain_proxy(self):
        facts = adapt_user_profile("u1", {
            "basic": {"skin_type": "건성"},
            "purchase_analysis": {"preferred_skincare_brand": ["라네즈"]},
            "chat": None,
        })
        for f in facts:
            assert "reviewer_proxy" not in f.get("concept_id", "")
            assert f["user_id"] == "u1"
