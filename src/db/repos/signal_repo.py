"""
Signal repository: wrapped_signal + signal_evidence.

Full-replace per review_id on reprocess (no partial patch).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.db.unit_of_work import UnitOfWork
from src.wrap.signal_emitter import WrappedSignal


def _coerce_timestamptz(value: Any) -> datetime | None:
    """Accept str / datetime / None and return a datetime suitable for asyncpg
    timestamptz binding.

    Wave 4 Task 4: upstream `event_time_utc` is stringified in process_review
    (`str(window_ts) if window_ts else None`), so a string can reach this repo.
    asyncpg refuses string for timestamptz columns; coerce here.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Tolerate naive 'YYYY-MM-DD HH:MM:SS+TZ', ISO, or date-only.
        s = value.strip()
        if not s:
            return None
        # datetime.fromisoformat handles most cases since Python 3.11.
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            # Fall back to date-only by appending midnight UTC.
            try:
                return datetime.fromisoformat(s + "T00:00:00+00:00")
            except ValueError as exc:
                raise ValueError(f"Cannot coerce {value!r} to timestamptz") from exc
    raise TypeError(f"Unsupported window_ts type: {type(value).__name__}")


async def replace_signals_for_review(
    uow: UnitOfWork,
    review_id: str,
    signals: list[WrappedSignal],
    evidence_rows: list[dict[str, Any]],
) -> set[str]:
    """Full-replace all signals and evidence for a review.

    Returns: set of affected target_product_ids (for dirty aggregate).
    """
    # Load existing product_ids before delete (for dirty tracking)
    existing = await uow.fetch(
        "SELECT DISTINCT target_product_id FROM wrapped_signal WHERE review_id = $1",
        review_id,
    )
    old_product_ids = {r["target_product_id"] for r in existing if r["target_product_id"]}

    # Delete existing evidence (cascade-safe: delete evidence first)
    await uow.execute("""
        DELETE FROM signal_evidence WHERE signal_id IN (
            SELECT signal_id FROM wrapped_signal WHERE review_id = $1
        )
    """, review_id)

    # Delete existing signals
    await uow.execute("DELETE FROM wrapped_signal WHERE review_id = $1", review_id)

    # Insert new signals
    for sig in signals:
        # source_fact_ids: DEPRECATED cache-only field. SoT = signal_evidence table.
        # Will be removed in future migration. Do NOT read this field for provenance.
        # Wave 4 Task 4: `window_ts` arrives as an ISO string from upstream
        # (review_ingest stringifies event_time_utc). asyncpg requires a real
        # datetime for `timestamptz` bind, so parse here.
        window_ts_val = _coerce_timestamptz(sig.window_ts)
        await uow.execute("""
            INSERT INTO wrapped_signal (signal_id, review_id, user_id, target_product_id,
                source_fact_ids, signal_family, edge_type, dst_type, dst_id, dst_ref_kind,
                bee_attr_id, keyword_id, polarity, negated, intensity, weight,
                evidence_kind, fact_status, source_confidence, target_linked, attribution_source,
                registry_version, window_ts, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
        """,
            sig.signal_id, sig.review_id, sig.user_id, sig.target_product_id,
            sig.source_fact_ids, sig.signal_family, sig.edge_type,
            sig.dst_type, sig.dst_id, sig.dst_ref_kind,
            sig.bee_attr_id, sig.keyword_id, sig.polarity,
            sig.negated, sig.intensity, sig.weight,
            sig.evidence_kind, sig.fact_status, sig.source_confidence,
            sig.target_linked, sig.attribution_source,
            sig.registry_version, window_ts_val, uow.as_of_ts,
        )

    # Insert new evidence
    for ev in evidence_rows:
        await uow.execute("""
            INSERT INTO signal_evidence (signal_id, fact_id, evidence_rank, contribution)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (signal_id, fact_id, evidence_rank) DO NOTHING
        """,
            ev["signal_id"], ev["fact_id"], ev["evidence_rank"], ev.get("contribution", 1.0),
        )

    # Compute dirty product_ids (old + new)
    new_product_ids = {s.target_product_id for s in signals if s.target_product_id}
    return old_product_ids | new_product_ids


# P0-4: edge types whose dst_id refers to a product entity. Shared by the
# dirty-product helper SQL below and by persist_review_bundle (inline NEW
# extraction) — single source of truth.
PRODUCT_DST_EDGE_TYPES: frozenset[str] = frozenset({
    "COMPARED_WITH_SIGNAL",
    "USED_WITH_PRODUCT_SIGNAL",
})


def normalize_dst_to_raw_product_id(value: str) -> str | None:
    """Normalize a wrapped_signal.dst_id / target_product_id to raw product_id.

    Recognized inputs:
      "product:P001"            → "P001"
      "concept:Product:P001"    → "P001"
      "P001" (no colon)         → "P001"  (raw passthrough)
    Skipped (return None):
      "mention:..."
      "placeholder:..."
      "concept:<not Product>:..."  (e.g. concept:Brand:b1)
      ""
      "product:" / "concept:Product:" (suffix-empty; guard against degenerate input)

    P0-4: Single normalization policy shared by helper SQL output and inline
    bundle extraction in persist.py, so dirty_product_ids always carries raw
    product_id domain. Public so callers outside signal_repo can use it.
    """
    if not value:
        return None
    if value.startswith("product:"):
        stripped = value[len("product:"):]
        return stripped or None  # guard against "product:" with empty suffix
    if value.startswith("concept:Product:"):
        stripped = value[len("concept:Product:"):]
        return stripped or None
    if ":" not in value:
        # Raw product_id (no IRI prefix) — passthrough.
        return value
    # Other IRI forms (mention:, placeholder:, concept:Brand: ...) are not
    # product IDs; skip rather than leak non-raw IDs into dirty set.
    return None


# Backward-compat alias: original implementation used the private name. Keep
# both available so existing imports do not break during the cleanup window.
_normalize_dst_to_raw_product_id = normalize_dst_to_raw_product_id


async def get_dirty_product_ids_for_review(
    uow: UnitOfWork,
    review_id: str,
) -> set[str]:
    """Union of dirty product_ids that should be re-aggregated when this review
    is modified or tombstoned.

    Includes:
      - target_product_id of all signals for this review
      - comparison/co-use dst_ids (COMPARED_WITH_SIGNAL, USED_WITH_PRODUCT_SIGNAL)

    Domain: raw product_id (e.g. "P001"), normalized via
    normalize_dst_to_raw_product_id(). Non-product IRIs are skipped.

    P0-4: Operational source-of-truth for dirty-product collection. Called
    by persist_review_bundle() (pre-replace OLD state) and handle_tombstone().

    Single-writer assumption: caller is responsible for ensuring no concurrent
    writer modifies this review's signals during the same transaction.
    """
    rows = await uow.fetch(
        """
        SELECT DISTINCT target_product_id AS pid FROM wrapped_signal WHERE review_id = $1
        UNION
        SELECT DISTINCT dst_id AS pid FROM wrapped_signal
        WHERE review_id = $1 AND edge_type = ANY($2::text[])
        """,
        review_id,
        list(PRODUCT_DST_EDGE_TYPES),
    )
    result: set[str] = set()
    for r in rows:
        normalized = normalize_dst_to_raw_product_id(r[0] or "")
        if normalized is not None:
            result.add(normalized)
    return result
