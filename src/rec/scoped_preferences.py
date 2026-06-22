"""Scoped user preference helpers for recommendation matching."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


GLOBAL_SCOPES = {None, "", "all", "global", "any"}


def has_scoped_preferences(user_profile: dict[str, Any]) -> bool:
    values = user_profile.get("scoped_preference_ids") or []
    return isinstance(values, list) and bool(values)


def scope_allows(scope_group: Any, product_group: str | None) -> bool:
    if scope_group in GLOBAL_SCOPES:
        return True
    scope = str(scope_group).strip()
    if not scope:
        return True
    if not product_group:
        return True
    return scope == product_group


def collect_preference_ids(
    user_profile: dict[str, Any],
    legacy_field: str,
    edge_type: str,
    product_group: str | None,
) -> set[str]:
    """Collect ids for an edge type, honoring scope when serving data has it."""
    if not has_scoped_preferences(user_profile):
        return _extract_ids(user_profile.get(legacy_field) or [])

    ids: set[str] = set()
    for item in iter_scoped_preferences(user_profile, edge_type=edge_type, product_group=product_group):
        item_id = item.get("id")
        if item_id:
            ids.add(str(item_id))
    return ids


def iter_scoped_preferences(
    user_profile: dict[str, Any],
    *,
    edge_type: str | None = None,
    edge_types: set[str] | None = None,
    product_group: str | None = None,
) -> Iterable[dict[str, Any]]:
    for item in user_profile.get("scoped_preference_ids") or []:
        if not isinstance(item, dict):
            continue
        item_edge_type = item.get("edge_type")
        if edge_type is not None and item_edge_type != edge_type:
            continue
        if edge_types is not None and item_edge_type not in edge_types:
            continue
        if not scope_allows(item.get("scope_group"), product_group):
            continue
        yield item


def _extract_ids(items: list[Any]) -> set[str]:
    result: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            value = item.get("id")
        else:
            value = item
        if value:
            result.add(str(value))
    return result
