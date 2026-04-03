"""
personal-agent PostgreSQL → UserLoadResult loader.

Reads user profiles from personal-agent's normalized 3-group structure
and converts to GraphRapping's user_masters + user_adapted_facts.

Includes preflight gate: verify_user_id_stability() checks encrypted user_id consistency.
MVP: purchase events and repurchase/seasonal summaries are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.user.adapters.personal_agent_adapter import adapt_user_profile


@dataclass
class UserLoadResult:
    """All user-side artifacts needed by run_batch()."""
    user_masters: dict[str, dict] = field(default_factory=dict)
    user_adapted_facts: dict[str, list[dict]] = field(default_factory=dict)
    user_count: int = 0
    stability_verified: bool = False


def load_users_from_profiles(
    user_profiles: dict[str, dict[str, Any]],
) -> UserLoadResult:
    """Convert normalized user profiles to GraphRapping user artifacts.

    Args:
        user_profiles: dict of user_id → normalized 3-group profile
            {
                "basic": {"gender": ..., "age": ..., "skin_type": ..., "skin_concerns": [...]},
                "purchase_analysis": {"preferred_skincare_brand": [...], ...},
                "chat": {"face": {...}, "ingredients": {...}, ...} or None
            }

    Returns:
        UserLoadResult with user_masters and user_adapted_facts ready for run_batch()
    """
    result = UserLoadResult()

    for user_id, profile in user_profiles.items():
        # Validate normalized 3-group format
        if "user_profile" in profile or "skin_profile" in profile:
            raise ValueError(
                f"User '{user_id}' appears to be in raw 7-column format. "
                f"Use normalized 3-group format (basic/purchase_analysis/chat) instead. "
                f"See mockdata/README.md for details."
            )
        if "basic" not in profile:
            raise ValueError(
                f"User '{user_id}' missing required 'basic' key. "
                f"Expected normalized 3-group format: {{basic, purchase_analysis, chat}}."
            )
        basic = profile.get("basic", {})

        # Build user_master row
        result.user_masters[user_id] = {
            "user_id": user_id,
            "age": None,
            "age_band": basic.get("age"),
            "gender": basic.get("gender"),
            "skin_type": basic.get("skin_type"),
            "skin_tone": basic.get("skin_tone"),
        }

        # Convert profile → adapted facts via personal_agent_adapter
        adapted_facts = adapt_user_profile(user_id, profile)
        result.user_adapted_facts[user_id] = adapted_facts

    result.user_count = len(result.user_masters)
    return result


async def verify_user_id_stability(
    pool,
    query: str,
    sample_size: int = 5,
) -> bool:
    """Preflight gate: verify encrypted user_id is stable across queries.

    Queries the personal-agent DB twice for the same users and checks
    if encrypted user_ids match. If any mismatch, returns False.

    Args:
        pool: asyncpg connection pool to personal-agent DB
        query: SQL to fetch user_id list (e.g., "SELECT user_id FROM agent.aibe_user_context_mstr_v LIMIT $1")
        sample_size: number of users to test

    Returns:
        True if all user_ids are stable, False if any mismatch
    """
    async with pool.acquire() as conn:
        # First query
        rows1 = await conn.fetch(query, sample_size)
        ids1 = [r["user_id"] for r in rows1]

        # Second query (same query, should return same IDs)
        rows2 = await conn.fetch(query, sample_size)
        ids2 = [r["user_id"] for r in rows2]

    if len(ids1) != len(ids2):
        return False

    for id1, id2 in zip(sorted(ids1), sorted(ids2)):
        if id1 != id2:
            return False

    return True
