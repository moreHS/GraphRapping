"""
Product-side aggregate: wrapped_signal → agg_product_signal (windowed).

Windows: 30d, 90d, all
Score: (pos - neg) / total, with evidence sample.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.common.enums import WindowType, SCORING_EXCLUDED_FAMILIES, SignalFamily


@dataclass
class AggProductSignalRow:
    target_product_id: str
    canonical_edge_type: str
    dst_node_type: str
    dst_node_id: str
    window_type: str
    review_cnt: int
    pos_cnt: int
    neg_cnt: int
    neu_cnt: int
    support_count: int
    score: float
    recent_score: float | None
    recent_support_count: int | None
    last_seen_at: str | None
    window_start: str | None
    window_end: str | None
    evidence_sample: list[dict] | None
    # Phase 4: Corpus promotion fields
    distinct_review_count: int = 0
    avg_confidence: float = 0.0
    synthetic_ratio: float = 0.0
    corpus_weight: float = 0.0
    is_promoted: bool = False


def is_corpus_promoted(row: AggProductSignalRow) -> bool:
    """Check if a signal group meets corpus promotion thresholds."""
    return (
        row.distinct_review_count >= 3
        and row.avg_confidence >= 0.6
        and row.synthetic_ratio <= 0.5
    )


def aggregate_product_signals(
    signals: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[AggProductSignalRow]:
    """Aggregate wrapped signals into product signal rows.

    Args:
        signals: List of wrapped_signal dicts with at least:
            target_product_id, edge_type, dst_type, dst_id, polarity, review_id, window_ts
        now: Reference time for windowing (default: utcnow)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    windows = {
        WindowType.D30: now - timedelta(days=30),
        WindowType.D90: now - timedelta(days=90),
        WindowType.ALL: datetime.min.replace(tzinfo=timezone.utc),
    }

    # Group by (product, edge_type, dst_id)
    GroupKey = tuple[str, str, str, str]  # product, edge_type, dst_type, dst_id
    groups: dict[GroupKey, list[dict]] = defaultdict(list)

    for sig in signals:
        # Skip catalog_validation from aggregation used in scoring
        family = sig.get("signal_family", "")
        if family == SignalFamily.CATALOG_VALIDATION.value:
            continue

        key: GroupKey = (
            sig.get("target_product_id", ""),
            sig.get("edge_type", ""),
            sig.get("dst_type", ""),
            sig.get("dst_id", ""),
        )
        groups[key].append(sig)

    results: list[AggProductSignalRow] = []

    for (product_id, edge_type, dst_type, dst_id), sigs in groups.items():
        for window_type, window_start in windows.items():
            window_sigs = [
                s for s in sigs
                if _parse_ts(s.get("window_ts")) >= window_start
            ] if window_type != WindowType.ALL else sigs

            if not window_sigs:
                continue

            review_ids = set()
            pos = neg = neu = 0
            last_ts = None
            evidence = []

            for s in window_sigs:
                review_ids.add(s.get("review_id", ""))
                pol = (s.get("polarity") or "").upper()
                if pol == "POS":
                    pos += 1
                elif pol == "NEG":
                    neg += 1
                else:
                    neu += 1

                ts = s.get("window_ts")
                if ts and (last_ts is None or str(ts) > str(last_ts)):
                    last_ts = ts

                if len(evidence) < 5:
                    evidence.append({
                        "review_id": s.get("review_id"),
                        "fact_id": s.get("source_fact_ids", [None])[0] if isinstance(s.get("source_fact_ids"), list) else s.get("source_fact_id"),
                        "polarity": s.get("polarity"),
                    })

            total = pos + neg + neu
            score = (pos - neg) / total if total > 0 else 0.0

            # Corpus promotion metrics
            distinct_review_count = len(review_ids)
            confidences = [s.get("weight", 1.0) or 1.0 for s in window_sigs]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            synthetic_count = sum(1 for s in window_sigs if s.get("evidence_kind") == "BEE_SYNTHETIC")
            synthetic_ratio = synthetic_count / total if total > 0 else 0.0
            # Corpus weight: support × confidence × recency
            recency_factor = 1.0 if window_type == WindowType.D30 else (0.8 if window_type == WindowType.D90 else 0.6)
            corpus_weight = round(distinct_review_count * avg_confidence * recency_factor, 4)

            row = AggProductSignalRow(
                target_product_id=product_id,
                canonical_edge_type=edge_type,
                dst_node_type=dst_type,
                dst_node_id=dst_id,
                window_type=window_type.value,
                review_cnt=distinct_review_count,
                pos_cnt=pos,
                neg_cnt=neg,
                neu_cnt=neu,
                support_count=total,
                score=round(score, 4),
                recent_score=None,
                recent_support_count=None,
                last_seen_at=str(last_ts) if last_ts else None,
                window_start=window_start.date().isoformat() if window_type != WindowType.ALL else None,
                window_end=now.date().isoformat(),
                evidence_sample=evidence if evidence else None,
                distinct_review_count=distinct_review_count,
                avg_confidence=round(avg_confidence, 4),
                synthetic_ratio=round(synthetic_ratio, 4),
                corpus_weight=corpus_weight,
            )
            row.is_promoted = is_corpus_promoted(row)
            results.append(row)

    return results


def _parse_ts(ts: Any) -> datetime:
    if ts is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)
