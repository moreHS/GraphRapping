"""Tests for idempotency: same review processed twice → no duplicates."""

import pytest
from src.canonical.canonical_fact_builder import CanonicalFactBuilder, FactProvenance
from src.common.ids import make_product_iri


class TestIdempotency:
    def test_same_bee_facts_no_duplicate(self):
        """Processing same BEE facts twice produces 1 fact with 2 provenance entries."""
        builder = CanonicalFactBuilder()
        product_iri = make_product_iri("P1")

        for run in range(2):
            prov = FactProvenance(
                raw_table="bee_raw", raw_row_id=f"0_run{run}",
                review_id="rv1", snippet="착붙해요", source_modality="BEE",
            )
            builder.add_bee_facts(
                review_id="rv1", product_iri=product_iri,
                bee_attr_id="bee_attr_adhesion", bee_attr_label="밀착력",
                keyword_ids=["kw_good"], keyword_labels=["밀착좋음"],
                polarity="POS", provenance=prov,
            )

        # HAS_ATTRIBUTE fact should be 1 (deduped by fact_id)
        attr_facts = [f for f in builder.facts if f.predicate == "has_attribute"]
        assert len(attr_facts) == 1
        # But should have 2 provenance entries
        assert len(attr_facts[0].provenance) == 2
        assert len(attr_facts[0].source_modalities) >= 1

    def test_same_review_id_deterministic(self):
        """Same inputs → same review_id."""
        from src.ingest.review_ingest import RawReviewRecord, ingest_review
        record = RawReviewRecord(brnd_nm="A", prod_nm="B", text="good", clct_site_nm="src")
        r1 = ingest_review(record, source="test")
        r2 = ingest_review(record, source="test")
        assert r1.review_id == r2.review_id
