"""Pure ingredient-constraint matcher tests (Phase 6 Track B, B2).

The matcher (``src.rec.ingredient_constraint.match_ingredient_constraint``) is a
single pure function reused by 4 wiring sites (login /api/ask hard gate, anonymous
/api/ask + /api/search, relax counting, related_products filter). It classifies a
product against one ingredient family as "ingredient" (structured concept/raw id
∩ family INCI), "name" (a family surface in representative_product_name, not a
free-of claim), or None — and must never mutate the shared product dict.
"""

from __future__ import annotations

import copy
from typing import Any

from src.rec.ingredient_constraint import (
    IngredientConstraint,
    match_ingredient_constraint,
    matched_name_labels,
    product_passes_constraints,
)

_HYA_S = "concept:Ingredient:소듐하이알루로네이트"
_HYA_A = "concept:Ingredient:하이알루로닉애씨드"
_RETINOL = "concept:Ingredient:레티놀"


def _product(pid: str = "P1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "representative_product_name": "",
    }
    base.update(overrides)
    return base


def _hya() -> IngredientConstraint:
    return IngredientConstraint(
        label="히알루론",
        inci_concept_ids=[_HYA_S, _HYA_A],
        name_surfaces=["히알루론산", "히알루론", "히아루론산"],
        provenance="raw",
    )


def _retinol() -> IngredientConstraint:
    return IngredientConstraint(
        label="레티놀",
        inci_concept_ids=[_RETINOL],
        name_surfaces=["레티놀"],
        provenance="raw",
    )


# ---------------------------------------------------------------------------
# Axis classification: ingredient / name / None
# ---------------------------------------------------------------------------


def test_match_structured_concept_id() -> None:
    product = _product(ingredient_concept_ids=[_HYA_S], representative_product_name="어떤 크림")
    assert match_ingredient_constraint(product, _hya()) == "ingredient"


def test_match_structured_raw_ingredient_id() -> None:
    """[F2] Real data stores ``ingredient_ids`` as RAW INCI strings (not concept
    IRIs). The structured axis normalizes both sides to the INCI suffix domain, so
    a carrier that holds the ingredient only as a raw master string still matches
    (a direct IRI∩raw intersection would have missed it — non-vacuous)."""
    product = _product(ingredient_ids=["하이알루로닉애씨드"], representative_product_name="어떤 크림")
    assert match_ingredient_constraint(product, _hya()) == "ingredient"


def test_match_name_axis_when_no_structured() -> None:
    product = _product(representative_product_name="그린티히알루론산 로션")
    assert match_ingredient_constraint(product, _hya()) == "name"


def test_match_structured_wins_over_name() -> None:
    product = _product(ingredient_concept_ids=[_HYA_S], representative_product_name="그린티히알루론산 로션")
    assert match_ingredient_constraint(product, _hya()) == "ingredient"


def test_match_none_when_neither_axis() -> None:
    product = _product(ingredient_concept_ids=["concept:Ingredient:정제수"], representative_product_name="정제수 토너")
    assert match_ingredient_constraint(product, _hya()) is None


# ---------------------------------------------------------------------------
# Name free-of guard (프리/free suffix) — shares negation.py's marker vocabulary
# ---------------------------------------------------------------------------


def test_name_free_suffix_no_separator_blocks_match() -> None:
    """'레티놀프리' (no separator, common Korean 'free-of' compound) must NOT count as
    a retinol name mention."""
    product = _product(representative_product_name="레티놀프리 수분크림")
    assert match_ingredient_constraint(product, _retinol()) is None


def test_name_free_suffix_with_separator_blocks_match() -> None:
    for name in ("레티놀 프리 크림", "레티놀-프리 크림", "레티놀-Free 크림"):
        product = _product(representative_product_name=name)
        assert match_ingredient_constraint(product, _retinol()) is None, name


def test_name_genuine_mention_still_matches() -> None:
    """A real name mention (surface not followed by 프리/free) matches by name."""
    product = _product(representative_product_name="레티놀 나이트 크림")
    assert match_ingredient_constraint(product, _retinol()) == "name"


def test_name_premium_is_not_free_of() -> None:
    """[F9] '프리미엄'(premium) is a word starting with 프리 but is NOT a free-of
    marker (프리 followed by a Hangul syllable), so the product CONTAINS retinol —
    both spaced and compound forms match by name."""
    for name in ("레티놀 프리미엄 크림", "레티놀프리미엄크림"):
        product = _product(representative_product_name=name)
        assert match_ingredient_constraint(product, _retinol()) == "name", name


def test_name_free_marker_still_blocks_after_premium_fix() -> None:
    """The F9 word-boundary refinement must not weaken the genuine free-of guard."""
    for name in ("레티놀프리 크림", "레티놀 프리 크림", "레티놀-free 크림"):
        product = _product(representative_product_name=name)
        assert match_ingredient_constraint(product, _retinol()) is None, name


def test_name_free_of_one_occurrence_but_genuine_elsewhere_matches() -> None:
    """A free-of occurrence does not suppress a separate genuine occurrence."""
    product = _product(representative_product_name="레티놀프리 그리고 레티놀 세럼")
    assert match_ingredient_constraint(product, _retinol()) == "name"


# ---------------------------------------------------------------------------
# Purity — the matcher never mutates the shared product dict
# ---------------------------------------------------------------------------


def test_matcher_is_pure_no_product_mutation() -> None:
    product = _product(ingredient_concept_ids=[_HYA_S], representative_product_name="그린티히알루론산 로션")
    before = copy.deepcopy(product)
    match_ingredient_constraint(product, _hya())
    match_ingredient_constraint(product, _retinol())
    assert product == before


# ---------------------------------------------------------------------------
# AND across families + name-label collection
# ---------------------------------------------------------------------------


def test_product_passes_constraints_and_semantics() -> None:
    both = _product(ingredient_concept_ids=[_HYA_S, _RETINOL])
    only_hya = _product(ingredient_concept_ids=[_HYA_S])
    assert product_passes_constraints(both, [_hya(), _retinol()]) is True
    assert product_passes_constraints(only_hya, [_hya(), _retinol()]) is False
    # Single family — passes on its own.
    assert product_passes_constraints(only_hya, [_hya()]) is True


def test_matched_name_labels_only_name_axis() -> None:
    # Structured for hya, name for retinol → only retinol's label is returned.
    product = _product(
        ingredient_concept_ids=[_HYA_S],
        representative_product_name="레티놀 세럼",
    )
    assert matched_name_labels(product, [_hya(), _retinol()]) == ["레티놀"]


def test_matched_name_labels_dedups() -> None:
    product = _product(representative_product_name="히알루론산 크림")
    # Two constraints with the same label matched by name → deduped.
    c1, c2 = _hya(), _hya()
    assert matched_name_labels(product, [c1, c2]) == ["히알루론"]


# ---------------------------------------------------------------------------
# to_dict shape (frontend/interpretation contract)
# ---------------------------------------------------------------------------


def test_constraint_to_dict_shape() -> None:
    payload = _hya().to_dict()
    # [A3] ``strength`` is an additive field (defaults to "required").
    assert set(payload) == {
        "label", "inci_concept_ids", "name_surfaces", "provenance", "strength"
    }
    assert payload["label"] == "히알루론"
    assert payload["provenance"] == "raw"
    assert payload["strength"] == "required"
    assert payload["inci_concept_ids"] == [_HYA_S, _HYA_A]
