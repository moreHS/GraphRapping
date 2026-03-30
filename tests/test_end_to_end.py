"""
End-to-end test: raw review → Layer 2 facts → Layer 3 signals.

Validates the full pipeline from ingest to serving signal generation.
"""

import pytest
from src.ingest.review_ingest import RawReviewRecord, ingest_review
from src.link.product_matcher import ProductIndex, match_product
from src.link.placeholder_resolver import resolve_placeholders
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.date_splitter import split_date
from src.canonical.canonical_fact_builder import CanonicalFactBuilder, CanonicalEntity, FactProvenance
from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter
from src.common.ids import make_product_iri
from src.common.enums import ObjectRefKind, DateSubType


# Sample Korean beauty review (from PLAN docs)
SAMPLE_REVIEW = RawReviewRecord(
    brnd_nm="LANEIGE",
    clct_site_nm="Sephora",
    prod_nm="Lip Sleeping Mask Intense Hydration with Vitamin C",
    text="I love using this it's super thick and hydrates my lips",
    ner=[
        {"word": "I", "entity_group": "PER", "start": 0, "end": 1, "sentiment": "중립"},
        {"word": "it", "entity_group": "PRD", "start": 18, "end": 20, "sentiment": "중립"},
        {"word": "my", "entity_group": "PER", "start": 46, "end": 48, "sentiment": "중립"},
        {"word": "Reviewer", "entity_group": "PER", "start": None, "end": None, "sentiment": "중립"},
        {"word": "Review Target", "entity_group": "PRD", "start": None, "end": None, "sentiment": "중립"},
    ],
    bee=[
        {"word": "super thick and hydrates my lips", "entity_group": "보습력", "start": 22, "end": 53, "sentiment": "긍정"},
    ],
    relation=[
        {"subject": {"word": "I", "entity_group": "PER"}, "object": {"word": "it", "entity_group": "PRD"}, "relation": "uses", "source_type": "NER-NER"},
        {"subject": {"word": "I", "entity_group": "PER"}, "object": {"word": "Reviewer", "entity_group": "PER"}, "relation": "same_entity", "source_type": "NER-NER"},
        {"subject": {"word": "Review Target", "entity_group": "PRD"}, "object": {"word": "super thick and hydrates my lips", "entity_group": "보습력"}, "relation": "has_attribute", "source_type": "NER-BeE"},
    ],
)


@pytest.fixture
def product_index():
    products = [
        {"product_id": "P_LANEIGE_LSM", "product_name": "Lip Sleeping Mask Intense Hydration with Vitamin C", "brand_name": "LANEIGE"},
    ]
    return ProductIndex.build(products)


@pytest.fixture
def bee_normalizer():
    n = BEENormalizer()
    n.load_from_dicts(
        attr_dict={"보습력": {"attr_id": "bee_attr_moisturizing_power", "label_ko": "보습력"}},
        keyword_map={"hydrates": [{"keyword_id": "kw_hydrating", "label_ko": "보습좋음"}]},
    )
    return n


@pytest.fixture
def relation_canon():
    c = RelationCanonicalizer()
    c.load_from_dict({"uses": "uses", "same_entity": "same_entity", "has_attribute": "has_attribute"})
    return c


class TestEndToEnd:
    def test_full_pipeline(self, product_index, bee_normalizer, relation_canon):
        """Test: raw review → review_id → product match → placeholder → BEE normalize → canonical facts → signals."""

        # Step 1: Ingest
        ingested = ingest_review(SAMPLE_REVIEW, source="sephora")
        assert ingested.review_id.startswith("review:Sephora:")
        assert ingested.reviewer_proxy_id.startswith("reviewer_proxy:")

        # Step 2: Product match
        match = match_product(SAMPLE_REVIEW.brnd_nm, SAMPLE_REVIEW.prod_nm, product_index)
        assert match.matched_product_id == "P_LANEIGE_LSM"
        target_product_iri = make_product_iri(match.matched_product_id)

        # Step 3: Placeholder resolution
        resolution = resolve_placeholders(
            ner_rows=ingested.ner_rows,
            rel_rows=ingested.rel_rows,
            review_id=ingested.review_id,
            target_product_iri=target_product_iri,
            reviewer_proxy_iri=ingested.reviewer_proxy_id,
        )
        # Review Target → product
        rt_idx = next(i for i, n in enumerate(ingested.ner_rows) if n["mention_text"] == "Review Target")
        assert resolution.resolved_mentions[rt_idx].resolved_iri == target_product_iri

        # I and Reviewer merged via same_entity → both proxy
        i_idx = next(i for i, n in enumerate(ingested.ner_rows) if n["mention_text"] == "I")
        assert resolution.resolved_mentions[i_idx].resolved_iri == ingested.reviewer_proxy_id

        # Step 4: BEE normalization
        bee_result = bee_normalizer.normalize(
            phrase_text="super thick and hydrates my lips",
            bee_attr_raw="보습력",
            raw_sentiment="긍정",
        )
        assert bee_result.bee_attr_id == "bee_attr_moisturizing_power"
        assert bee_result.polarity == "POS"

        # Step 5: Relation canonicalization
        for rel in ingested.rel_rows:
            canon = relation_canon.canonicalize(rel["relation_raw"])
            if canon.action == "KEEP":
                assert canon.canonical_predicate in ("uses", "has_attribute")

        # Step 6: Build canonical facts
        builder = CanonicalFactBuilder()

        # BEE facts
        bee_fact_ids = builder.add_bee_facts(
            review_id=ingested.review_id,
            product_iri=target_product_iri,
            bee_attr_id=bee_result.bee_attr_id,
            bee_attr_label=bee_result.bee_attr_label,
            keyword_ids=bee_result.keyword_ids,
            keyword_labels=bee_result.keyword_labels,
            polarity=bee_result.polarity,
        )
        assert len(bee_fact_ids) >= 1  # at least HAS_ATTRIBUTE

        # Step 7: Emit signals
        registry = ProjectionRegistry()
        # Minimal registry for test
        registry._rules = {}
        from src.wrap.projection_registry import ProjectionKey, ProjectionRule
        registry._rules[ProjectionKey("has_attribute", "Product", "BEEAttr", "")] = ProjectionRule(
            registry_version="v1", input_predicate="has_attribute",
            subject_type="Product", object_type="BEEAttr", polarity="",
            qualifier_required=False, qualifier_type="",
            output_signal_family="BEE_ATTR", output_edge_type="HAS_BEE_ATTR_SIGNAL",
            output_dst_type="BEEAttr", output_transform="identity",
            output_weight_rule="default", if_unresolved_action="", notes="",
        )
        registry._version = "v1"

        emitter = SignalEmitter(registry)
        for fact in builder.facts:
            emitter.emit_from_fact(
                fact=fact,
                target_product_id=match.matched_product_id,
            )

        # Verify signals generated
        assert emitter.signal_count >= 1

    def test_acceptance_criteria(self, product_index, bee_normalizer, relation_canon):
        """Verify key acceptance criteria from plan."""
        ingested = ingest_review(SAMPLE_REVIEW, source="sephora")

        # AC1: review_id, reviewer_proxy_id always generated
        assert ingested.review_id
        assert ingested.reviewer_proxy_id

        # AC3: Placeholder resolved
        match = match_product(SAMPLE_REVIEW.brnd_nm, SAMPLE_REVIEW.prod_nm, product_index)
        resolution = resolve_placeholders(
            ner_rows=ingested.ner_rows,
            rel_rows=ingested.rel_rows,
            review_id=ingested.review_id,
            target_product_iri=make_product_iri(match.matched_product_id),
            reviewer_proxy_iri=ingested.reviewer_proxy_id,
        )
        resolved_types = {r.resolution_type for r in resolution.resolved_mentions.values()}
        assert "PRODUCT_TARGET" in resolved_types
        assert "REVIEWER_PROXY" in resolved_types

        # AC4: BEE → BEE_ATTR + KEYWORD
        bee_result = bee_normalizer.normalize("super thick and hydrates my lips", "보습력", "긍정")
        assert bee_result.bee_attr_id  # BEE_ATTR preserved
        assert bee_result.polarity == "POS"

        # AC5: relation 65 canonical preservation
        for rel in ingested.rel_rows:
            result = relation_canon.canonicalize(rel["relation_raw"])
            assert result.action in ("KEEP", "PREPROCESS_ONLY", "DROP")
