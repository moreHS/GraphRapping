"""
Phase 1 tests: semantic metadata preservation through the pipeline.

Tests:
1. Negation passthrough (BEE → CanonicalFact → WrappedSignal)
2. Intensity passthrough
3. Synthetic edge not promoted
4. Auto keyword quarantined (KEYWORD entity not created)
5. BEE_ATTR sentiment split removed (single entity, polarity on edge)
6. Signal dedup with negation (different signals for negated/non-negated)
"""

from __future__ import annotations

import pytest

from src.normalize.bee_normalizer import BEENormalizer, BEENormalizeResult
from src.canonical.canonical_fact_builder import CanonicalFactBuilder, FactProvenance
from src.wrap.signal_emitter import SignalEmitter
from src.wrap.projection_registry import ProjectionRegistry
from src.common.ids import make_signal_id
from src.kg.models import (
    EntityMention, RelationMention, KeywordMention,
    KGEntity, KGEdge, KGResult,
)
from src.kg.mention_extractor import MentionExtractor
from src.kg.canonicalizer import Canonicalizer
from src.kg.adapter import kg_result_to_facts, _classify_promotion
from src.common.enums import PromotionDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bee_normalizer():
    n = BEENormalizer()
    n.load_from_dicts(
        attr_dict={"밀착력": {"attr_id": "adhesion", "label_ko": "밀착력"}},
        keyword_map={"끈적거림": [{"keyword_id": "stickiness", "label_ko": "끈적거림"}]},
    )
    return n


@pytest.fixture
def projection_registry():
    reg = ProjectionRegistry()
    reg.load()
    return reg


# ---------------------------------------------------------------------------
# 1. Negation passthrough
# ---------------------------------------------------------------------------

class TestNegationPassthrough:
    def test_bee_normalizer_detects_negation(self, bee_normalizer):
        result = bee_normalizer.normalize("안 끈적이는 사용감", "밀착력", "긍정")
        assert result.negated is True
        # Negated positive → flips to NEG
        assert result.polarity == "NEG"

    def test_canonical_fact_preserves_negation(self):
        builder = CanonicalFactBuilder()
        fid = builder.add_bee_facts(
            review_id="r1", product_iri="product:P1",
            bee_attr_id="adhesion", bee_attr_label="밀착력",
            keyword_ids=["stickiness"], keyword_labels=["끈적거림"],
            polarity="NEG",
            negated=True,
            intensity=1.5,
            evidence_kind="BEE_DICT",
            base_confidence=1.0,
        )
        assert len(fid) >= 1
        fact = builder.facts[0]
        assert fact.negated is True
        assert fact.intensity == 1.5
        assert fact.evidence_kind == "BEE_DICT"

    def test_wrapped_signal_carries_negation(self, projection_registry):
        builder = CanonicalFactBuilder()
        builder.add_bee_facts(
            review_id="r1", product_iri="product:P1",
            bee_attr_id="adhesion", bee_attr_label="밀착력",
            keyword_ids=["stickiness"], keyword_labels=["끈적거림"],
            polarity="NEG",
            negated=True,
            intensity=1.5,
            evidence_kind="BEE_DICT",
            base_confidence=1.0,
        )
        emitter = SignalEmitter(projection_registry)
        result = emitter.emit_from_facts(builder.facts, target_product_id="P1")
        negated_signals = [s for s in result.signals if s.negated is True]
        assert len(negated_signals) > 0


# ---------------------------------------------------------------------------
# 2. Intensity passthrough
# ---------------------------------------------------------------------------

class TestIntensityPassthrough:
    def test_high_intensity_preserved(self, bee_normalizer):
        result = bee_normalizer.normalize("매우 촉촉한 사용감", "밀착력", "긍정")
        assert result.intensity == 1.5

    def test_intensity_in_canonical_fact(self):
        builder = CanonicalFactBuilder()
        builder.add_bee_facts(
            review_id="r1", product_iri="product:P1",
            bee_attr_id="adhesion", bee_attr_label="밀착력",
            keyword_ids=[], keyword_labels=[],
            polarity="POS",
            intensity=1.5,
        )
        fact = builder.facts[0]
        assert fact.intensity == 1.5


# ---------------------------------------------------------------------------
# 3. Synthetic edge not promoted
# ---------------------------------------------------------------------------

class TestSyntheticNotPromoted:
    def test_classify_bee_synthetic(self):
        edge = KGEdge(
            edge_id="e1", subj_entity_id="s", obj_entity_id="o",
            relation_type="HAS_ATTRIBUTE",
            evidence_kind="BEE_SYNTHETIC",
        )
        decision = _classify_promotion(edge, None, None)
        assert decision == PromotionDecision.KEEP_EVIDENCE_ONLY

    def test_classify_normal_rel(self):
        edge = KGEdge(
            edge_id="e2", subj_entity_id="s", obj_entity_id="o",
            relation_type="used_on", confidence=1.0,
        )
        decision = _classify_promotion(edge, None, None)
        assert decision == PromotionDecision.PROMOTE

    def test_evidence_only_facts_skipped_by_emitter(self, projection_registry):
        builder = CanonicalFactBuilder()
        builder.add_fact(
            review_id="r1", subject_iri="product:P1",
            predicate="has_attribute", object_iri="concept:BEEAttr:adhesion",
            subject_type="Product", object_type="BEEAttr",
            polarity="POS", source_modality="BEE",
            fact_status="EVIDENCE_ONLY",
        )
        emitter = SignalEmitter(projection_registry)
        result = emitter.emit_from_facts(builder.facts, target_product_id="P1")
        assert len(result.signals) == 0  # evidence_only → skipped


# ---------------------------------------------------------------------------
# 4. Auto keyword quarantined
# ---------------------------------------------------------------------------

class TestAutoKeywordQuarantined:
    def test_mention_extractor_routes_to_candidates(self):
        from src.kg.config import KGConfig
        config = KGConfig()
        config.load()
        extractor = MentionExtractor(config)
        extractor.extract(
            review_id="r1", product_id="P1",
            ner_rows=[], rel_rows=[],
            bee_rows=[{"phrase_text": "느낌이 좋다", "bee_attr_raw": "밀착력", "start_offset": 0, "end_offset": 5, "raw_sentiment": "긍정"}],
        )
        # BEE-only mention → synthetic relation with keyword_candidates (not keyword_mentions)
        assert len(extractor.keyword_candidates) > 0
        # Verify the candidate has the expected fields
        cand = extractor.keyword_candidates[0]
        assert "surface_text" in cand
        assert "review_id" in cand

    def test_canonicalizer_skips_candidate_keywords(self):
        from src.kg.config import KGConfig
        config = KGConfig()
        config.load()
        # Create keyword with CANDIDATE source
        kw = KeywordMention(
            review_id="r1", product_id="P1",
            word="테스트키워드", bee_attr_type="밀착력",
            bee_mention_id="m1",
            keyword_source="CANDIDATE",
        )
        canonicalizer = Canonicalizer(config)
        # Create minimal entity for BEE mention
        mention = EntityMention(
            review_id="r1", product_id="P1",
            type="BEE_ATTR", word="좋다", original_type="밀착력",
            sentiment="POS", mention_id="m1",
        )
        result = canonicalizer.process(
            entity_mentions=[mention],
            relation_mentions=[],
            representative_map={},
            keyword_mentions=[kw],
            product_id="P1",
        )
        # CANDIDATE keyword should NOT create a KEYWORD entity
        keyword_entities = [e for e in result.entities if e.entity_type == "KEYWORD"]
        assert len(keyword_entities) == 0


# ---------------------------------------------------------------------------
# 5. BEE_ATTR sentiment split removed
# ---------------------------------------------------------------------------

class TestSentimentSplitRemoved:
    def test_same_attr_different_polarity_one_entity(self):
        from src.kg.config import KGConfig
        config = KGConfig()
        config.load()
        canonicalizer = Canonicalizer(config)
        pos_mention = EntityMention(
            review_id="r1", product_id="P1",
            type="BEE_ATTR", word="촉촉한", original_type="밀착력",
            sentiment="POS", mention_id="m1",
        )
        neg_mention = EntityMention(
            review_id="r1", product_id="P1",
            type="BEE_ATTR", word="안 촉촉한", original_type="밀착력",
            sentiment="NEG", mention_id="m2",
        )
        result = canonicalizer.process(
            entity_mentions=[pos_mention, neg_mention],
            relation_mentions=[],
            representative_map={},
            keyword_mentions=[],
            product_id="P1",
        )
        bee_entities = [e for e in result.entities if e.entity_type == "BEE_ATTR"]
        # Should be ONE entity (밀착력), not two (밀착력_POS + 밀착력_NEG)
        assert len(bee_entities) == 1
        assert bee_entities[0].word == "밀착력"
        # Entity should NOT have polarity in normalized_value
        assert "_POS" not in bee_entities[0].normalized_value
        assert "_NEG" not in bee_entities[0].normalized_value


# ---------------------------------------------------------------------------
# 6. Signal dedup with negation
# ---------------------------------------------------------------------------

class TestSignalDedupWithNegation:
    def test_different_negation_different_signal_ids(self):
        sid_normal = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "concept:BEEAttr:adhesion", "POS", "v1")
        sid_negated = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "concept:BEEAttr:adhesion", "POS", "v1", negated="true")
        assert sid_normal != sid_negated

    def test_same_negation_same_signal_id(self):
        sid1 = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1", negated="true")
        sid2 = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1", negated="true")
        assert sid1 == sid2

    def test_backward_compat_no_negation_same_as_before(self):
        # When negated="" (default), hash should be identical to old format
        sid_new = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1")
        sid_old = make_signal_id("r1", "P1", "HAS_BEE_ATTR_SIGNAL", "dst", "POS", "v1", negated="", qualifier_fingerprint="")
        assert sid_new == sid_old


# ---------------------------------------------------------------------------
# 7. Double negation detection
# ---------------------------------------------------------------------------

class TestDoubleNegation:
    def test_double_negation_not_negated(self, bee_normalizer):
        result = bee_normalizer.normalize("안 건조한 건 아닌데", "밀착력", "부정")
        # Two negation markers → even count → not negated
        assert result.negated is False

    def test_single_negation(self, bee_normalizer):
        result = bee_normalizer.normalize("안 좋다", "밀착력", "긍정")
        assert result.negated is True

    def test_keyword_source_dict(self, bee_normalizer):
        result = bee_normalizer.normalize("끈적거림 없이 좋다", "밀착력", "긍정")
        assert result.keyword_source == "DICT"

    def test_keyword_source_candidate(self, bee_normalizer):
        result = bee_normalizer.normalize("모르는 표현", "밀착력", "긍정")
        assert result.keyword_source == "CANDIDATE"
