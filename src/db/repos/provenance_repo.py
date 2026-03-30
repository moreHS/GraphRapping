"""
Provenance repository: signal_evidence + fact_provenance queries for explanation.
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def get_signal_evidence(pool: asyncpg.Pool, signal_id: str) -> list[dict]:
    """Get signal_evidence rows for a signal, ordered by evidence_rank."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT signal_id, fact_id, evidence_rank, contribution
            FROM signal_evidence
            WHERE signal_id = $1
            ORDER BY evidence_rank
        """, signal_id)
        return [dict(r) for r in rows]


async def get_fact_provenance(pool: asyncpg.Pool, fact_id: str) -> list[dict]:
    """Get fact_provenance rows for a fact, ordered by evidence_rank."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT fact_id, raw_table, raw_row_id, review_id,
                   snippet, start_offset, end_offset, source_modality, evidence_rank
            FROM fact_provenance
            WHERE fact_id = $1
            ORDER BY evidence_rank
        """, fact_id)
        return [dict(r) for r in rows]


async def get_review_snippet(pool: asyncpg.Pool, review_id: str,
                              start_offset: int | None = None,
                              end_offset: int | None = None) -> str | None:
    """Get review text snippet. If offsets given, extract substring."""
    async with pool.acquire() as conn:
        text = await conn.fetchval(
            "SELECT review_text FROM review_raw WHERE review_id = $1", review_id,
        )
        if text is None:
            return None
        if start_offset is not None and end_offset is not None:
            return text[start_offset:end_offset]
        return text


async def get_explanation_chain(pool: asyncpg.Pool, signal_id: str) -> list[dict]:
    """Full provenance chain: signal → evidence → fact → provenance → raw snippet."""
    evidence = await get_signal_evidence(pool, signal_id)
    chain = []
    for ev in evidence:
        prov_list = await get_fact_provenance(pool, ev["fact_id"])
        for prov in prov_list:
            snippet = prov.get("snippet")
            if not snippet and prov.get("review_id"):
                snippet = await get_review_snippet(
                    pool, prov["review_id"],
                    prov.get("start_offset"), prov.get("end_offset"),
                )
            chain.append({
                "signal_id": signal_id,
                "fact_id": ev["fact_id"],
                "evidence_rank": ev["evidence_rank"],
                "raw_table": prov.get("raw_table"),
                "raw_row_id": prov.get("raw_row_id"),
                "review_id": prov.get("review_id"),
                "snippet": snippet,
                "source_modality": prov.get("source_modality"),
            })
    return chain
