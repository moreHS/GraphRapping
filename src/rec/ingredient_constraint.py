"""Ingredient hard-filter constraint model + a single pure matcher (Phase 6 B2).

An ``IngredientConstraint`` is one *ingredient family* (성분군) the query asks for:
the catalog INCI concept ids of the family (structured axis) plus the colloquial
name surfaces (관용어 + 오타 변형) used to match a product NAME (name axis). The
matcher decides, per product, whether it carries the family and via which axis.

Placement note (plan §4 "순환 import 주의"): the plan *recommends*
``query_understanding.py`` for the model, but ``query_understanding`` imports
``src.rec.search`` and ``src.rec.search`` needs both the model (for
``search_products``'s new parameter) and the matcher — a cycle. This module
imports only ``text_normalize`` (no ``search``/``query_understanding`` import),
so both consume it cycle-free. B1's ``src.rec.negation`` is NOT modified; its FREE
marker vocabulary is mirrored here for the product-name suffix guard (see below).

Semantics (plan §4):
- Within a family: INCI variants (structured) OR name surfaces = OR.
- Across families (a list of constraints): AND.
- structured ∪ name for a single family = OR (either axis satisfies it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.common.text_normalize import normalize_text

# Product-NAME free-of guard. Mirrors ``src.rec.negation``'s FREE marker
# vocabulary (프리|free), applied here as an immediate name suffix with an
# OPTIONAL separator. A product name gives the exact ingredient surface, so a
# no-separator compound like "레티놀프리"/"파라벤프리" is a legitimate "free-of"
# claim to guard against — unlike negation.py's *query* pattern, which REQUIRES a
# separator to avoid the 이니스프리(=Innisfree) false positive where the preceding
# token boundary is ambiguous. (negation.py itself is left untouched — B1 output
# is consumed, not refactored.)
#
# The marker must be a *word*, not a prefix of a longer one: 프리 followed by
# another Hangul syllable (프리미엄 = premium) or free followed by an alnum
# (freedom) is NOT a free-of claim, so "레티놀 프리미엄 크림" reads as CONTAINING
# retinol (codex F9). Matching runs on the normalized (lowercased) name.
_NAME_FREE_SUFFIX_RE = re.compile(
    r"[\s-]*(?:프리(?![가-힣])|free(?![a-z0-9]))", re.IGNORECASE
)
_MIN_NAME_SURFACE_LEN = 2


def _norm_ingredient_suffix(value: str) -> str:
    """Normalize an ingredient token to the shared *suffix* domain: the INCI name
    after any ``concept:Ingredient:`` prefix, normalized. A concept IRI
    (``concept:Ingredient:소듐하이알루로네이트``) and a raw master string
    (``소듐하이알루로네이트``) both fold to the same key, so structured matching sees
    them as one (codex F2)."""
    parts = value.split(":", 2)
    suffix = parts[2] if len(parts) == 3 else value
    return normalize_text(suffix)


@dataclass
class IngredientConstraint:
    """One ingredient family (성분군) the query requires.

    - ``label``: the 관용어 the user actually typed (chip / summary copy).
    - ``inci_concept_ids``: catalog-existing INCI concept ids of the family (the
      structured axis; a product carrying ANY of them satisfies the family).
    - ``name_surfaces``: the family's alias keys (관용어 + 오타 변형) used for the
      product-NAME axis.
    - ``provenance``: "raw" (a family surface is literally present in the raw
      query, outside a negation span → hard-filter eligible) or "llm" (the LLM
      adopted it as ``ingredients_wanted`` with no raw surface → soft boost only).
    """

    label: str
    inci_concept_ids: list[str]
    name_surfaces: list[str]
    provenance: str  # "raw" | "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "inci_concept_ids": list(self.inci_concept_ids),
            "name_surfaces": list(self.name_surfaces),
            "provenance": self.provenance,
        }


def _name_carries_surface(name_norm: str, surface_norm: str) -> bool:
    """True iff ``surface_norm`` appears in ``name_norm`` at least once NOT
    immediately followed by a free marker (프리/free).

    "레티놀프리 크림"/"레티놀 프리 크림" → False for surface "레티놀" (free-of claim);
    "레티놀 크림"/"그린티히알루론산로션" → True (a genuine name mention).
    """
    start = 0
    while True:
        pos = name_norm.find(surface_norm, start)
        if pos == -1:
            return False
        after = name_norm[pos + len(surface_norm):]
        if not _NAME_FREE_SUFFIX_RE.match(after):
            return True
        start = pos + 1


def match_ingredient_constraint(
    product: dict[str, Any],
    constraint: IngredientConstraint,
) -> str | None:
    """Return the axis by which ``product`` satisfies ``constraint``:
    ``"ingredient"`` (structured concept/raw id ∩ family INCI — mirrors
    ``server._avoided_ingredient_product_ids``), ``"name"`` (a family surface is
    present in ``representative_product_name``, not as a free-of claim), or
    ``None``.

    Pure: reads the product dict only; never mutates it (the shared request-scoped
    profile must stay unchanged — [C1]). Structured wins over name (a carrier is
    reported by its strongest axis).

    Structured matching runs in the normalized suffix domain (codex F2): the
    constraint's INCI concept ids and BOTH product fields (concept ids AND the raw
    ``ingredient_ids`` master strings) fold to normalized INCI suffixes, so a
    product carrying the ingredient only as a raw string is not missed.
    """
    inci_suffixes = {_norm_ingredient_suffix(cid) for cid in constraint.inci_concept_ids}
    inci_suffixes.discard("")
    if inci_suffixes:
        structured = {
            _norm_ingredient_suffix(str(v))
            for v in (product.get("ingredient_concept_ids") or [])
        }
        structured.update(
            normalize_text(str(v)) for v in (product.get("ingredient_ids") or [])
        )
        structured.discard("")
        if structured & inci_suffixes:
            return "ingredient"

    name_norm = normalize_text(str(product.get("representative_product_name") or ""))
    if name_norm:
        for surface in constraint.name_surfaces:
            surface_norm = normalize_text(str(surface))
            if len(surface_norm) >= _MIN_NAME_SURFACE_LEN and _name_carries_surface(
                name_norm, surface_norm
            ):
                return "name"
    return None


def product_passes_constraints(
    product: dict[str, Any],
    constraints: list[IngredientConstraint],
) -> bool:
    """AND across families: the product must satisfy EVERY constraint (via either
    axis). An empty constraint list passes vacuously (callers gate on non-empty)."""
    return all(
        match_ingredient_constraint(product, c) is not None for c in constraints
    )


def matched_name_labels(
    product: dict[str, Any],
    constraints: list[IngredientConstraint],
) -> list[str]:
    """Labels of the constraints this product satisfies BY NAME (not structured),
    deduped in order. Used to attach the ``product_name:<label>`` overlap axis so a
    name-only carrier clears the "overlap ≥ 1" / evidence gate (plan §4)."""
    labels: list[str] = []
    seen: set[str] = set()
    for c in constraints:
        if match_ingredient_constraint(product, c) == "name" and c.label not in seen:
            seen.add(c.label)
            labels.append(c.label)
    return labels
