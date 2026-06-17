"""
P4-4 (Wave 3.4): skin_type → concern map loaded from YAML + normalize_text.

Verifies:
- All 5 canonical skin types loaded (건성/지성/복합성/민감성/수부지)
- English aliases (dry/oily/combination/sensitive/subuji) resolve to same row
- normalize_text input (lowercased/spaced) still matches
- Old hardcoded `_SKIN_TYPE_CONCERN_MAP` dict no longer exists in scorer
"""

from __future__ import annotations

import inspect

import pytest

from src.common.text_normalize import normalize_text
from src.rec import scorer
from src.rec.scorer import (
    _get_skin_type_concern_map,
    _load_skin_type_concern_map,
    _skin_type_fit,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Ensure each test sees a fresh YAML load."""
    scorer._SKIN_TYPE_CONCERN_MAP_CACHE = None


def test_yaml_loads_all_canonical_skin_types() -> None:
    lookup = _load_skin_type_concern_map()
    for kr in ("건성", "지성", "복합성", "민감성", "수부지"):
        key = normalize_text(kr)
        assert key in lookup, f"Korean canonical {kr} (normalized={key!r}) not loaded"


def test_english_aliases_resolve_to_same_row() -> None:
    lookup = _load_skin_type_concern_map()
    kr_key = normalize_text("건성")
    en_key = normalize_text("dry")
    assert kr_key in lookup
    assert en_key in lookup
    assert lookup[kr_key] == lookup[en_key], "건성 and dry must map identically"


def test_skin_type_fit_works_for_korean_input() -> None:
    user = {"skin_type": "건성"}
    product = {"top_concern_pos_ids": [{"id": "concern_dryness"}]}
    fit = _skin_type_fit(user, product)
    assert fit > 0, f"건성 + dryness boost should produce positive fit; got {fit}"


def test_skin_type_fit_works_for_english_alias() -> None:
    user = {"skin_type": "dry"}
    product = {"top_concern_pos_ids": [{"id": "concern_dryness"}]}
    fit = _skin_type_fit(user, product)
    assert fit > 0, f"dry + dryness boost should produce positive fit; got {fit}"


def test_skin_type_fit_works_for_normalized_input() -> None:
    """normalize_text idempotent — already-normalized input still matches."""
    user = {"skin_type": normalize_text("dry")}
    product = {"top_concern_pos_ids": [{"id": "concern_dryness"}]}
    fit = _skin_type_fit(user, product)
    assert fit > 0


def test_unknown_skin_type_returns_zero() -> None:
    user = {"skin_type": "unknown-skin"}
    product = {"top_concern_pos_ids": [{"id": "concern_dryness"}]}
    fit = _skin_type_fit(user, product)
    assert fit == 0.0


def test_hardcoded_dict_removed_from_scorer() -> None:
    """The old `_SKIN_TYPE_CONCERN_MAP` literal must not survive — replaced
    by the YAML loader. Catches accidental revert."""
    src = inspect.getsource(scorer)
    # The replacement uses `_get_skin_type_concern_map()` callable, not a dict literal
    assert "_SKIN_TYPE_CONCERN_MAP = {" not in src
    assert "_get_skin_type_concern_map" in src
    assert "skin_type_concern_map.yaml" in src or "_load_skin_type_concern_map" in src


def test_yaml_file_exists_and_has_required_canonical_keys() -> None:
    """Contract: configs/skin_type_concern_map.yaml must exist with all 5 canonicals."""
    from pathlib import Path
    yaml_path = Path(__file__).parent.parent / "configs" / "skin_type_concern_map.yaml"
    assert yaml_path.exists()
    text = yaml_path.read_text(encoding="utf-8")
    for canonical in ("건성", "지성", "복합성", "민감성", "수부지"):
        assert f"canonical: {canonical}" in text, f"missing canonical: {canonical}"


def test_cache_used_for_repeated_lookups() -> None:
    """Lookup should not re-read YAML every call."""
    first = _get_skin_type_concern_map()
    second = _get_skin_type_concern_map()
    assert first is second, "lookup map must be cached"
