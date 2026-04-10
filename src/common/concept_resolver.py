"""
Concept Resolver: normalizes Concern and Goal IDs to canonical stable keys.

Bridges the gap between:
- User-side IDs: concept:Concern:건조함, concept:Goal:보습강화
- Product-side IDs: concern_dryness, concept:Goal:보습
- Review-side IDs: concern_dryness (from concern_dict)

After resolution:
- All concern references use concern_dict stable keys (concern_dryness, concern_oiliness, etc.)
- All goal references use canonical goal tokens from goal_alias_map (보습, 톤업, etc.)
"""

from __future__ import annotations

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text


# ---------------------------------------------------------------------------
# Concern resolver
# ---------------------------------------------------------------------------

_concern_dict: dict | None = None


def _get_concern_dict() -> dict:
    global _concern_dict
    if _concern_dict is None:
        _concern_dict = load_yaml("concern_dict.yaml")
    return _concern_dict


def resolve_concern_id(value: str) -> str:
    """Normalize any concern reference to a stable concern_dict key.

    Handles: 'concept:Concern:건조함' → 'concern_dryness'
             'concern_dryness' → 'concern_dryness' (passthrough)
             '건조함' → 'concern_dryness'
    """
    if not value:
        return value

    # Strip IRI prefix
    raw = value
    if raw.startswith("concept:Concern:"):
        raw = raw[len("concept:Concern:"):]

    # Already a stable concern_* key?
    if raw.startswith("concern_"):
        return raw

    # Lookup in concern_dict (surface form → concept_id)
    concern_dict = _get_concern_dict()
    norm = normalize_text(raw)
    entry = concern_dict.get(norm) or concern_dict.get(raw)
    if entry and isinstance(entry, dict):
        return entry.get("concept_id", norm)

    # Fallback: normalized text
    return norm


def concern_label(concern_id: str) -> str:
    """Get Korean label for a concern ID."""
    concern_dict = _get_concern_dict()
    for entry in concern_dict.values():
        if isinstance(entry, dict) and entry.get("concept_id") == concern_id:
            return entry.get("label_ko", concern_id)
    return concern_id


# ---------------------------------------------------------------------------
# Goal resolver
# ---------------------------------------------------------------------------

_goal_alias_map: dict | None = None


def _get_goal_alias_map() -> dict:
    global _goal_alias_map
    if _goal_alias_map is None:
        _goal_alias_map = load_yaml("goal_alias_map.yaml")
    return _goal_alias_map


def resolve_goal_id(value: str) -> str:
    """Normalize any goal reference to a canonical goal token.

    Handles: 'concept:Goal:보습강화' → '보습'
             'concept:Goal:보습' → '보습' (passthrough)
             '보습강화' → '보습'
    """
    if not value:
        return value

    # Strip IRI prefix
    raw = value
    if raw.startswith("concept:Goal:"):
        raw = raw[len("concept:Goal:"):]

    # Lookup in goal_alias_map
    alias_map = _get_goal_alias_map()
    norm = normalize_text(raw)
    canonical = alias_map.get(norm) or alias_map.get(raw)
    if canonical:
        return canonical

    # Fallback: normalized text
    return norm
