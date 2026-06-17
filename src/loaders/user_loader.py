"""
personal-agent PostgreSQL → UserLoadResult loader.

Reads user profiles from personal-agent's normalized 3-group structure
and converts to GraphRapping's user_masters + user_adapted_facts.

P0-1 (audit fix): purchase events can be passed through to build OWNS_*/REPURCHASES_*/
RECENTLY_PURCHASED user facts. Contract: lookup IDs are raw normalized
(e.g. brand_id="b1"), not concept IRIs.

P1-5 (Wave 3.5): `verify_user_id_stability` removed as dead code — no
operational entry point ever called it, and activation required an async
DB pool/query contract `FullLoadConfig` does not expose. If reintroduced
during personal-agent DB integration, design it against the actual pool
plumbing then, not before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.ingest.purchase_ingest import (
    PurchaseEvent,
    derive_purchase_features,
    purchase_features_to_adapter_dict,
)
from src.user.adapters.personal_agent_adapter import adapt_user_profile

logger = logging.getLogger(__name__)


@dataclass
class UserLoadResult:
    """All user-side artifacts needed by run_batch()."""
    user_masters: dict[str, dict] = field(default_factory=dict)
    user_adapted_facts: dict[str, list[dict]] = field(default_factory=dict)
    user_count: int = 0
    stability_verified: bool = False


def load_users_from_profiles(
    user_profiles: dict[str, dict[str, Any]],
    *,
    purchase_events_by_user: dict[str, list[PurchaseEvent]] | None = None,
    brand_lookup: dict[str, str] | None = None,
    category_lookup: dict[str, str] | None = None,
    family_lookup: dict[str, str] | None = None,
) -> UserLoadResult:
    """Convert normalized user profiles to GraphRapping user artifacts.

    Args:
        user_profiles: dict of user_id → normalized 3-group profile
            {
                "basic": {"gender": ..., "age": ..., "skin_type": ..., "skin_concerns": [...]},
                "purchase_analysis": {"preferred_skincare_brand": [...], ...},
                "chat": {"face": {...}, "ingredients": {...}, ...} or None
            }
        purchase_events_by_user: optional per-user PurchaseEvent list, used to derive
            OWNS_PRODUCT/OWNS_FAMILY/REPURCHASES_FAMILY/REPURCHASES_BRAND/RECENTLY_PURCHASED
            user facts via derive_purchase_features().
        brand_lookup: product_id → brand_id (raw normalized).
        category_lookup: product_id → category_id (raw normalized).
        family_lookup: product_id → variant_family_id.

    Returns:
        UserLoadResult with user_masters and user_adapted_facts ready for run_batch()
    """
    result = UserLoadResult()

    # Surface purchase events for users absent from user_profiles — current loader
    # contract iterates profiles only, so those purchases are silently ignored.
    if purchase_events_by_user:
        unmatched = sorted(set(purchase_events_by_user) - set(user_profiles))
        if unmatched:
            logger.warning(
                "Purchase events provided for %d user(s) absent from user_profiles "
                "and will be ignored: %s",
                len(unmatched),
                unmatched[:10],
            )

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

        # P0-1: derive purchase features when events are supplied and convert
        # to the dict shape adapt_user_profile() consumes.
        purchase_features_dict: dict[str, Any] | None = None
        if purchase_events_by_user and user_id in purchase_events_by_user:
            pf = derive_purchase_features(
                purchase_events_by_user[user_id],
                brand_lookup=brand_lookup,
                category_lookup=category_lookup,
                family_lookup=family_lookup,
            )
            purchase_features_dict = purchase_features_to_adapter_dict(pf)

        # Convert profile → adapted facts via personal_agent_adapter
        adapted_facts = adapt_user_profile(
            user_id, profile, purchase_features=purchase_features_dict
        )
        result.user_adapted_facts[user_id] = adapted_facts

    result.user_count = len(result.user_masters)
    return result
