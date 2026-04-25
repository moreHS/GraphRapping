"""
Evidence sampler: selects top-k evidence refs for explanation.

Links signal → fact → raw for provenance chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvidenceRef:
    signal_id: str
    fact_id: str
    evidence_rank: int
    contribution: float
    review_id: str | None = None
    snippet: str | None = None


def sample_evidence(
    signal_id: str,
    fact_ids: list[str],
    fact_provenance_lookup: dict[str, list[dict]] | None = None,
    top_k: int = 5,
) -> list[EvidenceRef]:
    """Select top-k evidence references for a signal.

    Args:
        signal_id: The wrapped signal ID
        fact_ids: List of contributing fact_ids
        fact_provenance_lookup: fact_id → list of provenance rows
        top_k: Maximum evidence refs to return
    """
    refs: list[EvidenceRef] = []
    contribution_per_fact = 1.0 / max(len(fact_ids), 1)

    for rank, fact_id in enumerate(fact_ids[:top_k]):
        review_id = None
        snippet = None

        if fact_provenance_lookup and fact_id in fact_provenance_lookup:
            prov_list = fact_provenance_lookup[fact_id]
            if prov_list:
                best_prov = prov_list[0]
                review_id = best_prov.get("review_id")
                snippet = best_prov.get("snippet")

        refs.append(EvidenceRef(
            signal_id=signal_id,
            fact_id=fact_id,
            evidence_rank=rank,
            contribution=contribution_per_fact,
            review_id=review_id,
            snippet=snippet,
        ))

    return refs
