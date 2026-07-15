"""Phase 7 B1 — Korean morphology folding + keyword path unification.

Covers:
  1. korean_morph.stem_candidates / has_negation primitives.
  2. bee_normalizer.resolve_surface_keywords: substring pass (legacy),
     morphology pass (new), and the negation guard that keeps folding from
     flipping polarity.
  3. BEENormalizer integration: inflected surfaces resolve; negation flips
     polarity, never the concept.
  4. mention_extractor candidate-queue unification: a dictionary-resolvable
     BEE phrase is routed to the keyword path (signal), not unknown_keyword
     quarantine.
"""

from __future__ import annotations

from src.normalize import korean_morph
from src.normalize.bee_normalizer import BEENormalizer, resolve_surface_keywords
from src.ingest.review_ingest import RawReviewRecord
from src.jobs.run_daily_pipeline import process_review
from src.link.product_matcher import ProductIndex
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.qa.quarantine_handler import QuarantineHandler
from src.wrap.projection_registry import ProjectionRegistry


# ---------------------------------------------------------------------------
# 1. korean_morph primitives
# ---------------------------------------------------------------------------


def test_stem_candidates_strips_connective_endings() -> None:
    assert "촉촉" in korean_morph.stem_candidates("촉촉하고")
    assert "시원" in korean_morph.stem_candidates("시원하고")


def test_stem_candidates_folds_hae_contraction_to_ha() -> None:
    # 순해서 → 순해 → 순하  (해→하 adjective contraction)
    cands = korean_morph.stem_candidates("순해서")
    assert "순하" in cands


def test_stem_candidates_rejects_too_short_stem() -> None:
    # Stripping would leave a 1-char stem → refused (never returns <2 chars).
    assert all(len(c) >= 2 for c in korean_morph.stem_candidates("하고"))


def test_stem_candidates_p_irregular_is_a_miss_not_a_wrong_fold() -> None:
    # Documented limitation: ㅂ-irregular (부드러워요 → dict 부드럽) is not
    # reconciled. It must never fold to an unrelated stem — a miss is fine.
    cands = korean_morph.stem_candidates("부드러워요")
    assert "부드럽" not in cands  # not wrongly reconstructed


def test_has_negation_detects_markers() -> None:
    assert korean_morph.has_negation("촉촉하지 않아요")
    assert korean_morph.has_negation("안 촉촉해요")
    assert korean_morph.has_negation("끈적이지 않고")
    assert not korean_morph.has_negation("촉촉하고 좋아요")


# ---------------------------------------------------------------------------
# 2. resolve_surface_keywords passes
# ---------------------------------------------------------------------------

_MAP = {"순하": [{"keyword_id": "kw_mild", "label_ko": "순함"}]}


def test_resolve_substring_pass() -> None:
    matches = resolve_surface_keywords("순하고 좋아요", _MAP)
    assert [m[0] for m in matches] == ["kw_mild"]


def test_resolve_morphology_pass_folds_hae_form() -> None:
    # "순해서" has no substring "순하"; only the morphology pass (해→하) resolves.
    assert [m[0] for m in resolve_surface_keywords("순해서 좋아요", _MAP)] == ["kw_mild"]
    # With morphology disabled, the same phrase is unresolved.
    assert resolve_surface_keywords("순해서 좋아요", _MAP, use_morphology=False) == []


def test_resolve_negation_skips_folding() -> None:
    # Folding-only match ("순해" → 순하) under negation must be suppressed so a
    # "not mild" statement never asserts the bare 순함 concept. Substring cannot
    # match here (phrase has 순해, dict has 순하), so the result is empty.
    assert resolve_surface_keywords("순해 보이지 않아요", _MAP) == []


def test_resolve_dedups_by_keyword_id() -> None:
    dup_map = {
        "촉촉": [{"keyword_id": "kw_moist", "label_ko": "촉촉함"}],
        "촉촉한": [{"keyword_id": "kw_moist", "label_ko": "촉촉함"}],
    }
    # apply_alias=False isolates the raw dedup mechanic from B2 alias policy.
    matches = resolve_surface_keywords("촉촉한 느낌", dup_map, apply_alias=False)
    assert [m[0] for m in matches] == ["kw_moist"]


# ---------------------------------------------------------------------------
# 3. BEENormalizer integration (uses the real keyword_surface_map.yaml)
# ---------------------------------------------------------------------------


def test_normalizer_folds_registered_inflections() -> None:
    bn = BEENormalizer()
    bn.load_dictionaries()
    for phrase in ("순하고 좋아요", "순해서 좋아요", "발림성도 좋고", "향도 좋고"):
        result = bn.normalize(phrase, "보습력", "긍정")
        assert result.keyword_ids, f"expected a keyword for {phrase!r}"
        assert result.keyword_source == "DICT"


def test_normalizer_negation_flips_polarity_not_concept() -> None:
    bn = BEENormalizer()
    bn.load_dictionaries()
    result = bn.normalize("촉촉하지 않아요", "보습력", "긍정")
    # concept still recognized (via substring) but polarity is negative —
    # never a wrong positive assertion of the moisture concept. Phase 7 B2
    # folds kw_moist onto canonical kw_moisturizing.
    assert "kw_moisturizing" in result.keyword_ids
    assert result.negated is True
    assert result.polarity == "NEG"


# ---------------------------------------------------------------------------
# 4. mention_extractor candidate-queue unification
# ---------------------------------------------------------------------------


def _process(phrase: str):
    bn = BEENormalizer()
    bn.load_dictionaries()
    registry = ProjectionRegistry()
    registry.load()
    record = RawReviewRecord(
        brnd_nm="Brand",
        prod_nm="Product",
        text=f"이 제품 {phrase}",
        ner=[{"word": "Review Target", "entity_group": "PRD",
              "start": None, "end": None, "sentiment": "중립"}],
        bee=[{"word": phrase, "entity_group": "보습력",
              "start": 5, "end": 5 + len(phrase), "sentiment": "긍정"}],
        relation=[{
            "subject": {"word": "Review Target", "entity_group": "PRD"},
            "object": {"word": phrase, "entity_group": "보습력",
                       "start": 5, "end": 5 + len(phrase)},
            "relation": "has_attribute", "source_type": "NER-BeE",
        }],
    )
    return process_review(
        record=record, source="test",
        product_index=ProductIndex.build([
            {"product_id": "P1", "brand_name": "Brand", "product_name": "Product"}]),
        bee_normalizer=bn, relation_canonicalizer=RelationCanonicalizer(),
        projection_registry=registry, quarantine=QuarantineHandler(),
        predicate_contracts={}, kg_mode="on",
    )


def _unknown_keyword_surfaces(bundle) -> list[str]:
    return [
        e.data.get("surface_text", "")
        for e in bundle.quarantine_entries
        if e.table == "quarantine_unknown_keyword"
    ]


def test_dict_resolvable_inflection_not_quarantined() -> None:
    # "촉촉하고" resolves via the dictionary → keyword path, not quarantine.
    bundle = _process("촉촉하고 좋아요")
    assert not any("촉촉" in s for s in _unknown_keyword_surfaces(bundle))
    keyword_signals = [
        s for s in bundle.wrapped_signals
        if s.edge_type == "HAS_BEE_KEYWORD_SIGNAL"
    ]
    assert keyword_signals, "dictionary-backed keyword signal expected"


def test_registered_stem_not_quarantined() -> None:
    # New B1 stem 발림성 must resolve, keeping the phrase out of quarantine.
    bundle = _process("발림성도 좋고")
    assert _unknown_keyword_surfaces(bundle) == []


def test_open_vocabulary_surface_still_quarantined() -> None:
    # Genuine open-vocab neologism stays quarantined (B3/embedding territory).
    bundle = _process("정착템이라 계속 써요")
    assert _unknown_keyword_surfaces(bundle), "open-vocab surface should quarantine"
