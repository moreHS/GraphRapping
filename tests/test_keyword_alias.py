"""Phase 7 B2 — keyword canonical alias layer (동일 개념 접힘).

Covers:
  1. The flattened alias-map loader: cycle rejection + single-hop chaining so a
     canonical target that is itself an alias resolves transitively.
  2. resolve_surface_keywords folding: sibling ids of one concept collapse to
     the canonical id, and a single surface that hits multiple sibling ids no
     longer double-emits (the "촉촉한" → kw_moist + MoistLike double-count).
  3. The real configs/keyword_alias_map.yaml folds the moisture cluster.
"""

from __future__ import annotations

import pytest

from src.normalize.bee_normalizer import (
    _flatten_alias_chains,
    canonical_keyword_id,
    load_keyword_alias_map,
    resolve_surface_keywords,
)


# ---------------------------------------------------------------------------
# 1. alias-map loader error classes
# ---------------------------------------------------------------------------


def test_flatten_passthrough_when_not_aliased() -> None:
    resolved = _flatten_alias_chains({"a": "b"})
    assert resolved == {"a": "b"}
    assert canonical_keyword_id("a", resolved) == "b"
    assert canonical_keyword_id("z", resolved) == "z"  # identity for non-aliases


def test_flatten_rejects_direct_cycle() -> None:
    with pytest.raises(ValueError, match="cycle"):
        _flatten_alias_chains({"a": "b", "b": "a"})


def test_flatten_rejects_self_cycle() -> None:
    with pytest.raises(ValueError, match="cycle"):
        _flatten_alias_chains({"a": "a"})


def test_flatten_resolves_chain_to_terminal() -> None:
    # a -> b -> c must resolve a (and b) to the terminal canonical c.
    resolved = _flatten_alias_chains({"a": "b", "b": "c"})
    assert resolved["a"] == "c"
    assert resolved["b"] == "c"


def test_flatten_rejects_longer_cycle() -> None:
    with pytest.raises(ValueError, match="cycle"):
        _flatten_alias_chains({"a": "b", "b": "c", "c": "a"})


# ---------------------------------------------------------------------------
# 2. resolution folding + double-count elimination
# ---------------------------------------------------------------------------


_CLUSTER_MAP = {
    "보습": [{"keyword_id": "kw_moisturizing", "label_ko": "보습좋음"}],
    "촉촉": [{"keyword_id": "kw_moist", "label_ko": "촉촉함"}],
    "촉촉한": [{"keyword_id": "MoistLike", "label_ko": "촉촉한"}],
}


def test_sibling_surface_folds_to_canonical() -> None:
    # "촉촉" alone resolves kw_moist -> canonical kw_moisturizing with the
    # canonical concept's label.
    matches = resolve_surface_keywords("촉촉해요", _CLUSTER_MAP)
    assert [m[0] for m in matches] == ["kw_moisturizing"]
    assert matches[0][1] == "보습좋음"


def test_single_surface_no_longer_double_counts() -> None:
    # "촉촉한" hits BOTH kw_moist (substring 촉촉) and MoistLike (촉촉한); with
    # folding both collapse to one canonical id — the double-count is gone.
    raw = resolve_surface_keywords("촉촉한 느낌", _CLUSTER_MAP, apply_alias=False)
    assert sorted(m[0] for m in raw) == ["MoistLike", "kw_moist"]  # 2 rows before

    folded = resolve_surface_keywords("촉촉한 느낌", _CLUSTER_MAP)
    assert [m[0] for m in folded] == ["kw_moisturizing"]  # 1 row after


def test_non_cluster_keyword_unaffected() -> None:
    kmap = {"건조": [{"keyword_id": "kw_dry", "label_ko": "건조함"}]}
    matches = resolve_surface_keywords("건조해요", kmap)
    assert [m[0] for m in matches] == ["kw_dry"]


# ---------------------------------------------------------------------------
# 3. shipped config
# ---------------------------------------------------------------------------


def test_shipped_alias_map_folds_moisture_cluster() -> None:
    amap = load_keyword_alias_map(force=True)
    assert amap.get("kw_moist") == "kw_moisturizing"
    assert amap.get("MoistLike") == "kw_moisturizing"
    # canonical id is terminal — not itself an alias key (no chaining in prod).
    assert "kw_moisturizing" not in amap
