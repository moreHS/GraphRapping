"""
User-side aggregate: canonical_user_fact → agg_user_preference.

Refreshes user preference summary from canonical facts + purchase history.
Weighting: base_confidence × frequency_factor × recency_factor × source_type_weight.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, cast

from src.common.config_loader import load_yaml

_user_weight_config: dict | None = None


def _get_config() -> dict:
    global _user_weight_config
    if _user_weight_config is None:
        try:
            _user_weight_config = load_yaml("user_weighting.yaml")
        except Exception:
            _user_weight_config = {}
    return _user_weight_config


# Fallback defaults (used when config file is absent)
_DEFAULT_SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "purchase": 1.2,
    "chat": 1.0,
    "basic": 0.8,
    "inferred": 0.6,
}
_DEFAULT_RECENCY_LAMBDA = 0.01
_DEFAULT_FREQ_DENOMINATOR = 3.0
_DEFAULT_FREQ_CAP = 1.5


def _source_type_weights() -> dict[str, float]:
    return cast(dict[str, float], _get_config().get("source_type_weights", _DEFAULT_SOURCE_TYPE_WEIGHTS))


def _recency_lambda() -> float:
    return float(_get_config().get("recency_lambda", _DEFAULT_RECENCY_LAMBDA))


def _freq_denominator() -> float:
    freq = cast(dict[str, Any], _get_config().get("frequency", {}))
    return float(freq.get("denominator", _DEFAULT_FREQ_DENOMINATOR))


def _freq_cap() -> float:
    freq = cast(dict[str, Any], _get_config().get("frequency", {}))
    return float(freq.get("cap", _DEFAULT_FREQ_CAP))


def refresh_user_preferences(
    user_id: str,
    canonical_user_facts: list[dict[str, Any]],
    purchase_brand_confidence: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build agg_user_preference rows from canonical user facts.

    Weighting formula:
        weight = max_confidence × freq_factor × recency_factor × source_type_weight

    Args:
        user_id: Target user
        canonical_user_facts: Canonical user facts with confidence/source_modalities
        purchase_brand_confidence: Brand → confidence from purchase history
        now: Reference time for recency (default: utcnow)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Group by (predicate, dst_id)
    grouped: dict[tuple[str, str], dict] = {}

    for fact in canonical_user_facts:
        predicate = fact.get("predicate", "")
        dst_id = fact.get("object_iri", "")
        key = (predicate, dst_id)

        if key not in grouped:
            grouped[key] = {
                "user_id": user_id,
                "preference_edge_type": predicate,
                "dst_node_type": fact.get("object_type", ""),
                "dst_node_id": dst_id,
                "max_confidence": fact.get("confidence", 1.0) or 1.0,
                "sources": set(),
                "count": 0,
                "last_seen_at": None,
                "source_weights": {},
            }

        existing = grouped[key]
        new_conf = fact.get("confidence", 1.0) or 1.0
        if new_conf > existing["max_confidence"]:
            existing["max_confidence"] = new_conf

        existing["count"] += 1

        # Track last_seen_at
        fact_ts = fact.get("last_seen_at")
        if fact_ts:
            if isinstance(fact_ts, str):
                try:
                    fact_ts = datetime.fromisoformat(fact_ts)
                except (ValueError, TypeError):
                    fact_ts = None
            if fact_ts and (existing["last_seen_at"] is None or fact_ts > existing["last_seen_at"]):
                existing["last_seen_at"] = fact_ts

        for mod in fact.get("source_modalities", []):
            existing["sources"].add(mod)
            existing["source_weights"][mod] = _source_type_weights().get(mod, _source_type_weights().get("default", 0.8))

    # Boost brand/category preferences if purchase data exists
    if purchase_brand_confidence:
        for key, row in grouped.items():
            predicate, dst_id = key
            if predicate in ("PREFERS_BRAND", "PREFERS_CATEGORY"):
                brand_conf = purchase_brand_confidence.get(dst_id)
                if brand_conf:
                    row["max_confidence"] = max(row["max_confidence"], brand_conf)
                    row["sources"].add("purchase")
                    row["source_weights"]["purchase"] = _source_type_weights()["purchase"]

    # Convert to output rows with composite weighting
    results = []
    for row in grouped.values():
        # Frequency factor: sub-linear boost for repeated signals
        freq_factor = min(row["count"] / _freq_denominator(), _freq_cap())

        # Recency factor: exponential decay from last_seen_at
        if row["last_seen_at"]:
            last_seen = row["last_seen_at"]
            if not last_seen.tzinfo:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            days_elapsed = (now - last_seen).total_seconds() / 86400.0
            recency_factor = math.exp(-_recency_lambda() * max(days_elapsed, 0))
        else:
            recency_factor = 1.0

        # Source type weight: max across contributing sources
        source_type_weight = max(row["source_weights"].values()) if row["source_weights"] else 0.8

        # Composite weight
        weight = round(row["max_confidence"] * freq_factor * recency_factor * source_type_weight, 4)

        sources = row.pop("sources")
        source_weights = row.pop("source_weights")
        last_seen = row.pop("last_seen_at")

        row["weight"] = weight
        row["support_count"] = row.pop("count")
        row["last_seen_at"] = last_seen.isoformat() if last_seen else None
        row["source_types"] = sorted(sources)
        row["source_mix"] = {src: round(w, 2) for src, w in source_weights.items()} if source_weights else {"sources": sorted(sources)}
        row["recency_weight"] = round(recency_factor, 4)
        row["frequency_weight"] = round(freq_factor, 4)

        results.append(row)

    return results
