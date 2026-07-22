"""Ingredient alias layer + shared negation module (Phase 6 Track B, B1).

The bare ingredient axis (tests/test_search.py) only resolves an INCI when its
own normalized surface appears verbatim in the query. B1 adds an ALIAS layer that
bridges colloquial names (관용어, e.g. 히알루론) to catalog INCI concept ids via
configs/ingredient_alias_map.yaml, still gated to catalog-existing ids and refused
inside a negation span. Covers:

- positive adoption (marquee query), catalog-existence gate, 관용어 label;
- negation-span guard (없는/빼고/프리/-free) via the shared src.rec.negation module;
- the alias map file's structural integrity (hyaluron group + augmentation).

Product fixtures mirror the tests/test_search.py _product pattern. The alias map
is loaded from the real YAML (lru_cache); the catalog gate is exercised against
these synthetic products' ingredient_concept_ids.
"""

from __future__ import annotations

from typing import Any

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.rec.negation import negated_surfaces
from src.rec.search import _ingredient_alias_dict, resolve_query_concepts

# Catalog INCI concept ids for the hyaluron family (all 4 catalog 하이알루 tokens).
_HYA_SODIUM = "concept:Ingredient:소듐하이알루로네이트"
_HYA_ACID = "concept:Ingredient:하이알루로닉애씨드"
_HYA_HYDRO = "concept:Ingredient:하이드롤라이즈드하이알루로닉애씨드"
_HYA_XPOLY = "concept:Ingredient:소듐하이알루로네이트크로스폴리머"
_RETINOL = "concept:Ingredient:레티놀"


def _product(pid: str = "P1", *, ingredients: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_name": None,
        "brand_concept_ids": [],
        "category_name": None,
        "category_concept_ids": [],
        "ingredient_ids": [],
        "ingredient_concept_ids": list(ingredients or []),
        "main_benefit_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [],
        "top_concern_pos_ids": [],
    }
    base.update(overrides)
    return base


def _ingredient_ids(query: str, products: list[dict[str, Any]]) -> set[str]:
    return {
        c.concept_id
        for c in resolve_query_concepts(query, products)
        if c.concept_type == "ingredient"
    }


def _ingredient_concepts(query: str, products: list[dict[str, Any]]) -> list[Any]:
    return [c for c in resolve_query_concepts(query, products) if c.concept_type == "ingredient"]


# ---------------------------------------------------------------------------
# Positive adoption + catalog-existence gate + 관용어 label
# ---------------------------------------------------------------------------


def test_alias_marquee_query_adopts_only_catalog_existing_inci() -> None:
    """"히알루론 든거 뭐 좋은거 없나": the alias key 히알루론 maps to all 4 catalog
    하이알루 tokens, but only the 2 the product actually carries are adopted (the
    catalog-existence gate), and the concept label is the 관용어 (user language)."""
    product = _product("P1", ingredients=[_HYA_SODIUM, _HYA_ACID])
    concepts = _ingredient_concepts("히알루론 든거 뭐 좋은거 없나", [product])

    adopted = {c.concept_id for c in concepts}
    assert adopted == {_HYA_SODIUM, _HYA_ACID}  # only the catalog-existing 2
    assert _HYA_HYDRO not in adopted and _HYA_XPOLY not in adopted
    # label + matched_text carry the 관용어, not the INCI surface.
    assert all(c.label == "히알루론" and c.matched_text == "히알루론" for c in concepts)


def test_alias_marquee_query_has_no_negation_marker() -> None:
    """"...좋은거 없나" ends in 없나, which is NOT a negation marker (없는), so the
    marquee query is not misread as a negation."""
    assert negated_surfaces("히알루론 든거 뭐 좋은거 없나") == set()


def test_alias_catalog_gate_zero_when_product_lacks_inci() -> None:
    """An alias surface that matches the query but whose mapped INCI are absent from
    the loaded catalog adopts nothing (no forged concept ids)."""
    product = _product("P1", ingredients=["concept:Ingredient:정제수"])
    assert _ingredient_ids("히알루론 든거", [product]) == set()


def test_alias_retinol_via_vitamin_a_positive() -> None:
    """비타민에이 → 레티놀 (real catalog token). The alias layer resolves 레티놀 from a
    surface (비타민에이) the bare axis could never match, labelled with the 관용어."""
    product = _product("PR", ingredients=[_RETINOL])
    concepts = _ingredient_concepts("비타민에이 든거", [product])
    assert {c.concept_id for c in concepts} == {_RETINOL}
    assert concepts[0].label == "비타민에이"


# ---------------------------------------------------------------------------
# Negation-span guard (resolution-level defence; protects every caller)
# ---------------------------------------------------------------------------


def test_alias_negation_없는_blocks_adoption() -> None:
    """"히알루론 없는 크림": the alias surface sits inside a negated word, so the alias
    layer refuses to pull the hyaluron product in (the /api/search reversal codex
    flagged)."""
    product = _product("P1", ingredients=[_HYA_SODIUM, _HYA_ACID])
    assert _ingredient_ids("히알루론 없는 크림", [product]) == set()


def test_alias_negation_빼고_blocks_adoption() -> None:
    product = _product("P1", ingredients=[_HYA_SODIUM])
    assert _ingredient_ids("히알루론 빼고 추천", [product]) == set()


def test_alias_negation_retinol_via_vitamin_a_blocked() -> None:
    """Non-vacuous retinol negation: 비타민에이 IS an alias key mapping to 레티놀 (which
    the product carries), so WITHOUT the negation guard "비타민에이 없는" would adopt
    레티놀 — the guard makes it zero."""
    product = _product("PR", ingredients=[_RETINOL])
    assert _ingredient_ids("비타민에이 없는 크림", [product]) == set()
    # Sanity: the same surface WITHOUT negation does adopt (proves the guard, not a
    # missing mapping, is what suppresses it).
    assert _ingredient_ids("비타민에이 든거", [product]) == {_RETINOL}


def test_alias_negation_free_marker_blocks_adoption() -> None:
    """The loanword free markers ("프리" with a separator / "-free") suppress the
    alias layer too."""
    product = _product("P1", ingredients=[_HYA_SODIUM])
    assert _ingredient_ids("히알루론 프리 크림", [product]) == set()
    assert _ingredient_ids("히알루론-프리 크림", [product]) == set()
    assert _ingredient_ids("히알루론-Free 크림", [product]) == set()


def test_alias_negation_scoped_to_negated_word_only() -> None:
    """A mixed query "레티놀 없는 히알루론 크림" negates only 레티놀; the (non-negated)
    히알루론 alias is still adopted."""
    product = _product("P1", ingredients=[_HYA_SODIUM, _RETINOL])
    adopted = _ingredient_ids("레티놀 없는 히알루론 크림", [product])
    assert _HYA_SODIUM in adopted


# ---------------------------------------------------------------------------
# [F2 codex] Nested alias keys — longest match wins (비타민 ⊂ 비타민A, 히알루론 ⊂
# 히알루론산). A matched surface that is a substring of another matched surface is
# dropped, so two DIFFERENT families never both fire on one token.
# ---------------------------------------------------------------------------

_ASCORBIC = "concept:Ingredient:아스코빅애씨드"


def test_alias_longest_match_비타민A_excludes_비타민_family() -> None:
    """"비타민A 든거" matches both '비타민'(→아스코빅애씨드) and '비타민A'(→레티놀); the
    longest-match rule keeps ONLY 비타민A, so vitamin C is NOT bound as a second
    (AND) family."""
    product = _product("P", ingredients=[_RETINOL, _ASCORBIC])
    ids = _ingredient_ids("비타민A 든거", [product])
    assert ids == {_RETINOL}  # 아스코빅애씨드 (비타민 family) not adopted


def test_alias_bare_비타민_still_resolves_vitamin_c() -> None:
    """'비타민' typed alone (no longer nested key present) still resolves its own
    family — longest-match only drops it when a superstring key also matched."""
    product = _product("P", ingredients=[_ASCORBIC])
    ids = _ingredient_ids("비타민 크림 추천", [product])
    assert ids == {_ASCORBIC}


def test_alias_longest_match_same_family_nested_is_result_stable() -> None:
    """'히알루론' ⊂ '히알루론산' but SAME INCI set — dropping either yields the same
    catalog concept ids (result stable regardless of which survives)."""
    product = _product("P", ingredients=[_HYA_SODIUM, _HYA_ACID])
    assert _ingredient_ids("히알루론산 히알루론 크림", [product]) == {_HYA_SODIUM, _HYA_ACID}


# ---------------------------------------------------------------------------
# Bare-axis behaviour is unchanged when no alias surface is present
# ---------------------------------------------------------------------------


def test_no_alias_surface_leaves_resolution_unchanged() -> None:
    """A query with no alias surface resolves exactly as before (the alias layer is
    additive; it must not perturb the bare axes)."""
    product = _product("P1", ingredients=[_RETINOL])
    # 레티놀 is NOT an alias key (INGREDIENT_DICT 레티놀→레티닐팔미테이트 is dropped: the
    # ester is not a catalog token), so only the BARE axis resolves it here.
    concepts = _ingredient_concepts("레티놀 세럼 궁금해요", [product])
    assert {c.concept_id for c in concepts} == {_RETINOL}
    # Bare-axis label is the INCI suffix, not a 관용어.
    assert concepts[0].label == "레티놀"


# ---------------------------------------------------------------------------
# Shared negation module (src.rec.negation)
# ---------------------------------------------------------------------------


def test_negation_markers_detected() -> None:
    for query in (
        "레티놀 없는 크림",
        "레티놀 없이 세럼",
        "레티놀 빼고",
        "레티놀 제외",
        "레티놀 제외한 것",
        "레티놀-프리 토너",
        "레티놀 프리",
        "레티놀-Free 크림",
    ):
        assert negated_surfaces(query) == {"레티놀"}, query


def test_negation_free_marker_requires_separator() -> None:
    """"프리" attached with no separator is NOT a negation (이니스프리 = Innisfree brand);
    only a space/hyphen-separated 프리/free counts."""
    assert negated_surfaces("이니스프리 토너 추천") == set()
    assert normalize_text("레티놀") in negated_surfaces("레티놀 프리")  # separated → counts


# ---------------------------------------------------------------------------
# Alias map file integrity (configs/ingredient_alias_map.yaml)
# ---------------------------------------------------------------------------


def test_alias_map_hyaluron_group_and_augmentation() -> None:
    alias_map = load_yaml("ingredient_alias_map.yaml")
    # At least the 85 seed entries + 2 augmentation keys.
    assert len(alias_map) >= 87

    hya4 = {"소듐하이알루로네이트", "하이알루로닉애씨드", "하이드롤라이즈드하이알루로닉애씨드", "소듐하이알루로네이트크로스폴리머"}
    for key in ("히알루론산", "히알루론", "히아루론산"):
        assert key in alias_map
        assert {normalize_text(t) for t in alias_map[key]} == {normalize_text(t) for t in hya4}

    # General-pass 다이메티콘-family additions on 실리콘.
    silicone = {normalize_text(t) for t in alias_map["실리콘"]}
    for tok in ("다이메티콘올", "아모다이메티콘", "다이페닐다이메티콘"):
        assert normalize_text(tok) in silicone


def test_alias_dict_loader_is_the_same_file() -> None:
    """The cached loader used at runtime reads the same map the integrity test checks."""
    assert _ingredient_alias_dict()["히알루론"]
