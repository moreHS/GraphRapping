"""
P3-3: is_corpus_promoted must use per-window distinct_review_count thresholds.
P3-4: _classify_promotion must apply DROP → QUARANTINE → EVIDENCE_ONLY → PROMOTE
       priority so corpus-quality gates win over downstream markers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.common.enums import PromotionDecision
from src.mart.aggregate_product_signals import (
    AggProductSignalRow,
    aggregate_product_signals,
    is_corpus_promoted,
)
from src.kg.adapter import _classify_promotion
from src.kg.models import KGEdge, KGEntity


def _row(*, window_type: str, distinct_review_count: int,
         avg_confidence: float = 0.7, synthetic_ratio: float = 0.0) -> AggProductSignalRow:
    return AggProductSignalRow(
        target_product_id="p1",
        canonical_edge_type="HAS_BEE_ATTR_SIGNAL",
        dst_node_type="BEEAttr",
        dst_node_id="moisture",
        window_type=window_type,
        review_cnt=distinct_review_count,
        pos_cnt=distinct_review_count,
        neg_cnt=0,
        neu_cnt=0,
        support_count=distinct_review_count,
        score=1.0,
        recent_score=None,
        recent_support_count=None,
        last_seen_at=None,
        window_start=None,
        window_end=None,
        evidence_sample=None,
        distinct_review_count=distinct_review_count,
        avg_confidence=avg_confidence,
        synthetic_ratio=synthetic_ratio,
    )


# ---------------------------------------------------------------------------
# P3-3: window-aware promotion thresholds
# ---------------------------------------------------------------------------

def test_30d_promotes_at_two_reviews() -> None:
    assert is_corpus_promoted(_row(window_type="30d", distinct_review_count=2))


def test_30d_blocks_at_one_review() -> None:
    assert not is_corpus_promoted(_row(window_type="30d", distinct_review_count=1))


def test_90d_blocks_at_two_reviews() -> None:
    assert not is_corpus_promoted(_row(window_type="90d", distinct_review_count=2))


def test_90d_promotes_at_three_reviews() -> None:
    assert is_corpus_promoted(_row(window_type="90d", distinct_review_count=3))


def test_all_blocks_at_two_reviews() -> None:
    assert not is_corpus_promoted(_row(window_type="all", distinct_review_count=2))


def test_all_promotes_at_three_reviews() -> None:
    assert is_corpus_promoted(_row(window_type="all", distinct_review_count=3))


def test_unknown_window_falls_back_to_strict_threshold() -> None:
    # Fallback uses ≥3 (strictest), so 2 reviews on an unknown window stays blocked.
    assert not is_corpus_promoted(_row(window_type="weird", distinct_review_count=2))
    assert is_corpus_promoted(_row(window_type="weird", distinct_review_count=3))


def test_promotion_still_requires_confidence_and_synthetic_ratio() -> None:
    # 30d threshold loosening must not bypass confidence / synthetic gates.
    assert not is_corpus_promoted(
        _row(window_type="30d", distinct_review_count=2, avg_confidence=0.5)
    )
    assert not is_corpus_promoted(
        _row(window_type="30d", distinct_review_count=2, synthetic_ratio=0.6)
    )


# ---------------------------------------------------------------------------
# P3-4: classify_promotion priority order
# ---------------------------------------------------------------------------

def _edge(*, confidence: float | None = 0.7,
          evidence_kind: str | None = None,
          target_linked: bool | None = True) -> KGEdge:
    return KGEdge(
        edge_id="e1",
        subj_entity_id="s1",
        obj_entity_id="o1",
        relation_type="has_attribute",
        confidence=confidence,
        evidence_kind=evidence_kind,
        target_linked=target_linked,
    )


def _entity(entity_type: str) -> KGEntity:
    return KGEntity(
        entity_id="e1",
        entity_type=entity_type,
        word="moisture",
        normalized_value="moisture",
    )


def test_low_confidence_drops_even_when_bee_synthetic() -> None:
    """P3-4: confidence<0.2 wins over BEE_SYNTHETIC marker (was EVIDENCE_ONLY)."""
    edge = _edge(confidence=0.1, evidence_kind="BEE_SYNTHETIC")
    assert _classify_promotion(edge, _entity("BEE_ATTR"), _entity("PRD")) == PromotionDecision.DROP


def test_low_confidence_drops_even_when_auto_keyword() -> None:
    """P3-4: confidence<0.2 wins over AUTO_KEYWORD marker (was QUARANTINE)."""
    edge = _edge(confidence=0.15, evidence_kind="AUTO_KEYWORD")
    assert _classify_promotion(edge, _entity("KEYWORD"), _entity("PRD")) == PromotionDecision.DROP


def test_low_confidence_drops_even_when_bee_unlinked() -> None:
    """P3-4: confidence<0.2 wins over BEE-unlinked condition (was EVIDENCE_ONLY)."""
    edge = _edge(confidence=0.1, target_linked=False)
    assert _classify_promotion(edge, _entity("BEE_ATTR"), _entity("PRD")) == PromotionDecision.DROP


def test_auto_keyword_quarantine_when_confidence_ok() -> None:
    edge = _edge(confidence=0.8, evidence_kind="AUTO_KEYWORD")
    assert _classify_promotion(edge, _entity("KEYWORD"), _entity("PRD")) == PromotionDecision.QUARANTINE


def test_bee_unlinked_evidence_only_when_confidence_ok() -> None:
    edge = _edge(confidence=0.8, target_linked=False)
    assert _classify_promotion(edge, _entity("BEE_ATTR"), _entity("PRD")) == PromotionDecision.KEEP_EVIDENCE_ONLY


def test_bee_synthetic_evidence_only_when_confidence_ok() -> None:
    edge = _edge(confidence=0.8, evidence_kind="BEE_SYNTHETIC", target_linked=True)
    assert _classify_promotion(edge, _entity("BEE_ATTR"), _entity("PRD")) == PromotionDecision.KEEP_EVIDENCE_ONLY


def test_standard_edge_promotes() -> None:
    edge = _edge(confidence=0.8, evidence_kind=None, target_linked=True)
    assert _classify_promotion(edge, _entity("PRD"), _entity("PRD")) == PromotionDecision.PROMOTE


def test_auto_keyword_wins_over_bee_evidence_only_at_normal_confidence() -> None:
    """When both AUTO_KEYWORD and BEE-unlinked apply, QUARANTINE wins per priority."""
    edge = _edge(confidence=0.8, evidence_kind="AUTO_KEYWORD", target_linked=False)
    assert _classify_promotion(edge, _entity("KEYWORD"), _entity("PRD")) == PromotionDecision.QUARANTINE


# ---------------------------------------------------------------------------
# End-to-end: aggregate_product_signals dispatches the 30d ≥2 threshold via
# row.window_type. Caller integration coverage in addition to helper tests.
# ---------------------------------------------------------------------------

def test_aggregate_promotes_30d_signal_with_two_distinct_reviews() -> None:
    """Two distinct 30d reviews on the same (product, edge, dst) must promote.

    Pre-P3-3 this would have failed (≥3 across all windows).
    """
    now = datetime.now(timezone.utc)
    recent_iso = now.isoformat()
    signals = [
        {
            "target_product_id": "p1",
            "edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_type": "BEEAttr",
            "dst_id": "moisture",
            "polarity": "POS",
            "review_id": f"r{i}",
            "window_ts": recent_iso,
            "weight": 0.7,
            # P3-6: avg_confidence is computed from source_confidence (fact-level),
            # not signal weight. Promotion requires avg_confidence ≥ 0.6.
            "source_confidence": 0.7,
            "signal_family": "BEE_ATTR",
        }
        for i in range(2)
    ]
    rows = aggregate_product_signals(signals, now=now)
    rows_30d = [r for r in rows if r.window_type == "30d"]
    assert rows_30d, "no 30d row produced"
    assert all(r.is_promoted for r in rows_30d), \
        f"30d row with distinct_review_count=2 should promote: {[(r.distinct_review_count, r.is_promoted) for r in rows_30d]}"


def test_aggregate_blocks_90d_signal_with_two_distinct_reviews() -> None:
    """Two distinct 90d reviews must still block (90d threshold ≥3)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    # 60 days ago — within 90d but outside 30d window.
    sixty_days_ago = (now - timedelta(days=60)).isoformat()
    signals = [
        {
            "target_product_id": "p1",
            "edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_type": "BEEAttr",
            "dst_id": "moisture",
            "polarity": "POS",
            "review_id": f"r{i}",
            "window_ts": sixty_days_ago,
            "weight": 0.7,
            "source_confidence": 0.7,
            "signal_family": "BEE_ATTR",
        }
        for i in range(2)
    ]
    rows = aggregate_product_signals(signals, now=now)
    rows_90d = [r for r in rows if r.window_type == "90d"]
    assert rows_90d, "no 90d row produced"
    assert all(not r.is_promoted for r in rows_90d), \
        "90d row with distinct_review_count=2 should NOT promote (threshold≥3)"
