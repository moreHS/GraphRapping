"""Semantic compatibility matching for recommendation evidence.

This module is recommendation-only. It does not create graph facts and it does
not broaden generic axes such as formulation/texture without a compatible
value and polarity from ``configs/recommendation_semantic_compatibility.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.rec.category_groups import classify_product_category_group
from src.rec.scoped_preferences import collect_preference_ids


PROMOTED_EVIDENCE_FIELDS = {
    "keyword": ("top_keyword_ids",),
    "bee_attr": ("top_bee_attr_ids",),
}

WEAK_EVIDENCE_FIELDS = {
    "keyword": (
        "weak_keyword_ids",
        "longtail_keyword_ids",
        "debug_keyword_ids",
        "candidate_keyword_ids",
    ),
    "bee_attr": (
        "weak_bee_attr_ids",
        "longtail_bee_attr_ids",
        "debug_bee_attr_ids",
        "candidate_bee_attr_ids",
    ),
}


@dataclass(frozen=True)
class SemanticCompatibilityMatch:
    """A user value/polarity preference matched to product-side evidence."""

    axis: str
    value: str
    polarity: str
    user_id: str
    product_id: str
    product_key: str
    product_type: str
    strength: float
    weak: bool = False

    @property
    def overlap_type(self) -> str:
        if self.weak:
            return f"weak_semantic_{self.product_type}"
        return f"semantic_{self.product_type}"

    def to_overlap_concept(self) -> str:
        strength = _bounded_strength(self.strength)
        return f"{self.overlap_type}:{self.axis}:{self.value}:{self.product_id}|strength={strength:.4f}"


def find_semantic_matches(
    user_profile: dict[str, Any],
    product_profile: dict[str, Any],
) -> list[SemanticCompatibilityMatch]:
    """Return value-and-polarity compatible user/product evidence matches.

    Promoted product fields produce ``semantic_*`` overlaps. Optional long-tail
    fields produce ``weak_semantic_*`` overlaps so they can be scored and
    explained separately from promoted review graph relation evidence.
    """

    user_ids = _collect_user_preference_ids(user_profile, product_profile)
    if not user_ids:
        return []

    promoted_evidence = _collect_product_evidence(product_profile, PROMOTED_EVIDENCE_FIELDS, weak=False)
    weak_evidence = _collect_product_evidence(product_profile, WEAK_EVIDENCE_FIELDS, weak=True)
    product_evidence = promoted_evidence + weak_evidence
    if not product_evidence:
        return []

    matches: list[SemanticCompatibilityMatch] = []
    seen: set[tuple[str, str, str, bool]] = set()

    for rule in _load_rules():
        triggered_user_ids = sorted(user_ids & _rule_user_keys(rule))
        if not triggered_user_ids:
            continue
        rule_polarity = str(rule.get("polarity") or "positive")
        if rule_polarity not in {"positive", "negative", "avoid"}:
            continue
        if _has_blocked_product_evidence(rule, product_evidence):
            continue

        allowed_by_type = _rule_match_entries(rule)
        if not allowed_by_type:
            continue

        for evidence in product_evidence:
            if not _polarity_compatible(rule_polarity, evidence["polarity"]):
                continue
            match_entry = allowed_by_type.get(evidence["type"], {}).get(evidence["key"])
            if not match_entry:
                continue
            key = (
                str(rule.get("axis") or ""),
                str(rule.get("value") or ""),
                evidence["id"],
                bool(evidence["weak"]),
            )
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                SemanticCompatibilityMatch(
                    axis=str(rule.get("axis") or ""),
                    value=str(rule.get("value") or ""),
                    polarity=rule_polarity,
                    user_id=triggered_user_ids[0],
                    product_id=evidence["id"],
                    product_key=evidence["key"],
                    product_type=evidence["type"],
                    strength=float(match_entry.get("strength") or 1.0),
                    weak=bool(evidence["weak"]),
                )
            )

    return matches


def normalize_signal_id(value: Any) -> str:
    """Normalize concept/raw IDs onto the same comparison key."""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.startswith("concept:"):
        parts = raw.split(":")
        raw = parts[-1]
    elif raw.startswith("product:"):
        raw = raw[len("product:"):]
    return normalize_text(raw)


@lru_cache(maxsize=1)
def _load_rules() -> tuple[dict[str, Any], ...]:
    config = load_yaml("recommendation_semantic_compatibility.yaml") or {}
    rules = config.get("rules") or []
    return tuple(rule for rule in rules if isinstance(rule, dict))


def _collect_user_preference_ids(
    user_profile: dict[str, Any],
    product_profile: dict[str, Any],
) -> set[str]:
    product_group = classify_product_category_group(product_profile)
    ids: set[str] = set()
    for legacy_field, edge_type in (
        ("preferred_keyword_ids", "PREFERS_KEYWORD"),
        ("preferred_bee_attr_ids", "PREFERS_BEE_ATTR"),
        ("goal_ids", "WANTS_GOAL"),
    ):
        ids.update(collect_preference_ids(user_profile, legacy_field, edge_type, product_group))
    return {normalize_signal_id(value) for value in ids if normalize_signal_id(value)}


def _collect_product_evidence(
    product_profile: dict[str, Any],
    fields_by_type: dict[str, tuple[str, ...]],
    *,
    weak: bool,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for evidence_type, fields in fields_by_type.items():
        for field in fields:
            for item in _iter_signal_items(product_profile.get(field) or []):
                evidence_id = str(item.get("id") or "").strip()
                key = normalize_signal_id(evidence_id)
                if not key:
                    continue
                evidence.append(
                    {
                        "id": evidence_id,
                        "key": key,
                        "type": evidence_type,
                        "polarity": _normalize_polarity(item.get("polarity")),
                        "weak": weak,
                    }
                )
    return evidence


def _rule_user_keys(rule: dict[str, Any]) -> set[str]:
    signals = rule.get("user_signals") or {}
    keys: set[str] = set()
    for group in ("keywords", "bee_attrs", "ids"):
        keys.update(normalize_signal_id(value) for value in signals.get(group) or [])
    return {key for key in keys if key}


def _rule_match_entries(rule: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    matches = rule.get("matches") or {}
    result: dict[str, dict[str, dict[str, Any]]] = {"keyword": {}, "bee_attr": {}}
    for evidence_type, yaml_key in (("keyword", "keywords"), ("bee_attr", "bee_attrs")):
        for entry in matches.get(yaml_key) or []:
            if isinstance(entry, dict):
                evidence_id = entry.get("id")
                strength = entry.get("strength", 1.0)
            else:
                evidence_id = entry
                strength = 1.0
            key = normalize_signal_id(evidence_id)
            if not key:
                continue
            result[evidence_type][key] = {"strength": _bounded_strength(strength)}
    return result


def _has_blocked_product_evidence(rule: dict[str, Any], product_evidence: list[dict[str, Any]]) -> bool:
    blocks = rule.get("blocks") or {}
    blocked: dict[str, set[str]] = {"keyword": set(), "bee_attr": set()}
    for evidence_type, yaml_key in (("keyword", "keywords"), ("bee_attr", "bee_attrs")):
        blocked[evidence_type].update(
            normalize_signal_id(value) for value in blocks.get(yaml_key) or []
        )
    blocked = {key: {value for value in values if value} for key, values in blocked.items()}
    if not blocked["keyword"] and not blocked["bee_attr"]:
        return False
    return any(evidence["key"] in blocked.get(evidence["type"], set()) for evidence in product_evidence)


def _polarity_compatible(user_polarity: str, product_polarity: str) -> bool:
    user_polarity = _normalize_polarity(user_polarity)
    product_polarity = _normalize_polarity(product_polarity)
    if user_polarity == "positive":
        return product_polarity == "positive"
    if user_polarity in {"negative", "avoid"}:
        return product_polarity in {"negative", "avoid"}
    return False


def _normalize_polarity(value: Any) -> str:
    polarity = normalize_text(str(value or "positive"))
    if polarity in {"pos", "+", "positive"}:
        return "positive"
    if polarity in {"neg", "-", "negative"}:
        return "negative"
    if polarity in {"avoid", "blocked", "block"}:
        return "avoid"
    return polarity


def _bounded_strength(value: Any) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(strength, 1.0))


def _iter_signal_items(items: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for item in items:
        if isinstance(item, dict):
            yield item
        elif item:
            yield {"id": str(item)}
