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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.ingest.purchase_ingest import (
    PurchaseEvent,
    derive_purchase_features,
    purchase_features_to_adapter_dict,
)
from src.user.adapters.personal_agent_adapter import adapt_user_profile
from src.user.profile_purchase_summary import (
    derive_purchase_summary_features,
    merge_purchase_feature_dicts,
)

logger = logging.getLogger(__name__)


@dataclass
class UserLoadResult:
    """All user-side artifacts needed by run_batch()."""
    user_masters: dict[str, dict] = field(default_factory=dict)
    user_adapted_facts: dict[str, list[dict]] = field(default_factory=dict)
    user_count: int = 0
    stability_verified: bool = False


def extract_purchase_events_from_profiles(
    user_profiles: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[PurchaseEvent]] | None:
    """Build ``purchase_events_by_user`` from an optional per-profile ``purchase_events`` list.

    Purchase-history backfill (fable_doc §C1) embeds resolved purchases directly
    in each normalized profile under a top-level ``purchase_events`` key. Each
    item mirrors the :class:`PurchaseEvent` input contract
    (``purchase_ingest.py``); ``user_id`` is injected from the profile key here
    rather than duplicated in the file. The result feeds the *existing*
    ``load_users_from_profiles(purchase_events_by_user=...)`` →
    ``derive_purchase_features`` path — no new adapter surface is introduced.

    Boundary contract (cross-review P1-10 — no silent correction):
    - entry not a mapping, or ``product_id`` missing/blank → event SKIPPED.
    - ``quantity`` absent → 1 (documented default). Present but not a positive
      integer (bool excluded; 0/negative/non-int rejected) → event SKIPPED —
      quantity feeds repurchase counting, so fabricating a value is worse than
      dropping the event.
    - ``purchased_at``/``channel`` present but not ``str``, or ``price`` present
      but not numeric → that FIELD nulled (event kept — ownership truth is the
      product reference; these fields are auxiliary recency/metadata).
    All skips/nullifications are counted and surfaced via ``logger.warning``.

    Returns ``None`` when no profile carries events, so callers passing the
    result to ``load_demo_data`` / ``run_full_load`` stay byte-identical to the
    prior no-purchase default (the standard fixtures have no ``purchase_events``
    key).
    """
    events_by_user: dict[str, list[PurchaseEvent]] = {}
    skipped_events = 0
    nullified_fields = 0
    for user_id, profile in user_profiles.items():
        if not isinstance(profile, Mapping):
            continue
        raw_events = profile.get("purchase_events")
        if not isinstance(raw_events, list):
            continue
        events: list[PurchaseEvent] = []
        for idx, ev in enumerate(raw_events):
            if not isinstance(ev, Mapping):
                skipped_events += 1
                continue
            product_id_raw = ev.get("product_id")
            if product_id_raw is None or str(product_id_raw).strip() == "":
                skipped_events += 1
                continue
            product_id = str(product_id_raw).strip()

            quantity_raw = ev.get("quantity")
            if quantity_raw is None:
                quantity = 1
            elif isinstance(quantity_raw, bool) or not isinstance(quantity_raw, int) or quantity_raw <= 0:
                skipped_events += 1
                continue
            else:
                quantity = quantity_raw

            purchased_at = ev.get("purchased_at")
            if purchased_at is not None and not isinstance(purchased_at, str):
                purchased_at = None
                nullified_fields += 1
            price = ev.get("price")
            if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))):
                price = None
                nullified_fields += 1
            channel = ev.get("channel")
            if channel is not None and not isinstance(channel, str):
                channel = None
                nullified_fields += 1

            events.append(
                PurchaseEvent(
                    purchase_event_id=str(
                        ev.get("purchase_event_id") or f"{user_id}::{product_id}::{idx}"
                    ),
                    user_id=user_id,
                    product_id=product_id,
                    purchased_at=purchased_at,
                    price=float(price) if price is not None else None,
                    quantity=quantity,
                    channel=channel,
                )
            )
        if events:
            events_by_user[user_id] = events
    if skipped_events or nullified_fields:
        logger.warning(
            "purchase_events hygiene: skipped %d malformed event(s), nullified %d "
            "ill-typed field(s) across embedded profiles",
            skipped_events,
            nullified_fields,
        )
    return events_by_user or None


def load_users_from_profiles(
    user_profiles: dict[str, dict[str, Any]],
    *,
    purchase_events_by_user: dict[str, list[PurchaseEvent]] | None = None,
    brand_lookup: dict[str, str] | None = None,
    category_lookup: dict[str, str] | None = None,
    family_lookup: dict[str, str] | None = None,
    product_masters: dict[str, dict[str, Any]] | None = None,
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
            user facts via derive_purchase_features(). When omitted (None), a
            centralized fallback auto-extracts any profile-embedded
            ``purchase_events`` (purchase-history backfill) — standard fixtures
            have no such key, so the fallback returns None and the default path
            stays byte-identical. NOTE: this fallback covers the user-fact build
            only; run_batch's brand-confidence weighting is a separate contract
            and still requires callers to pass events explicitly.
        brand_lookup: product_id → brand_id (raw normalized).
        category_lookup: product_id → category_id (raw normalized).
        family_lookup: product_id → variant_family_id.
        product_masters: optional product master map used to resolve
            personal-agent purchase summary products by exact id/name.

    Returns:
        UserLoadResult with user_masters and user_adapted_facts ready for run_batch()
    """
    result = UserLoadResult()

    # Centralized backfill fallback (cross-review P1-9): profiles may embed
    # purchase_events; when the caller didn't supply purchase_events_by_user,
    # extract them here so every entry point (run_full_load / load_demo_data /
    # direct callers) consumes them through the same derive_purchase_features
    # path without per-entry-point wiring.
    if purchase_events_by_user is None:
        purchase_events_by_user = extract_purchase_events_from_profiles(user_profiles)

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
        summary_features_dict = derive_purchase_summary_features(profile, product_masters)
        purchase_features_dict = merge_purchase_feature_dicts(
            purchase_features_dict,
            summary_features_dict,
        )

        # Convert profile → adapted facts via personal_agent_adapter
        adapted_facts = adapt_user_profile(
            user_id, profile, purchase_features=purchase_features_dict
        )
        result.user_adapted_facts[user_id] = adapted_facts

    result.user_count = len(result.user_masters)
    return result
