"""Tests for reranker: diversity bonus + contribution logging."""

import pytest
from src.rec.reranker import rerank, build_contribution_log_rows, RerankedProduct
from src.rec.scorer import ScoredProduct


@pytest.fixture
def scored():
    return [
        ScoredProduct("P1", 0.9, 0.85, 0.85, {"keyword_match": 0.3}, 100),
        ScoredProduct("P2", 0.8, 0.75, 0.75, {"concern_fit": 0.2}, 80),
        ScoredProduct("P3", 0.7, 0.65, 0.65, {"context_match": 0.15}, 60),
        ScoredProduct("P4", 0.6, 0.55, 0.55, {"keyword_match": 0.1}, 50),
    ]


@pytest.fixture
def profiles():
    return {
        "P1": {"brand_id": "B1", "category_id": "C1"},
        "P2": {"brand_id": "B1", "category_id": "C1"},  # same brand/category as P1
        "P3": {"brand_id": "B2", "category_id": "C2"},
        "P4": {"brand_id": "B3", "category_id": "C1"},
    }


class TestDiversityReranking:
    def test_diversity_changes_order(self, scored, profiles):
        """Same-brand products should get penalized after first selection."""
        result = rerank(scored, product_profiles=profiles, diversity_weight=0.15, top_k=4)
        assert len(result) == 4
        # P1 should still be first (highest score)
        assert result[0].product_id == "P1"
        # P2 (same brand B1) should be penalized; P3 (different brand) might move up
        product_order = [r.product_id for r in result]
        # P3 should appear before P2 due to diversity bonus
        assert product_order.index("P3") < product_order.index("P2")

    def test_no_profiles_no_diversity(self, scored):
        """Without profiles, ranking = original score order."""
        result = rerank(scored, product_profiles=None, top_k=4)
        assert [r.product_id for r in result] == ["P1", "P2", "P3", "P4"]

    def test_diversity_bonus_logged(self, scored, profiles):
        result = rerank(scored, product_profiles=profiles, diversity_weight=0.1, top_k=4)
        # At least one product should have non-zero diversity bonus
        bonuses = [r.diversity_bonus for r in result]
        assert any(b != 0.0 for b in bonuses)

    def test_contribution_log_rows(self, scored, profiles):
        result = rerank(scored, product_profiles=profiles, top_k=4)
        log_rows = build_contribution_log_rows(result, run_id=1, user_id="u1")
        assert len(log_rows) == 4
        assert all(r["run_id"] == 1 for r in log_rows)
        assert all(r["user_id"] == "u1" for r in log_rows)
