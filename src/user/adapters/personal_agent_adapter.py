"""
Personal-agent adapter: transforms personal-agent output to GraphRapping canonical facts.

Decouples GraphRapping from personal-agent's internal structure.
Converts SignalBuilder output → canonical_user_fact format.
"""

from __future__ import annotations

from typing import Any

from src.common.config_loader import get_texture_surface_to_keyword, get_texture_axis
from src.common.ids import make_concept_iri, make_product_iri
from src.common.text_normalize import normalize_text
from src.common.enums import ConceptType


def adapt_user_profile(
    user_id: str,
    profile: dict[str, Any],
    purchase_features: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert personal-agent 3-group profile to canonical user fact inputs.

    Args:
        user_id: Real user_id
        profile: Normalized 3-group profile from personal-agent data_store
            {basic: {}, purchase_analysis: {}, chat: {}}
        purchase_features: Optional PurchaseFeatures-derived dict with
            owned_product_ids, repurchased_brand_ids, etc.

    Returns:
        List of dicts ready for canonicalize_user_facts.py
        Each dict: {user_id, predicate, concept_type, concept_value, confidence, source}
    """
    facts: list[dict[str, Any]] = []
    basic = profile.get("basic", {})
    purchase = profile.get("purchase_analysis", {})
    chat = profile.get("chat") or {}

    # P3-2: recency timestamps so recency_factor = exp(-λ·days_elapsed) ≠ 1.0
    # - chat.updated_at drives chat + basic facts (basic is captured together
    #   with chat in personal-agent flow; chat.updated_at is the best proxy)
    # - purchase_features.last_seen_at = max(purchased_at) drives all
    #   purchase_analysis-derived facts (PREFERS_BRAND/CATEGORY,
    #   REPURCHASES_CATEGORY) — same source as event-level purchase facts
    chat_ts = chat.get("updated_at") if isinstance(chat, dict) else None
    basic_ts = chat_ts
    purchase_ts = purchase_features.get("last_seen_at") if purchase_features else None

    # Basic demographics
    if basic.get("skin_type"):
        facts.append(_make_pref("HAS_SKIN_TYPE", ConceptType.SKIN_TYPE, basic["skin_type"],
                                user_id, "basic", last_seen_at=basic_ts))
    if basic.get("skin_tone"):
        facts.append(_make_pref("HAS_SKIN_TONE", ConceptType.SKIN_TONE, basic["skin_tone"],
                                user_id, "basic", last_seen_at=basic_ts))
    if basic.get("skin_concerns"):
        from src.common.concept_resolver import resolve_concern_id
        for concern in _as_list(basic.get("skin_concerns")):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, resolve_concern_id(concern),
                                    user_id, "basic", last_seen_at=basic_ts))

    # Purchase-based brand preferences
    for field_name in (
        "preferred_brand",
        "preferred_skincare_brand",
        "preferred_makeup_brand",
        "preferred_bodycare_brand",
        "preferred_hair_brand",
        "preferred_perfume_brand",
    ):
        for brand in _as_list(purchase.get(field_name)):
            facts.append(_make_pref("PREFERS_BRAND", ConceptType.BRAND, brand,
                                    user_id, "purchase", last_seen_at=purchase_ts))

    # Purchase-based category preferences
    for cat in _as_list(purchase.get("active_product_category")):
        facts.append(_make_pref("PREFERS_CATEGORY", ConceptType.CATEGORY, cat,
                                user_id, "purchase", last_seen_at=purchase_ts))

    # Repurchase categories (Fix C: split from REPURCHASES_PRODUCT_OR_FAMILY)
    for cat in _as_list(purchase.get("preferred_repurchase_category")):
        facts.append(_make_pref("REPURCHASES_CATEGORY", ConceptType.CATEGORY, cat,
                                user_id, "purchase", last_seen_at=purchase_ts))

    # Chat-based preferences
    if chat:
        # Ingredients
        ingredients = chat.get("ingredients", {})
        for ing in ingredients.get("preferred", []):
            facts.append(_make_pref("PREFERS_INGREDIENT", ConceptType.INGREDIENT, ing,
                                    user_id, "chat", last_seen_at=chat_ts))
        for ing in ingredients.get("avoid", []):
            facts.append(_make_pref("AVOIDS_INGREDIENT", ConceptType.INGREDIENT, ing,
                                    user_id, "chat", last_seen_at=chat_ts))
        for ing in ingredients.get("allergy", []):
            facts.append(_make_pref("AVOIDS_INGREDIENT", ConceptType.INGREDIENT, ing,
                                    user_id, "chat", confidence=1.0, last_seen_at=chat_ts))

        # Face profile (concern/goal → canonical IDs via resolver)
        from src.common.concept_resolver import resolve_concern_id, resolve_goal_id
        face = chat.get("face", {})
        for concern in _as_list(face.get("skin_concerns")):
            canonical = resolve_concern_id(concern)
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, canonical,
                                    user_id, "chat", last_seen_at=chat_ts))
        for goal in _as_list(face.get("skincare_goals")):
            canonical = resolve_goal_id(goal)
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, canonical,
                                    user_id, "chat", last_seen_at=chat_ts))

        # Fix B: texture → axis-level BEE_ATTR + specific KEYWORD
        _append_texture_preferences(facts, user_id, _as_list(face.get("preferred_texture")), chat_ts)

        # Hair profile (concern/goal → canonical IDs via resolver)
        hair = chat.get("hair", {})
        for concern in _as_list(hair.get("hair_concerns")):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, resolve_concern_id(concern),
                                    user_id, "chat", last_seen_at=chat_ts))
        for goal in _as_list(hair.get("haircare_goals")):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, resolve_goal_id(goal),
                                    user_id, "chat", last_seen_at=chat_ts))
        _append_texture_preferences(facts, user_id, _as_list(hair.get("preferred_texture")), chat_ts)

        # Body / scalp / makeup profiles from personal-agent's richer chat shape.
        body = chat.get("body", {})
        for concern in _as_list(body.get("body_concerns")):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, resolve_concern_id(concern),
                                    user_id, "chat", last_seen_at=chat_ts))
        for goal in _as_list(body.get("bodycare_goals")):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, resolve_goal_id(goal),
                                    user_id, "chat", last_seen_at=chat_ts))
        _append_texture_preferences(facts, user_id, _as_list(body.get("preferred_texture")), chat_ts)

        scalp = chat.get("scalp", {})
        for concern in _as_list(scalp.get("scalp_concerns")):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, resolve_concern_id(concern),
                                    user_id, "chat", last_seen_at=chat_ts))
        for goal in _as_list(scalp.get("scalpcare_goals")):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, resolve_goal_id(goal),
                                    user_id, "chat", last_seen_at=chat_ts))

        makeup = chat.get("makeup", {})
        for concern in _as_list(makeup.get("makeup_concerns")):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, resolve_concern_id(concern),
                                    user_id, "chat", last_seen_at=chat_ts))
        for goal in _as_list(makeup.get("makeup_goals")):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, resolve_goal_id(goal),
                                    user_id, "chat", last_seen_at=chat_ts))
        _append_texture_preferences(facts, user_id, _as_list(makeup.get("preferred_texture")), chat_ts)

        # Scent preferences
        scent = chat.get("scent", {})
        scent_values = _as_list(scent.get("preferences")) + _as_list(scent.get("preferred_scent"))
        for pref in scent_values:
            facts.append(_make_pref("PREFERS_KEYWORD", ConceptType.KEYWORD, pref,
                                    user_id, "chat", last_seen_at=chat_ts))

    # Purchase-derived features (from derive_purchase_features)
    if purchase_features:
        pf_last_seen = purchase_features.get("last_seen_at")
        # Fix A: OWNS_PRODUCT → entity reference, not concept
        for pid in purchase_features.get("owned_product_ids", []):
            facts.append(_make_product_ref("OWNS_PRODUCT", pid, user_id, "purchase", confidence=0.9, last_seen_at=pf_last_seen))
        for fid in purchase_features.get("owned_family_ids", []):
            facts.append(_make_product_ref("OWNS_FAMILY", fid, user_id, "purchase", confidence=0.85, last_seen_at=pf_last_seen))
        for fid in purchase_features.get("repurchased_family_ids", []):
            facts.append(_make_product_ref("REPURCHASES_FAMILY", fid, user_id, "purchase", confidence=0.9, last_seen_at=pf_last_seen))
        # Fix C: REPURCHASES_BRAND instead of REPURCHASES_PRODUCT_OR_FAMILY
        for brand_id in purchase_features.get("repurchased_brand_ids", []):
            facts.append(_make_pref("REPURCHASES_BRAND", ConceptType.BRAND, brand_id, user_id, "purchase", confidence=0.9, last_seen_at=pf_last_seen))
        for brand_id in purchase_features.get("recently_purchased_brand_ids", []):
            facts.append(_make_pref("RECENTLY_PURCHASED", ConceptType.BRAND, brand_id, user_id, "purchase", confidence=0.7, last_seen_at=pf_last_seen))

    return facts


def _make_pref(
    predicate: str,
    concept_type: ConceptType,
    value: str,
    user_id: str,
    source: str,
    confidence: float = 0.8,
    last_seen_at: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "user_id": user_id,
        "predicate": predicate,
        "concept_type": concept_type.value,
        "concept_value": value,
        "concept_id": make_concept_iri(concept_type.value, normalize_text(value)),
        "confidence": confidence,
        "source": source,
    }
    result["last_seen_at"] = last_seen_at
    return result


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _append_texture_preferences(
    facts: list[dict[str, Any]],
    user_id: str,
    textures: list[str],
    last_seen_at: str | None,
) -> None:
    if not textures:
        return
    facts.append(_make_pref("PREFERS_BEE_ATTR", ConceptType.BEE_ATTR, get_texture_axis(),
                            user_id, "chat", last_seen_at=last_seen_at))
    texture_map = get_texture_surface_to_keyword()
    for texture in textures:
        keyword = texture_map.get(texture.replace(" ", ""), texture)
        facts.append(_make_pref("PREFERS_KEYWORD", ConceptType.KEYWORD, keyword,
                                user_id, "chat", last_seen_at=last_seen_at))


def _make_product_ref(
    predicate: str,
    product_id: str,
    user_id: str,
    source: str,
    confidence: float = 0.8,
    last_seen_at: str | None = None,
) -> dict[str, Any]:
    """Create a fact dict referencing a product entity (not a concept)."""
    result = {
        "user_id": user_id,
        "predicate": predicate,
        "object_ref_kind": "ENTITY",
        "concept_type": "Product",
        "concept_value": product_id,
        "concept_id": make_product_iri(product_id),
        "confidence": confidence,
        "source": source,
    }
    result["last_seen_at"] = last_seen_at
    return result
