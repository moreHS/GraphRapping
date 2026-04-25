"""
Config loader for YAML, CSV, and JSON config files.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, cast

import yaml


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


def load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return cast(dict[str, Any], data)


def load_json(filename: str) -> Any:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(filename: str) -> list[dict[str, str]]:
    path = CONFIGS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # Strict: reject rows with more columns than header
    for i, row in enumerate(rows):
        if None in row:
            raise ValueError(
                f"CSV '{filename}' row {i + 2} has more columns than header"
            )
    return rows


# ---------------------------------------------------------------------------
# Texture taxonomy shared loader
# ---------------------------------------------------------------------------

_texture_taxonomy: dict[str, Any] | None = None


def load_texture_taxonomy() -> dict[str, Any]:
    """Load texture taxonomy from authoritative source (texture_keyword_map.yaml).

    Both user adapter and review normalizer should use this function
    to ensure they reference the same taxonomy version.
    """
    global _texture_taxonomy
    if _texture_taxonomy is None:
        _texture_taxonomy = load_yaml("texture_keyword_map.yaml")
    return _texture_taxonomy


def get_texture_surface_to_keyword() -> dict[str, str]:
    """Get surface -> canonical keyword mapping from texture taxonomy."""
    return cast(dict[str, str], load_texture_taxonomy().get("surface_to_keyword", {}))


def get_texture_axis() -> str:
    """Get the texture BEE_ATTR axis name."""
    return str(load_texture_taxonomy().get("texture_axis", "Texture"))


# ---------------------------------------------------------------------------
# Concern / Goal / Bridge loaders
# ---------------------------------------------------------------------------

_concern_dict: dict[str, Any] | None = None
_goal_alias_map: dict[str, Any] | None = None
_concern_bee_attr_map: dict[str, Any] | None = None


def load_concern_dict() -> dict[str, Any]:
    """Load concern dictionary (surface form → concept_id)."""
    global _concern_dict
    if _concern_dict is None:
        _concern_dict = load_yaml("concern_dict.yaml")
    return _concern_dict


def load_goal_alias_map() -> dict[str, Any]:
    """Load goal alias map (alias → canonical goal)."""
    global _goal_alias_map
    if _goal_alias_map is None:
        _goal_alias_map = load_yaml("goal_alias_map.yaml")
    return _goal_alias_map


def load_concern_bee_attr_map() -> dict[str, Any]:
    """Load BEE_ATTR → Concern bridge mapping."""
    global _concern_bee_attr_map
    if _concern_bee_attr_map is None:
        _concern_bee_attr_map = load_yaml("concern_bee_attr_map.yaml")
    return _concern_bee_attr_map
