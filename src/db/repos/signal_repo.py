"""
Signal repository: wrapped_signal + signal_evidence.

Full-replace per review_id on reprocess (no partial patch).
"""

from __future__ import annotations

from typing import Any

from src.db.unit_of_work import UnitOfWork
from src.wrap.signal_emitter import WrappedSignal


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
        await uow.execute("""
            INSERT INTO wrapped_signal (signal_id, review_id, user_id, target_product_id,
                source_fact_ids, signal_family, edge_type, dst_type, dst_id, dst_ref_kind,
                bee_attr_id, keyword_id, polarity, negated, intensity, weight,
                registry_version, window_ts, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
        """,
            sig.signal_id, sig.review_id, sig.user_id, sig.target_product_id,
            sig.source_fact_ids, sig.signal_family, sig.edge_type,
            sig.dst_type, sig.dst_id, sig.dst_ref_kind,
            sig.bee_attr_id, sig.keyword_id, sig.polarity,
            sig.negated, sig.intensity, sig.weight,
            sig.registry_version, sig.window_ts, uow.as_of_ts,
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


async def get_dirty_product_ids_for_review(
    uow: UnitOfWork,
    review_id: str,
) -> set[str]:
    """Get all product_ids that would be dirty if this review is modified/deleted.

    Includes: target_product_id + comparison targets + co-used products.
    """
    rows = await uow.fetch("""
        SELECT DISTINCT target_product_id FROM wrapped_signal WHERE review_id = $1
        UNION
        SELECT DISTINCT dst_id FROM wrapped_signal
        WHERE review_id = $1 AND edge_type IN ('COMPARED_WITH_SIGNAL', 'USED_WITH_PRODUCT_SIGNAL')
    """, review_id)
    return {r[0] for r in rows if r[0]}
