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

            results.append(AggProductSignalRow(
                target_product_id=product_id,
                canonical_edge_type=edge_type,
                dst_node_type=dst_type,
                dst_node_id=dst_id,
                window_type=window_type.value,
                review_cnt=len(review_ids),
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
            ))

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
