"""
Personal-agent adapter: transforms personal-agent output to GraphRapping canonical facts.

Decouples GraphRapping from personal-agent's internal structure.
Converts SignalBuilder output → canonical_user_fact format.
"""

from __future__ import annotations

from typing import Any

from src.common.ids import make_concept_iri
from src.common.text_normalize import normalize_text
from src.common.enums import ConceptType


def adapt_user_profile(
    user_id: str,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert personal-agent 3-group profile to canonical user fact inputs.

    Args:
        user_id: Real user_id
        profile: Normalized 3-group profile from personal-agent data_store
            {basic: {}, purchase_analysis: {}, chat: {}}

    Returns:
        List of dicts ready for canonicalize_user_facts.py
        Each dict: {user_id, predicate, concept_type, concept_value, confidence, source}
    """
    facts: list[dict[str, Any]] = []
    basic = profile.get("basic", {})
    purchase = profile.get("purchase_analysis", {})
    chat = profile.get("chat") or {}

    # Basic demographics
    if basic.get("skin_type"):
        facts.append(_make_pref("HAS_SKIN_TYPE", ConceptType.SKIN_TYPE, basic["skin_type"], user_id, "basic"))
    if basic.get("skin_tone"):
        facts.append(_make_pref("HAS_SKIN_TONE", ConceptType.SKIN_TONE, basic["skin_tone"], user_id, "basic"))

    # Purchase-based brand preferences
    for brand in purchase.get("preferred_skincare_brand", []):
        facts.append(_make_pref("PREFERS_BRAND", ConceptType.BRAND, brand, user_id, "purchase"))
    for brand in purchase.get("preferred_makeup_brand", []):
        facts.append(_make_pref("PREFERS_BRAND", ConceptType.BRAND, brand, user_id, "purchase"))

    # Purchase-based category preferences
    for cat in purchase.get("active_product_category", []):
        facts.append(_make_pref("PREFERS_CATEGORY", ConceptType.CATEGORY, cat, user_id, "purchase"))

    # Repurchase categories
    for cat in purchase.get("preferred_repurchase_category", []):
        facts.append(_make_pref("REPURCHASES_PRODUCT_OR_FAMILY", ConceptType.CATEGORY, cat, user_id, "purchase"))

    # Chat-based preferences
    if chat:
        # Ingredients
        ingredients = chat.get("ingredients", {})
        for ing in ingredients.get("preferred", []):
            facts.append(_make_pref("PREFERS_INGREDIENT", ConceptType.INGREDIENT, ing, user_id, "chat"))
        for ing in ingredients.get("avoid", []):
            facts.append(_make_pref("AVOIDS_INGREDIENT", ConceptType.INGREDIENT, ing, user_id, "chat"))
        for ing in ingredients.get("allergy", []):
            facts.append(_make_pref("AVOIDS_INGREDIENT", ConceptType.INGREDIENT, ing, user_id, "chat", confidence=1.0))

        # Face profile
        face = chat.get("face", {})
        for concern in face.get("skin_concerns", []):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, concern, user_id, "chat"))
        for goal in face.get("skincare_goals", []):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, goal, user_id, "chat"))
        for texture in face.get("preferred_texture", []):
            facts.append(_make_pref("PREFERS_BEE_ATTR", ConceptType.BEE_ATTR, texture, user_id, "chat"))

        # Hair profile
        hair = chat.get("hair", {})
        for concern in hair.get("hair_concerns", []):
            facts.append(_make_pref("HAS_CONCERN", ConceptType.CONCERN, concern, user_id, "chat"))
        for goal in hair.get("haircare_goals", []):
            facts.append(_make_pref("WANTS_GOAL", ConceptType.GOAL, goal, user_id, "chat"))

        # Scent preferences
        scent = chat.get("scent", {})
        for pref in scent.get("preferences", []):
            facts.append(_make_pref("PREFERS_KEYWORD", ConceptType.KEYWORD, pref, user_id, "chat"))

    return facts


def _make_pref(
    predicate: str,
    concept_type: ConceptType,
    value: str,
    user_id: str,
    source: str,
    confidence: float = 0.8,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "predicate": predicate,
        "concept_type": concept_type.value,
        "concept_value": value,
        "concept_id": make_concept_iri(concept_type.value, normalize_text(value)),
        "confidence": confidence,
        "source": source,
    }
