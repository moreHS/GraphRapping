"""Tests for placeholder resolver (union-find + Review Target/Reviewer)."""

import pytest
from src.link.placeholder_resolver import resolve_placeholders, UnionFind


class TestUnionFind:
    def test_basic_merge(self):
        uf = UnionFind()
        uf.union("I", "my")
        assert uf.find("I") == uf.find("my")

    def test_transitive_merge(self):
        uf = UnionFind()
        uf.union("I", "my")
        uf.union("my", "myself")
        assert uf.find("I") == uf.find("myself")

    def test_groups(self):
        uf = UnionFind()
        uf.union("I", "my")
        uf.union("it", "this")
        groups = uf.groups()
        assert len(groups) == 2


class TestPlaceholderResolution:
    def test_review_target_resolved(self):
        ner_rows = [
            {"mention_text": "Review Target", "entity_group": "PRD"},
            {"mention_text": "Reviewer", "entity_group": "PER"},
            {"mention_text": "I", "entity_group": "PER"},
        ]
        rel_rows = [
            {"relation_raw": "same_entity", "subj_text": "I", "obj_text": "Reviewer"},
        ]
        result = resolve_placeholders(
            ner_rows=ner_rows,
            rel_rows=rel_rows,
            review_id="rv_1",
            target_product_iri="product:P001",
            reviewer_proxy_iri="reviewer_proxy:rv_1",
        )
        # Review Target → product
        assert result.resolved_mentions[0].resolved_iri == "product:P001"
        assert result.resolved_mentions[0].resolution_type == "PRODUCT_TARGET"

        # Reviewer → proxy
        assert result.resolved_mentions[1].resolved_iri == "reviewer_proxy:rv_1"
        assert result.resolved_mentions[1].resolution_type == "REVIEWER_PROXY"

        # I merged with Reviewer → also proxy
        assert result.resolved_mentions[2].resolved_iri == "reviewer_proxy:rv_1"

    def test_unresolved_product(self):
        ner_rows = [
            {"mention_text": "Review Target", "entity_group": "PRD"},
        ]
        result = resolve_placeholders(
            ner_rows=ner_rows,
            rel_rows=[],
            review_id="rv_2",
            target_product_iri=None,  # quarantined
            reviewer_proxy_iri="reviewer_proxy:rv_2",
        )
        assert result.resolved_mentions[0].resolution_type == "UNRESOLVED"
        assert result.unresolved_count >= 1
