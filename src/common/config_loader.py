"""
Config loader for YAML, CSV, and JSON config files.
"""

from __future__ import annotations

import csv
import json
import os
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


# ---------------------------------------------------------------------------
# Predicate contracts (P0-2 audit fix)
# ---------------------------------------------------------------------------

_predicate_contracts: dict[str, dict[str, str]] | None = None


def load_predicate_contracts() -> dict[str, dict[str, str]]:
    """Load predicate_contracts.csv as {predicate: row} dict.

    Cached at module level following other config loaders. Consumed by
    CanonicalFactBuilder to validate (predicate, subject_type, object_type)
    triples against configs/predicate_contracts.csv.

    Fails closed on:
      - Missing required header columns (predicate / allowed_subject_types /
        allowed_object_types). Without these the builder validation would
        silently skip — caught early with a clear ValueError.
      - Duplicate predicate keys (second row would silently shadow the first).
    """
    global _predicate_contracts
    if _predicate_contracts is None:
        rows = load_csv("predicate_contracts.csv")
        if not rows:
            raise ValueError(
                "predicate_contracts.csv is empty or header-only. "
                "Refusing to load — would silently disable all validation."
            )

        required = {"predicate", "allowed_subject_types", "allowed_object_types"}
        missing = required - set(rows[0].keys())
        if missing:
            raise ValueError(
                f"predicate_contracts.csv is missing required columns: {sorted(missing)}. "
                f"Validation would silently disable for these axes — refusing to load."
            )

        # Blank type cells are valid only for predicates that are intentionally
        # not stored as edges (preprocess-only / drop). Any other blank is a
        # fail-open hole — refuse to load.
        from src.normalize.relation_canonicalizer import (
            DROP_PREDICATES,
            PREPROCESS_ONLY,
        )
        allow_blank_predicates = PREPROCESS_ONLY | DROP_PREDICATES

        contracts: dict[str, dict[str, str]] = {}
        for row_idx, row in enumerate(rows, start=2):  # +2: 1-based + header
            pred = (row.get("predicate") or "").strip()
            if not pred:
                raise ValueError(
                    f"predicate_contracts.csv row {row_idx} has blank predicate. "
                    f"Refusing to load — ambiguous contract."
                )
            allowed_subj = (row.get("allowed_subject_types") or "").strip()
            allowed_obj = (row.get("allowed_object_types") or "").strip()
            if (not allowed_subj or not allowed_obj) and pred not in allow_blank_predicates:
                raise ValueError(
                    f"predicate_contracts.csv row {row_idx} (predicate='{pred}') has "
                    f"blank allowed_subject_types or allowed_object_types. "
                    f"Validation would silently skip for that axis — refusing to load. "
                    f"(Blank is permitted only for preprocess-only/drop predicates: "
                    f"{sorted(allow_blank_predicates)})"
                )
            if pred in contracts:
                raise ValueError(
                    f"predicate_contracts.csv has duplicate predicate '{pred}'. "
                    f"Second row would silently shadow the first — refusing to load."
                )
            contracts[pred] = row
        _predicate_contracts = contracts
    return _predicate_contracts


# ---------------------------------------------------------------------------
# kg_mode resolver (P0-3 audit fix)
# ---------------------------------------------------------------------------

_ALLOWED_KG_MODES = ("off", "shadow", "on")


def get_kg_mode(arg: str | None = None, *, default: str = "off") -> str:
    """Resolve kg_mode using arg → GRAPHRAPPING_KG_MODE env → caller-specific default.

    Allowed values: "off" | "shadow" | "on".

    Fails closed on:
      - Invalid value (typos like "On" / "true") — explicit ValueError.
      - Explicit empty env ("") — must not silently fall back; treated as invalid.
    """
    env = os.environ.get("GRAPHRAPPING_KG_MODE")
    if arg is not None:
        value = arg
        source = "arg"
    elif env is not None:
        value = env
        source = "env"
    else:
        value = default
        source = "default"
    if value not in _ALLOWED_KG_MODES:
        raise ValueError(
            f"Invalid kg_mode {value!r}. Allowed: {_ALLOWED_KG_MODES}. (Source: {source})"
        )
    return value
