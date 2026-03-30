"""
Dictionary growth loop: quarantine → cluster → suggest → approve → backfill.

Semi-automatic: surfaces are clustered and candidates suggested,
but approval is manual or rule-based before dictionary update.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import asyncpg

from src.common.text_normalize import normalize_text


@dataclass
class SurfaceCluster:
    representative: str
    members: list[str] = field(default_factory=list)
    count: int = 0
    bee_attrs: set[str] = field(default_factory=set)


@dataclass
class ConceptCandidate:
    surface: str
    suggested_keyword_id: str
    suggested_label: str
    suggested_bee_attr: str | None = None
    confidence: float = 0.0
    source_cluster: SurfaceCluster | None = None


async def get_pending_unknown_keywords(
    pool: asyncpg.Pool,
    limit: int = 100,
) -> list[dict]:
    """Fetch PENDING unknown keyword surfaces from quarantine."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, surface_text, bee_attr_raw, context_text, review_id
            FROM quarantine_unknown_keyword
            WHERE status = 'PENDING'
            ORDER BY created_at DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


def cluster_surfaces(surfaces: list[dict], threshold: float = 0.7) -> list[SurfaceCluster]:
    """Cluster similar surface forms using sequence matching.

    Simple single-pass greedy clustering.
    """
    clusters: list[SurfaceCluster] = []
    used = set()

    for i, s in enumerate(surfaces):
        if i in used:
            continue
        text = normalize_text(s.get("surface_text", ""))
        cluster = SurfaceCluster(representative=text, members=[text], count=1)
        if s.get("bee_attr_raw"):
            cluster.bee_attrs.add(s["bee_attr_raw"])
        used.add(i)

        for j in range(i + 1, len(surfaces)):
            if j in used:
                continue
            other = normalize_text(surfaces[j].get("surface_text", ""))
            score = SequenceMatcher(None, text, other).ratio()
            if score >= threshold:
                cluster.members.append(other)
                cluster.count += 1
                if surfaces[j].get("bee_attr_raw"):
                    cluster.bee_attrs.add(surfaces[j]["bee_attr_raw"])
                used.add(j)

        clusters.append(cluster)

    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters


def suggest_candidates(clusters: list[SurfaceCluster]) -> list[ConceptCandidate]:
    """Suggest keyword candidates from clusters."""
    candidates = []
    for cluster in clusters:
        if cluster.count < 2:
            continue  # skip singletons
        keyword_id = f"kw_{normalize_text(cluster.representative).replace(' ', '_')}"
        label = cluster.representative
        bee_attr = next(iter(cluster.bee_attrs), None)
        candidates.append(ConceptCandidate(
            surface=cluster.representative,
            suggested_keyword_id=keyword_id,
            suggested_label=label,
            suggested_bee_attr=bee_attr,
            confidence=min(cluster.count / 10.0, 1.0),
            source_cluster=cluster,
        ))
    return candidates


async def approve_candidate(
    pool: asyncpg.Pool,
    candidate: ConceptCandidate,
    dictionary_version: str = "v2",
) -> None:
    """Approve a candidate: add to concept_registry + mark quarantine entries RESOLVED."""
    from src.common.ids import make_concept_iri

    concept_id = make_concept_iri("Keyword", candidate.suggested_keyword_id)

    async with pool.acquire() as conn:
        # Add to concept_registry
        await conn.execute("""
            INSERT INTO concept_registry (concept_id, concept_type, canonical_name,
                canonical_name_norm, source_system, source_key)
            VALUES ($1, 'Keyword', $2, $3, 'dictionary_growth', $4)
            ON CONFLICT (concept_id) DO NOTHING
        """,
            concept_id, candidate.suggested_label,
            normalize_text(candidate.suggested_label), dictionary_version,
        )

        # Add aliases for all cluster members
        if candidate.source_cluster:
            for member in candidate.source_cluster.members:
                await conn.execute("""
                    INSERT INTO concept_alias (concept_id, alias_text, alias_norm, source)
                    VALUES ($1, $2, $3, 'dictionary_growth')
                """, concept_id, member, normalize_text(member))

        # Mark quarantine entries as RESOLVED
        for member in (candidate.source_cluster.members if candidate.source_cluster else [candidate.surface]):
            await conn.execute("""
                UPDATE quarantine_unknown_keyword
                SET status = 'RESOLVED', resolved_keyword_id = $1,
                    resolved_concept_id = $2, dictionary_version = $3, resolved_at = now()
                WHERE surface_text = $4 AND status = 'PENDING'
            """,
                candidate.suggested_keyword_id, concept_id, dictionary_version,
                member,
            )
