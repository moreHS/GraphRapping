"""Tests for LLM query understanding (Phase 6 Track B, B1).

Covers the evidence-first contract: the LLM is a translator whose every output
is re-validated through the dictionary/catalog gate (search.resolve_query_concepts),
hallucinations are excluded + surfaced in unresolved_terms, the result is a
superset of the dictionary fallback, avoided ingredients are catalog-gated, and
every LLM-unavailable path (off / error / timeout / missing httpx) degrades to
the fallback with the same return shape.

Product fixtures mirror the tests/test_search.py _product pattern.
"""

from __future__ import annotations

import builtins
import os
from typing import Any

import pytest

from src.rec.query_understanding import (
    QueryInterpretation,
    clear_query_cache,
    understand_query,
)
from src.rec.search import resolve_query_concepts


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _product(pid: str = "P1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": pid,
        "brand_name": None,
        "brand_concept_ids": [],
        "category_name": None,
        "category_concept_ids": [],
        "ingredient_ids": [],
        "ingredient_concept_ids": [],
        "main_benefit_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [],
        "top_concern_pos_ids": [],
    }
    base.update(overrides)
    return base


def _products() -> list[dict[str, Any]]:
    return [
        _product(
            "P1",
            brand_name="이니스프리",
            brand_concept_ids=["concept:Brand:이니스프리"],
            category_name="토너",
            category_concept_ids=["concept:Category:토너"],
            ingredient_ids=["히알루론산", "레티놀"],
            ingredient_concept_ids=["concept:Ingredient:히알루론산", "concept:Ingredient:레티놀"],
            main_benefit_ids=["보습강화"],
            main_benefit_concept_ids=["concept:Goal:보습"],
            top_keyword_ids=[{"id": "kw_moist", "score": 0.9}],
            top_concern_pos_ids=[{"id": "concern_dryness", "score": 0.8}],
        ),
    ]


def _fake_json(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "search",
        "categories": [],
        "brands": [],
        "product_names": [],
        "desired_attributes": [],
        "ingredients_wanted": [],
        "ingredients_avoided": [],
        "concerns": [],
        "goals": [],
    }
    base.update(overrides)
    return base


class FakeLLMClient:
    """Deterministic LLMClient stub: returns a fixture dict or raises."""

    def __init__(self, response: dict[str, Any] | None = None, *, raises: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.calls = 0

    def complete_json(self, system: str, user: str, *, timeout_sec: float) -> dict[str, Any]:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


def _ids(interp: QueryInterpretation) -> set[str]:
    return {c.concept_id for c in interp.resolved_concepts}


def _typed_ids(concepts: list[Any]) -> set[tuple[str, str]]:
    return {(c.concept_type, c.concept_id) for c in concepts}


@pytest.fixture(autouse=True)
def _isolate_cache() -> Any:
    clear_query_cache()
    yield
    clear_query_cache()


# ---------------------------------------------------------------------------
# (a) normal extraction → validated concepts adopted (recall broadened)
# ---------------------------------------------------------------------------


def test_llm_extraction_adopts_validated_concepts_and_broadens_recall() -> None:
    products = _products()
    # Base query resolves only the brand; the LLM adds category/ingredient/concern
    # terms not present in the raw query — all must validate and be adopted.
    fake = FakeLLMClient(
        _fake_json(
            intent="recommend",
            brands=["이니스프리"],
            categories=["토너"],
            ingredients_wanted=["히알루론산"],
            concerns=["건조"],
        )
    )
    interp = understand_query("이니스프리 신제품 궁금해요", products, llm=fake)

    assert interp.llm_used is True
    assert interp.intent == "recommend"
    ids = _ids(interp)
    assert "concept:Brand:이니스프리" in ids  # from base query
    assert "concept:Ingredient:히알루론산" in ids  # LLM-added, not in raw query
    assert "concern_dryness" in ids  # LLM-added, not in raw query
    assert "concept:Category:토너" in ids or "concept:Category:skincare" in ids
    assert interp.unresolved_terms == []


# ---------------------------------------------------------------------------
# (b) hallucinations excluded + surfaced in unresolved_terms (crossreview C3)
# ---------------------------------------------------------------------------


def test_hallucinated_terms_are_rejected_and_reported() -> None:
    products = _products()
    fake = FakeLLMClient(
        _fake_json(
            ingredients_wanted=["존재하지않는성분xyz"],
            concerns=["없는고민123", "건조"],
        )
    )
    interp = understand_query("이니스프리 신제품", products, llm=fake)

    ids = _ids(interp)
    # Valid term still adopted...
    assert "concern_dryness" in ids
    # ...hallucinations excluded from concepts entirely...
    assert not any("존재하지않는성분xyz" in cid for cid in ids)
    assert not any("없는고민123" in cid for cid in ids)
    # ...and explicitly reported, never silently dropped.
    assert "존재하지않는성분xyz" in interp.unresolved_terms
    assert "없는고민123" in interp.unresolved_terms


def test_bare_resolver_normalization_does_not_leak_hallucinated_concern() -> None:
    """Guards the C3 fix: resolve_concern_id would normalize an unknown surface
    and return it (a fake concept id); the membership gate must reject it."""
    fake = FakeLLMClient(_fake_json(concerns=["절대없는고민단어"]))
    interp = understand_query("아무 질의", [_product("P1")], llm=fake)
    assert interp.resolved_concepts == []
    assert "절대없는고민단어" in interp.unresolved_terms


# ---------------------------------------------------------------------------
# (c) LLM error / timeout → fallback identical to dictionary resolution
# ---------------------------------------------------------------------------


def test_llm_exception_falls_back_to_dictionary() -> None:
    products = _products()
    fake = FakeLLMClient(raises=RuntimeError("boom"))
    interp = understand_query("촉촉한 토너", products, llm=fake)

    assert interp.llm_used is False
    assert interp.intent == "search"
    assert interp.avoided_ingredient_concept_ids == []
    assert interp.unresolved_terms == []
    assert _typed_ids(interp.resolved_concepts) == _typed_ids(
        resolve_query_concepts("촉촉한 토너", products)
    )


def test_llm_timeout_falls_back_to_dictionary() -> None:
    products = _products()
    fake = FakeLLMClient(raises=TimeoutError("slow"))
    interp = understand_query("촉촉한 토너", products, llm=fake)
    assert interp.llm_used is False
    assert _typed_ids(interp.resolved_concepts) == _typed_ids(
        resolve_query_concepts("촉촉한 토너", products)
    )


# ---------------------------------------------------------------------------
# (d) provider off / unset env → fallback
# ---------------------------------------------------------------------------


def test_env_off_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_QUERY_LLM", "off")
    products = _products()
    interp = understand_query("촉촉한 토너", products)
    assert interp.llm_used is False
    assert _typed_ids(interp.resolved_concepts) == _typed_ids(
        resolve_query_concepts("촉촉한 토너", products)
    )


def test_env_unset_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("촉촉한 토너", _products())
    assert interp.llm_used is False


def test_env_unknown_value_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_QUERY_LLM", "gpt-something")
    interp = understand_query("촉촉한 토너", _products())
    assert interp.llm_used is False


# ---------------------------------------------------------------------------
# (e) avoided ingredient validation (catalog-gated) + negation subtraction
# ---------------------------------------------------------------------------


def test_avoided_ingredient_catalog_gated() -> None:
    products = _products()
    fake = FakeLLMClient(
        _fake_json(ingredients_avoided=["레티놀", "존재하지않는성분xyz"])
    )
    # Raw query does NOT mention 레티놀 — acceptance must come from the catalog.
    interp = understand_query("수분크림 추천", products, llm=fake)

    assert interp.avoided_ingredient_concept_ids == ["concept:Ingredient:레티놀"]
    assert "존재하지않는성분xyz" in interp.unresolved_terms
    # Avoided ingredient is not surfaced as a positive concept.
    assert "concept:Ingredient:레티놀" not in _ids(interp)


def test_avoided_ingredient_subtracted_from_positive_on_negation_query() -> None:
    products = _products()
    fake = FakeLLMClient(_fake_json(ingredients_avoided=["레티놀"], categories=["크림"]))
    # The substring gate resolves 레티놀 positively inside "레티놀 없는" — the
    # avoided list must win and remove it from the positive concepts.
    interp = understand_query("레티놀 없는 수분크림", products, llm=fake)

    assert interp.avoided_ingredient_concept_ids == ["concept:Ingredient:레티놀"]
    assert "concept:Ingredient:레티놀" not in _ids(interp)
    # A non-negated axis still resolves.
    assert "concept:Category:skincare" in _ids(interp)


# ---------------------------------------------------------------------------
# (e2) [F1] fallback negation preprocessing — no LLM, no mocking. The dictionary
# path itself must read "X 없는/없이/…" and flip X to the avoided side, and
# surface a warning when X is not a catalog ingredient (no silent failure).
# ---------------------------------------------------------------------------


def _niacinamide_products() -> list[dict[str, Any]]:
    """Catalog carrying 나이아신아마이드 (for the 없이-marker negation case)."""
    return [
        _product(
            "P_nia",
            category_name="세럼",
            category_concept_ids=["concept:Category:세럼"],
            ingredient_ids=["나이아신아마이드"],
            ingredient_concept_ids=["concept:Ingredient:나이아신아마이드"],
        ),
    ]


def test_fallback_detects_negation_없는_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()
    interp = understand_query("레티놀 없는 수분크림", products)  # no llm → dictionary fallback

    assert interp.llm_used is False
    assert "concept:Ingredient:레티놀" in interp.avoided_ingredient_concept_ids
    # Substring gate matched 레티놀 positively inside the negation; it must be
    # subtracted from the positive concepts.
    assert "concept:Ingredient:레티놀" not in _ids(interp)
    assert interp.warnings == []  # a resolved negation produces no warning


def test_fallback_detects_negation_없이_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _niacinamide_products()
    interp = understand_query("나이아신아마이드 없이 세럼", products)

    assert interp.llm_used is False
    assert interp.avoided_ingredient_concept_ids == ["concept:Ingredient:나이아신아마이드"]
    assert "concept:Ingredient:나이아신아마이드" not in _ids(interp)
    assert interp.warnings == []


def test_fallback_negation_unknown_ingredient_warns_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()  # 저분자콜라겐 is NOT in this catalog
    interp = understand_query("저분자콜라겐 없는 크림", products)

    assert interp.llm_used is False
    # Not forged into an avoided id (bare-resolver C3 discipline preserved)...
    assert interp.avoided_ingredient_concept_ids == []
    # ...surfaced as unresolved + a single user-facing warning (no silent failure).
    assert "저분자콜라겐" in interp.unresolved_terms
    assert len(interp.warnings) == 1
    assert "저분자콜라겐 없는" in interp.warnings[0]
    assert "성분으로 해석하지 못했습니다" in interp.warnings[0]


def test_fallback_no_negation_leaves_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a query with no negation marker keeps the exact pre-F1 fallback
    behavior — empty warnings, empty avoided, and resolved_concepts identical to a
    bare resolve_query_concepts."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()
    interp = understand_query("촉촉한 토너", products)

    assert interp.llm_used is False
    assert interp.warnings == []
    assert interp.avoided_ingredient_concept_ids == []
    assert interp.unresolved_terms == []
    assert _typed_ids(interp.resolved_concepts) == _typed_ids(
        resolve_query_concepts("촉촉한 토너", products)
    )


def test_fallback_negation_is_case_insensitive_free_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The English "-free" marker (case-insensitive) is honored on the fallback."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()
    interp = understand_query("레티놀-Free 크림 추천", products)

    assert interp.llm_used is False
    assert "concept:Ingredient:레티놀" in interp.avoided_ingredient_concept_ids
    assert "concept:Ingredient:레티놀" not in _ids(interp)


# ---------------------------------------------------------------------------
# (e3) [F2] fallback surfaces UNREFLECTED query tokens — no LLM. Meaningful
# tokens the dictionary reflected nowhere are surfaced (unresolved_terms + one
# warning) so two differently-worded queries stop collapsing to the same
# interpretation; request words / negation-consumed tokens are NOT re-surfaced.
# ---------------------------------------------------------------------------


def test_fallback_surfaces_unreflected_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("피부에 맞는 스킨케어", _products())

    assert interp.llm_used is False
    # The category IS reflected...
    assert "concept:Category:skincare" in _ids(interp)
    # ...and the tokens reflected nowhere are surfaced verbatim, in appearance
    # order (previously dropped silently — the reported bug).
    assert interp.unresolved_terms == ["피부에", "맞는"]
    assert len(interp.warnings) == 1
    assert "피부에, 맞는" in interp.warnings[0]
    assert "반영되지 않았습니다" in interp.warnings[0]


def test_fallback_two_worded_queries_no_longer_collapse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug: "피부에 맞는 스킨케어" and "성분이 좋은 스킨케어" resolved the
    identical (skincare-only) interpretation with no trace of the dropped words.
    Concepts still match, but unresolved_terms now distinguishes them."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()
    a = understand_query("피부에 맞는 스킨케어", products)
    b = understand_query("성분이 좋은 스킨케어", products)

    assert _typed_ids(a.resolved_concepts) == _typed_ids(b.resolved_concepts)
    assert a.unresolved_terms == ["피부에", "맞는"]
    assert b.unresolved_terms == ["성분이", "좋은"]
    assert a.unresolved_terms != b.unresolved_terms


def test_fallback_fully_resolved_query_surfaces_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("이니스프리 토너", _products())
    assert interp.llm_used is False
    assert interp.unresolved_terms == []
    assert interp.warnings == []


def test_fallback_request_word_not_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("수분크림 추천해줘", _products())
    assert interp.llm_used is False
    # "수분크림" is reflected (수분/크림); "추천해줘" is request phrasing — neither surfaces.
    assert interp.unresolved_terms == []
    assert interp.warnings == []


def test_fallback_negation_consumed_token_not_resurfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negated ingredient + its marker are owned by the negation path; F2 must
    not re-surface them as unreflected tokens (and adds no second warning)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("레티놀 없는 수분크림", _products())
    assert interp.llm_used is False
    assert interp.avoided_ingredient_concept_ids == ["concept:Ingredient:레티놀"]
    assert interp.unresolved_terms == []  # 레티놀 / 없는 not re-surfaced; 수분크림 reflected
    assert interp.warnings == []


def test_fallback_unknown_negation_does_not_double_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the negation path already surfaces its own unresolved term + warning,
    F2 adds neither a duplicate term nor a second warning."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("저분자콜라겐 없는 크림", _products())
    assert interp.unresolved_terms == ["저분자콜라겐"]  # from negation only, not duplicated
    assert len(interp.warnings) == 1  # negation warning only — no second F2 warning
    assert "성분으로 해석하지 못했습니다" in interp.warnings[0]


# ---------------------------------------------------------------------------
# (f) cache: identical query does not re-call the LLM
# ---------------------------------------------------------------------------


def test_llm_response_is_cached_per_query() -> None:
    products = _products()
    fake = FakeLLMClient(_fake_json(brands=["이니스프리"]))

    understand_query("이니스프리 토너", products, llm=fake)
    understand_query("이니스프리 토너", products, llm=fake)
    assert fake.calls == 1  # second call served from cache

    understand_query("촉촉한 세럼", products, llm=fake)
    assert fake.calls == 2  # different query → new call

    clear_query_cache()
    understand_query("이니스프리 토너", products, llm=fake)
    assert fake.calls == 3  # cache cleared → re-call


def test_cache_revalidates_against_current_products() -> None:
    """Only the LLM response is cached; validation re-runs against products, so
    the same cached response yields different concepts for different catalogs."""
    fake = FakeLLMClient(_fake_json(ingredients_wanted=["레티놀"]))

    with_catalog = understand_query("성분 질의", _products(), llm=fake)
    assert "concept:Ingredient:레티놀" in _ids(with_catalog)

    # Same normalized query (cache hit), but an empty catalog cannot gate 레티놀.
    without_catalog = understand_query("성분 질의", [_product("P0")], llm=fake)
    assert fake.calls == 1  # LLM response reused
    assert "concept:Ingredient:레티놀" not in _ids(without_catalog)
    assert "레티놀" in without_catalog.unresolved_terms


# ---------------------------------------------------------------------------
# (g) LLM result is always a superset of the dictionary fallback
# ---------------------------------------------------------------------------


def test_result_is_superset_of_dictionary_fallback() -> None:
    products = _products()
    # LLM returns nothing useful; base query resolution must still be preserved.
    fake = FakeLLMClient(_fake_json())
    interp = understand_query("촉촉한 토너", products, llm=fake)

    assert interp.llm_used is True
    fallback = _typed_ids(resolve_query_concepts("촉촉한 토너", products))
    assert fallback  # sanity: the query does resolve something
    assert fallback <= _typed_ids(interp.resolved_concepts)


# ---------------------------------------------------------------------------
# (h) httpx not installed → guarded fallback (no ImportError propagation)
# ---------------------------------------------------------------------------


def test_missing_httpx_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_QUERY_LLM", "azure")
    # Configure Azure creds so the ONLY reason to fall back is the missing httpx.
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unused-in-this-test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-x")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx" or name.startswith("httpx."):
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    products = _products()
    interp = understand_query("촉촉한 토너", products)
    assert interp.llm_used is False
    assert _typed_ids(interp.resolved_concepts) == _typed_ids(
        resolve_query_concepts("촉촉한 토너", products)
    )


# ---------------------------------------------------------------------------
# Misc: empty / whitespace query, length guard, to_dict shape
# ---------------------------------------------------------------------------


def test_blank_query_returns_empty_fallback_without_calling_llm() -> None:
    fake = FakeLLMClient(_fake_json(brands=["이니스프리"]))
    interp = understand_query("   ", _products(), llm=fake)
    assert fake.calls == 0
    assert interp.llm_used is False
    assert interp.resolved_concepts == []


def test_to_dict_shape() -> None:
    fake = FakeLLMClient(_fake_json(intent="recommend", concerns=["건조"]))
    payload = understand_query("이니스프리 신제품", _products(), llm=fake).to_dict()
    assert set(payload) == {
        "query",
        "intent",
        "resolved_concepts",
        "avoided_ingredient_concept_ids",
        "unresolved_terms",
        "llm_used",
        "warnings",
        "profile_refs",
        "ingredient_constraints",
        "excluded_product_ids",
        "excluded_brand_ids",
        "excluded_category_surfaces",
        "excluded_category_groups",
    }
    assert payload["intent"] == "recommend"
    assert payload["llm_used"] is True
    assert isinstance(payload["resolved_concepts"], list)
    assert all("concept_id" in c for c in payload["resolved_concepts"])
    # Frontend contract: warnings is always present and a list (default []).
    assert payload["warnings"] == []
    # [F4-c''] profile_refs always present and a list (default []).
    assert payload["profile_refs"] == []
    # [B2] ingredient_constraints always present and a list (default []); this
    # query mentions no ingredient family, so it is empty here.
    assert payload["ingredient_constraints"] == []
    # [A1] excluded_product_ids always present and a list (default []); this query
    # negates no product name, so it is empty here.
    assert payload["excluded_product_ids"] == []
    # [A2] excluded brand/category/group axes always present and lists (default []);
    # this query negates nothing, so all empty.
    assert payload["excluded_brand_ids"] == []
    assert payload["excluded_category_surfaces"] == []
    assert payload["excluded_category_groups"] == []


# ---------------------------------------------------------------------------
# [F4-c''] Profile-reference class selection (gate + prompt + fallback)
# ---------------------------------------------------------------------------


def test_prompt_advertises_profile_ref_schema_and_new_keys() -> None:
    from src.rec.query_understanding import PROFILE_REF_CLASSES, _build_system_prompt

    prompt = _build_system_prompt()
    # New output-schema keys are part of the JSON contract example.
    assert '"profile_refs": []' in prompt
    assert '"unresolved_terms": []' in prompt
    # Every closed class name is advertised so the LLM selects from the enum.
    for cls in PROFILE_REF_CLASSES:
        assert cls in prompt


def test_profile_refs_gate_keeps_enum_members_only() -> None:
    products = _products()
    fake = FakeLLMClient(_fake_json(profile_refs=["concerns", "not_a_class", "goals"]))
    interp = understand_query("내 고민이랑 목표에 맞는 거", products, llm=fake)
    assert interp.profile_refs == ["concerns", "goals"]  # out-of-enum dropped


def test_profile_refs_gate_dedups_and_caps_at_three() -> None:
    products = _products()
    fake = FakeLLMClient(
        _fake_json(profile_refs=["concerns", "concerns", "goals", "preferred_brands", "owned"])
    )
    interp = understand_query("x", products, llm=fake)
    # dedup then cap at 3 (owned drops off the end).
    assert interp.profile_refs == ["concerns", "goals", "preferred_brands"]


def test_profile_refs_default_empty_when_llm_omits_field() -> None:
    products = _products()
    interp = understand_query("보습 토너", products, llm=FakeLLMClient(_fake_json()))
    assert interp.profile_refs == []


def test_llm_declared_unresolved_terms_merged_and_capped() -> None:
    products = _products()
    # 6 declared terms (> the 5 cap) with an over-length item inside the first 5.
    fake = FakeLLMClient(
        _fake_json(unresolved_terms=["a1", "x" * 60, "a3", "a4", "a5", "a6"])
    )
    interp = understand_query("이니스프리 신제품", products, llm=fake)
    assert set(interp.unresolved_terms) == {"a1", "a3", "a4", "a5"}
    assert ("x" * 60) not in interp.unresolved_terms  # over-length dropped
    assert "a6" not in interp.unresolved_terms  # beyond the 5-item cap


def test_fallback_profile_refs_detects_possessive_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("내 고민에 맞는 토너", _products())  # llm=None → fallback
    assert interp.llm_used is False
    assert "concerns" in interp.profile_refs


def test_fallback_profile_refs_not_triggered_by_generic_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("피부에 맞는 스킨케어", _products())
    assert interp.llm_used is False
    assert interp.profile_refs == []  # no possessive marker → no false positive


def test_fallback_profile_refs_capped_at_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    # Hits concerns + goals + preferred_brands + repurchase (>3) → enum-order cap 3.
    interp = understand_query("내 고민 내 목표 좋아하는 브랜드 자주 사는 걸로", _products())
    assert interp.llm_used is False
    assert interp.profile_refs == ["concerns", "goals", "preferred_brands"]


# ---------------------------------------------------------------------------
# [B1] Ingredient alias layer end-to-end through understand_query. The alias
# layer lives in resolve_query_concepts, so both paths inherit it; these assert
# the query-understanding-level consequences (adoption, unresolved cleanup,
# LLM ingredients_wanted flowing through the SAME alias gate).
# ---------------------------------------------------------------------------


def _hyaluron_products() -> list[dict[str, Any]]:
    """Catalog carrying real 하이알루 tokens (what the alias map 히알루론 → INCI points
    at) — distinct from _products()'s concept:Ingredient:히알루론산, which the alias
    map does NOT point at, so the alias layer only fires when these are present."""
    return [
        _product(
            "P_hya",
            category_name="크림",
            category_concept_ids=["concept:Category:크림"],
            ingredient_concept_ids=[
                "concept:Ingredient:소듐하이알루로네이트",
                "concept:Ingredient:하이알루로닉애씨드",
            ],
        ),
    ]


def test_alias_fallback_adopts_and_clears_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dictionary fallback: the marquee 히알루론 query adopts the catalog INCI, and
    히알루론 does NOT also appear as an unresolved chip (the contradiction B1 fixes)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("히알루론 든거", _hyaluron_products())

    assert interp.llm_used is False
    ids = _ids(interp)
    assert "concept:Ingredient:소듐하이알루로네이트" in ids
    assert "concept:Ingredient:하이알루로닉애씨드" in ids
    assert not any("히알루론" in t for t in interp.unresolved_terms)


def test_alias_fallback_negation_avoids_family_no_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """"히알루론 없는 크림": no positive hyaluron adoption (resolution-level negation
    guard), and — because the alias layer now maps 히알루론 to catalog INCI inside
    the negation preprocessor — the family is recorded as AVOIDED with no warning."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("히알루론 없는 크림", _hyaluron_products())

    assert interp.llm_used is False
    assert not any(c.concept_type == "ingredient" for c in interp.resolved_concepts)
    assert set(interp.avoided_ingredient_concept_ids) == {
        "concept:Ingredient:소듐하이알루로네이트",
        "concept:Ingredient:하이알루로닉애씨드",
    }
    assert interp.warnings == []


def test_alias_llm_declared_surface_removed_from_unresolved() -> None:
    """The LLM re-declares 히알루론 in unresolved_terms, but the base query resolved it
    through the alias layer — it must be dropped from unresolved (no double state)."""
    fake = FakeLLMClient(_fake_json(unresolved_terms=["히알루론"]))
    interp = understand_query("히알루론 든거", _hyaluron_products(), llm=fake)

    assert interp.llm_used is True
    assert "concept:Ingredient:소듐하이알루로네이트" in _ids(interp)
    assert "히알루론" not in interp.unresolved_terms


def test_alias_llm_ingredients_wanted_flows_through_gate() -> None:
    """[recall] 히알루론 is absent from the raw query; the LLM supplies it as
    ingredients_wanted and it resolves through the SAME alias/catalog gate to the
    catalog INCI (the existing recall-expansion contract, now via the alias layer)."""
    fake = FakeLLMClient(_fake_json(ingredients_wanted=["히알루론"]))
    interp = understand_query("보습 크림 추천", _hyaluron_products(), llm=fake)

    ids = _ids(interp)
    assert "concept:Ingredient:소듐하이알루로네이트" in ids
    assert "concept:Ingredient:하이알루로닉애씨드" in ids
    assert "히알루론" not in interp.unresolved_terms


# ---------------------------------------------------------------------------
# [B2] IngredientConstraint building (성분군 grouping + provenance). Constraints
# are built AFTER avoided subtraction; only "raw" ones are hard-filter eligible.
# ---------------------------------------------------------------------------


_HYA_S = "concept:Ingredient:소듐하이알루로네이트"
_HYA_A = "concept:Ingredient:하이알루로닉애씨드"


def test_constraint_raw_provenance_from_query_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marquee query mentions 히알루론 verbatim → ONE family constraint,
    provenance="raw" (hard-filter eligible), grouping the whole 성분군 (all alias
    surfaces + every catalog-existing INCI of the family)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("히알루론 든거 뭐 좋은거 없나", _hyaluron_products())

    assert interp.llm_used is False
    assert len(interp.ingredient_constraints) == 1
    c = interp.ingredient_constraints[0]
    assert c.provenance == "raw"
    assert c.label == "히알루론"  # the 관용어 the user typed
    assert set(c.inci_concept_ids) == {_HYA_S, _HYA_A}  # whole catalog family (OR)
    # name_surfaces span the family's alias keys (관용어 + 오타 변형).
    assert {"히알루론산", "히알루론", "히아루론산"} <= set(c.name_surfaces)


def test_constraint_family_grouping_is_one_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two surfaces of the SAME family in one query (히알루론산 + 히알루론) collapse to a
    single constraint (they share the identical catalog INCI set)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("히알루론산 히알루론 크림", _hyaluron_products())
    assert len(interp.ingredient_constraints) == 1
    assert set(interp.ingredient_constraints[0].inci_concept_ids) == {_HYA_S, _HYA_A}


def test_constraint_llm_only_ingredient_is_llm_provenance() -> None:
    """The LLM adopts 히알루론 (ingredients_wanted) but the RAW query has no such
    surface → provenance="llm" (soft boost only, NOT hard-filter eligible). This is
    the existing recall-expansion behaviour, now classified as llm."""
    fake = FakeLLMClient(_fake_json(ingredients_wanted=["히알루론"]))
    interp = understand_query("보습 크림 추천", _hyaluron_products(), llm=fake)

    assert interp.llm_used is True
    assert "concept:Ingredient:소듐하이알루로네이트" in _ids(interp)  # still resolved (recall)
    assert len(interp.ingredient_constraints) == 1
    assert interp.ingredient_constraints[0].provenance == "llm"


def test_constraint_avoided_family_produces_no_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """기피 우선: an avoided family is subtracted from the positive concepts BEFORE
    constraint building, so a "히알루론 없는" query records the family as avoided and
    builds NO wanted constraint for it."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("히알루론 없는 크림", _hyaluron_products())
    assert interp.avoided_ingredient_concept_ids  # family recorded as avoided
    assert interp.ingredient_constraints == []  # and NOT as a wanted constraint


def test_constraint_bare_ingredient_singleton_is_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare INCI typed verbatim (레티놀, not an alias key mapping into this catalog)
    becomes a singleton constraint, provenance="raw" (the surface is in the query)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()  # carries concept:Ingredient:레티놀
    interp = understand_query("레티놀 세럼 궁금해요", products)
    retinol = [c for c in interp.ingredient_constraints
               if c.inci_concept_ids == ["concept:Ingredient:레티놀"]]
    assert len(retinol) == 1
    assert retinol[0].provenance == "raw"
    assert retinol[0].label == "레티놀"


def test_constraint_direct_inci_name_surfaces_include_typed_and_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[F3] A directly-typed INCI (레티놀) grouped into the 비타민A alias family must
    keep 레티놀 in name_surfaces (the typed surface + INCI suffix) — otherwise a
    name-only "레티놀 나이트 크림" (no structured ingredient) would be missed because
    name_surfaces held only the alias keys (비타민A/비타민에이) the user never typed."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _products()  # carries concept:Ingredient:레티놀 (→ 비타민A alias family)
    interp = understand_query("레티놀 든거", products)
    retinol = [c for c in interp.ingredient_constraints
               if "concept:Ingredient:레티놀" in c.inci_concept_ids]
    assert len(retinol) == 1
    assert retinol[0].label == "레티놀"  # user's typed surface, not an alias key
    assert "레티놀" in retinol[0].name_surfaces  # F3: typed surface / INCI suffix present
    # The name-fallback surface actually matches a structure-less product by name.
    from src.rec.ingredient_constraint import match_ingredient_constraint
    name_only = {"product_id": "P", "ingredient_ids": [], "ingredient_concept_ids": [],
                 "representative_product_name": "레티놀 나이트 크림"}
    assert match_ingredient_constraint(name_only, retinol[0]) == "name"


# ---------------------------------------------------------------------------
# [A3] Ingredient strength (required/preferred) — the "있으면 더 좋고" slot + the
# strength-source threading through the concept_map flatten.
# ---------------------------------------------------------------------------


def _hya_of(interp: QueryInterpretation) -> Any:
    """The single hyaluron family constraint (tests below build exactly one)."""
    hya = [
        c for c in interp.ingredient_constraints
        if "concept:Ingredient:소듐하이알루로네이트" in c.inci_concept_ids
    ]
    assert len(hya) == 1, interp.ingredient_constraints
    return hya[0]


def test_a3_preferred_slot_classifies_constraint_preferred() -> None:
    """The LLM routes a family to ingredients_preferred → strength="preferred" even
    though the raw query surfaces it (raw-floor default of required must NOT override
    an explicit preferred classification). provenance stays "raw" (the surface is in
    the query); only the strength distinguishes it from a hard need."""
    fake = FakeLLMClient(_fake_json(ingredients_preferred=["히알루론"], desired_attributes=["보습"]))
    interp = understand_query("히알루론 들어있으면 더 좋고 보습 크림", _hyaluron_products(), llm=fake)
    c = _hya_of(interp)
    assert c.provenance == "raw"  # surface literally in the query
    assert c.strength == "preferred"  # ...but a preference, never a hard gate


def test_a3_wanted_slot_stays_required_regression() -> None:
    """ingredients_wanted keeps strength="required" (the existing hard-gate axis)."""
    fake = FakeLLMClient(_fake_json(ingredients_wanted=["히알루론"]))
    interp = understand_query("히알루론 든거", _hyaluron_products(), llm=fake)
    c = _hya_of(interp)
    assert c.strength == "required"


def test_a3_required_and_preferred_same_family_promotes_required() -> None:
    """A family named by BOTH slots resolves to required (required-wins), regardless
    of raw surface. Order in _POSITIVE_FIELDS ensures wanted is recorded first."""
    fake = FakeLLMClient(
        _fake_json(ingredients_wanted=["히알루론"], ingredients_preferred=["히알루론"])
    )
    interp = understand_query("보습 크림", _hyaluron_products(), llm=fake)
    assert _hya_of(interp).strength == "required"


def test_a3_raw_floor_default_is_required_llm_path() -> None:
    """A raw-surface family with NO explicit slot classification (the LLM omitted it,
    only the raw floor resolved it) defaults to strength="required"."""
    fake = FakeLLMClient(_fake_json())  # no ingredient slots at all
    interp = understand_query("히알루론 든거", _hyaluron_products(), llm=fake)
    c = _hya_of(interp)
    assert c.provenance == "raw" and c.strength == "required"


def test_a3_fallback_family_is_required() -> None:
    """Dictionary fallback (LLM off) has no preference slot → every family required
    (documented degradation)."""
    fake = FakeLLMClient(raises=RuntimeError("llm down"))
    interp = understand_query("히알루론 든거", _hyaluron_products(), llm=fake)
    assert interp.llm_used is False
    assert _hya_of(interp).strength == "required"


def test_a3_avoided_wins_over_preferred() -> None:
    """기피 우선: an avoided family produces NO constraint even if the LLM also puts it
    in ingredients_preferred (avoided > required > preferred; avoided subtraction runs
    before constraint building)."""
    fake = FakeLLMClient(_fake_json(ingredients_preferred=["히알루론"]))
    interp = understand_query("히알루론 없는 크림", _hyaluron_products(), llm=fake)
    assert interp.avoided_ingredient_concept_ids  # recorded as avoided
    assert interp.ingredient_constraints == []  # never a wanted/preferred constraint


def test_a3_to_dict_includes_strength() -> None:
    """The constraint to_dict carries the additive strength field (query-response
    shape contract)."""
    fake = FakeLLMClient(_fake_json(ingredients_preferred=["히알루론"]))
    interp = understand_query("히알루론 크림", _hyaluron_products(), llm=fake)
    payload = interp.to_dict()["ingredient_constraints"][0]
    assert payload["strength"] == "preferred"
    assert set(payload) == {"label", "inci_concept_ids", "name_surfaces", "provenance", "strength"}


def test_a3_prompt_advertises_preferred_slot() -> None:
    from src.rec.query_understanding import _build_system_prompt

    prompt = _build_system_prompt()
    assert '"ingredients_preferred": []' in prompt
    assert "ingredients_preferred" in prompt


def test_a3_dup_term_in_earlier_slot_does_not_lose_preferred_strength() -> None:
    """[F1] The LLM puts the SAME family in a non-strength slot (desired_attributes)
    AND ingredients_preferred. The earlier slot consumes the adoption dedupe first;
    the strength signal must still be recorded from the preferred slot → the raw
    surface stays preferred (never promoted to the required raw-floor default →
    spurious hard gate)."""
    fake = FakeLLMClient(
        _fake_json(desired_attributes=["히알루론"], ingredients_preferred=["히알루론"])
    )
    interp = understand_query("히알루론 크림", _hyaluron_products(), llm=fake)
    c = _hya_of(interp)
    assert c.provenance == "raw"
    assert c.strength == "preferred"  # F1: NOT promoted to required


def test_a3_dup_wanted_and_preferred_still_promotes_required() -> None:
    """[F1] The inverse still holds: wanted + preferred on the same family → required
    (required-wins), regardless of dedupe order."""
    fake = FakeLLMClient(
        _fake_json(ingredients_wanted=["히알루론"], ingredients_preferred=["히알루론"])
    )
    interp = understand_query("히알루론 크림", _hyaluron_products(), llm=fake)
    assert _hya_of(interp).strength == "required"


def _alcohol_products() -> list[dict[str, Any]]:
    """Catalog carrying the volatile solvent (변성알코올) + a fatty alcohol (세틸알코올,
    deliberately NOT swept in by the alcohol alias)."""
    return [
        _product("P_denat", category_name="스킨케어",
                 category_concept_ids=["concept:Category:스킨케어"],
                 ingredient_concept_ids=["concept:Ingredient:변성알코올"]),
        _product("P_fatty", category_name="스킨케어",
                 category_concept_ids=["concept:Category:스킨케어"],
                 ingredient_concept_ids=["concept:Ingredient:세틸알코올"]),
    ]


def test_alcohol_negation_resolves_denatured_only_both_spellings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[2026-07-23] "알콜없는 스킨케어" AND its typo "알콜업는 스킨케어" both resolve the
    volatile-solvent family (변성알코올) as AVOIDED — not fatty alcohols — with no
    positive/wanted ingredient and no dangling 알콜/알코올 chip. Live dictionary
    fallback (no LLM), exercising the YAML alias + the 업는 typo marker together."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    products = _alcohol_products()
    for query in ("알콜없는 스킨케어", "알콜업는 스킨케어"):
        interp = understand_query(query, products)
        assert interp.llm_used is False, query
        # denatured solvent only (fatty 세틸알코올 NOT included) ...
        assert interp.avoided_ingredient_concept_ids == ["concept:Ingredient:변성알코올"], query
        # ... not flipped to a positive/wanted ingredient (no hard-filter) ...
        assert not any(c.concept_type == "ingredient" for c in interp.resolved_concepts), query
        assert interp.ingredient_constraints == [], query
        # ... and no dangling 알콜/알코올 unresolved chip.
        assert not any("알콜" in t or "알코올" in t for t in interp.unresolved_terms), query


def test_llm_typo_negation_blob_dropped_from_unresolved() -> None:
    """[2026-07-23] The LLM emits the WHOLE typo token '알콜업는' as unresolved, but
    the raw-query negation already resolved '알콜' → 변성알코올 (AVOIDED). The blob must
    not linger as a "미해석" chip (an already-applied avoidance is not an unmapped
    expression); the avoidance itself is preserved."""
    products = _alcohol_products()
    fake = FakeLLMClient(_fake_json(unresolved_terms=["알콜업는"]))
    interp = understand_query("알콜업는 스킨케어", products, llm=fake)

    assert "concept:Ingredient:변성알코올" in interp.avoided_ingredient_concept_ids
    # '알콜' ⊂ '알콜업는' and '알콜' resolved to an avoided id → the blob is dropped.
    assert not any("알콜" in t for t in interp.unresolved_terms)


def test_llm_unmapped_negation_blob_kept_in_unresolved() -> None:
    """Over-deletion guard: a negation of an UNMAPPED ingredient (제라늄 — not in the
    catalog/alias) did NOT resolve to an avoided id, so its surface is never a drop
    key and the honest unmapped chip stays (no over-deletion)."""
    products = _alcohol_products()
    fake = FakeLLMClient(_fake_json(unresolved_terms=["제라늄업는"]))
    interp = understand_query("제라늄업는 크림", products, llm=fake)

    assert interp.avoided_ingredient_concept_ids == []  # 제라늄 not resolvable
    assert any("제라늄" in t for t in interp.unresolved_terms)  # honestly retained


# ---------------------------------------------------------------------------
# [2026-07-23] Tier 3 reverse-containment → constraint building. A colloquial
# expression ('콜라겐') that resolves MULTIPLE INCI must be ONE OR-constraint (not
# per-id AND singletons), and its avoided twin ("콜라겐 없는") avoids all of them.
# ---------------------------------------------------------------------------


_COLLAGEN_SOL = "concept:Ingredient:솔루블콜라겐"
_COLLAGEN_HYD = "concept:Ingredient:하이드롤라이즈드콜라겐"


def _collagen_products() -> list[dict[str, Any]]:
    return [
        _product("P_col", category_name="크림", category_concept_ids=["concept:Category:크림"],
                 ingredient_concept_ids=[_COLLAGEN_SOL, _COLLAGEN_HYD]),
    ]


def test_constraint_reverse_tier_is_single_or_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """'콜라겐' (single-word, dict fallback) resolves BOTH catalog collagen INCI via
    Tier 3 → exactly ONE constraint whose inci are OR'd, so a product carrying only
    ONE of them passes (an AND of two singletons would have failed it — the bug)."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    from src.rec.ingredient_constraint import match_ingredient_constraint
    interp = understand_query("콜라겐", _collagen_products())

    assert len(interp.ingredient_constraints) == 1
    c = interp.ingredient_constraints[0]
    assert c.label == "콜라겐"
    assert set(c.inci_concept_ids) == {_COLLAGEN_SOL, _COLLAGEN_HYD}
    # OR semantics: a 솔루블콜라겐-ONLY product satisfies the single constraint.
    only_sol = {"product_id": "X", "ingredient_ids": [], "ingredient_concept_ids": [_COLLAGEN_SOL],
                "representative_product_name": ""}
    assert match_ingredient_constraint(only_sol, c) == "ingredient"


def test_avoided_reverse_tier_collagen_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """기피 auto-benefit: "콜라겐 없는 크림" avoids BOTH collagen INCI (the negation term
    '콜라겐' flows through the same Tier 3 resolution) with no positive concept."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("콜라겐 없는 크림", _collagen_products())
    assert set(interp.avoided_ingredient_concept_ids) == {_COLLAGEN_SOL, _COLLAGEN_HYD}
    assert not any(c.concept_type == "ingredient" for c in interp.resolved_concepts)


def test_constraint_direct_inci_singleton_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a directly-typed INCI ('레티놀', matched_text unique) is still ONE
    singleton constraint — the matched_text grouping did not change this case."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    interp = understand_query("레티놀 든거", _products())  # _products() carries 레티놀
    retinol = [c for c in interp.ingredient_constraints
               if "concept:Ingredient:레티놀" in c.inci_concept_ids]
    assert len(retinol) == 1
    assert retinol[0].inci_concept_ids == ["concept:Ingredient:레티놀"]
    assert retinol[0].label == "레티놀"


def test_constraint_family_plus_specific_inci_no_false_and_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[F2 codex repro] "히알루론 소듐하이알루로네이트" names BOTH the family (히알루론)
    and a specific member. The (type,id) dedupe pins the shared 소듐 to the bare
    expression's group, so the 히알루론 constraint must be filled with the FULL family
    catalog signature (OR) — otherwise a 소듐-only product is falsely rejected by an
    AND of {소듐}·{하이알루로닉}."""
    monkeypatch.delenv("GRAPHRAPPING_QUERY_LLM", raising=False)
    from src.rec.ingredient_constraint import product_passes_constraints

    sod = "concept:Ingredient:소듐하이알루로네이트"
    acid = "concept:Ingredient:하이알루로닉애씨드"
    products = [
        _product("SOD", ingredient_concept_ids=[sod]),
        _product("ACID", ingredient_concept_ids=[acid]),
    ]
    interp = understand_query("히알루론 소듐하이알루로네이트", products)
    cons = interp.ingredient_constraints
    # The family (히알루론) constraint carries BOTH catalog siblings as an OR.
    fam = [c for c in cons if c.label == "히알루론"]
    assert fam and set(fam[0].inci_concept_ids) == {sod, acid}
    # A 소듐-only product satisfies EVERY constraint (no false AND rejection).
    sod_only = _product("X", ingredient_concept_ids=[sod])
    assert product_passes_constraints(sod_only, cons) is True


def test_reverse_tier_llm_wanted_provenance_rule() -> None:
    """The LLM adopts '콜라겐' as ingredients_wanted; provenance follows the raw-surface
    rule — "raw" when the expression is in the query, "llm" when only the LLM adds it."""
    products = _collagen_products()
    raw_hit = understand_query(
        "콜라겐 크림 추천", products, llm=FakeLLMClient(_fake_json(ingredients_wanted=["콜라겐"])))
    llm_only = understand_query(
        "보습 크림 추천", products, llm=FakeLLMClient(_fake_json(ingredients_wanted=["콜라겐"])))

    raw_c = [c for c in raw_hit.ingredient_constraints if c.label == "콜라겐"]
    llm_c = [c for c in llm_only.ingredient_constraints if c.label == "콜라겐"]
    assert len(raw_c) == 1 and raw_c[0].provenance == "raw"  # '콜라겐' present in query
    assert len(llm_c) == 1 and llm_c[0].provenance == "llm"  # only the LLM supplied it
    # Both are single OR-constraints regardless of provenance.
    assert set(raw_c[0].inci_concept_ids) == {_COLLAGEN_SOL, _COLLAGEN_HYD}


# ---------------------------------------------------------------------------
# Real provider smoke test (only runs when a provider env is configured)
# ---------------------------------------------------------------------------

_AZURE_KEYS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
)
_HAS_AZURE = all(os.environ.get(k) for k in _AZURE_KEYS)
_HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(
    not (_HAS_AZURE or _HAS_ANTHROPIC),
    reason="no query LLM provider env configured",
)
def test_real_llm_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_QUERY_LLM", "azure" if _HAS_AZURE else "anthropic")
    interp = understand_query("지성 피부에 맞는 순한 토너 추천해줘", _products())
    assert isinstance(interp, QueryInterpretation)
    assert interp.query


# ---------------------------------------------------------------------------
# Search-absorption A1: product axis interpretation — excluded_product_ids field,
# _negated_products, brand-contradiction guard (post-merge), LLM product_names slot.
# ---------------------------------------------------------------------------

from src.rec.query_understanding import (  # noqa: E402
    _apply_brand_product_guard,
    _build_negation_index,
    _negated_products,
)
from src.rec.search import MatchedConcept  # noqa: E402


def _named_products() -> list[dict[str, Any]]:
    """Two 설화수 essences (name axis) + one 헤라 product (a different brand)."""
    return [
        _product("50165", representative_product_name="설화수 윤조에센스",
                 brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"]),
        _product("50166", representative_product_name="설화수 윤조에센스 미스트",
                 brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"]),
        _product("70001", representative_product_name="헤라 블랙 쿠션",
                 brand_name="헤라", brand_concept_ids=["concept:Brand:헤라"]),
    ]


def test_negated_products_resolves_excluded_ids():
    """_negated_products resolves the negated surface (in isolation) via the product
    axis — '윤조에센스 빼고' excludes BOTH the essence and its mist variant."""
    index = _build_negation_index(_named_products())
    excluded, consumed, _spans = _negated_products("설화수 윤조에센스 빼고 다른 에센스", index)
    assert set(excluded) == {"50165", "50166"}
    assert "윤조에센스" in consumed  # group-1 surface reported for F7


def test_negated_products_empty_without_marker():
    index = _build_negation_index(_named_products())
    excluded, consumed, _spans = _negated_products("설화수 윤조에센스 어때", index)
    assert excluded == [] and consumed == set()


def test_negated_products_span_multiword_name():
    """[F2b] Span-based: '헤라 블랙 쿠션 빼고' excludes the product even though the
    regex captures only the single token '쿠션'."""
    products = _named_products()
    products.append(_product("70002", representative_product_name="헤라 블랙 쿠션",
                             brand_name="헤라", brand_concept_ids=["concept:Brand:헤라"]))
    excluded, _consumed, _spans = _negated_products("헤라 블랙 쿠션 빼고 다른거", _build_negation_index(products))
    assert "70002" in excluded


def test_negated_products_malgo_marker():
    """[F2a] '말고' is now a recognised negation marker."""
    index = _build_negation_index(_named_products())
    excluded, _consumed, _spans = _negated_products("설화수 윤조에센스 말고", index)
    assert set(excluded) == {"50165", "50166"}


def test_fallback_excluded_products_populated_and_subtracted():
    """The dictionary fallback populates excluded_product_ids AND keeps the negated
    products out of the positive concepts (subtraction + resolution guard)."""
    interp = understand_query("설화수 윤조에센스 빼고 추천", _named_products(), llm=None)
    assert interp.llm_used is False
    assert set(interp.excluded_product_ids) == {"50165", "50166"}
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert product_ids == set()  # negated products are not positive
    # [A2 precedence] A product-NAME negation must NOT also be read as a brand
    # exclusion just because the span "설화수 윤조에센스" contains the brand token
    # "설화수" — the product axis claims the span first (product > brand). If this
    # regressed, the whole 설화수 brand would be wiped.
    assert interp.excluded_brand_ids == []


def test_llm_product_negation_does_not_exclude_brand():
    """[A2 precedence — LLM twin] The raw-query product-name negation is detected on
    BOTH paths; on the LLM path too, "설화수 윤조에센스 빼고" is a product exclusion, not
    a 설화수 brand exclusion (product > brand precedence)."""
    fake = FakeLLMClient(_fake_json(intent="recommend"))  # empty slots; raw span drives it
    interp = understand_query("설화수 윤조에센스 빼고 추천", _named_products(), llm=fake)
    assert interp.llm_used is True
    assert set(interp.excluded_product_ids) == {"50165", "50166"}
    assert interp.excluded_brand_ids == []  # brand 설화수 NOT wiped


def test_fallback_forward_pin_resolved_positive():
    """A named product (no negation) is a positive product concept in the fallback."""
    interp = understand_query("설화수 윤조에센스 어때", _named_products(), llm=None)
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert "50165" in product_ids
    assert interp.excluded_product_ids == []


def test_brand_guard_drops_mismatched_product():
    """Post-merge brand guard: when a brand concept is present, a product of a
    DIFFERENT brand is dropped; a matching-brand product is kept."""
    products = _named_products()
    resolved = [
        MatchedConcept("brand", "concept:Brand:설화수", "설화수", "설화수"),
        MatchedConcept("product", "50165", "설화수 윤조에센스", "설화수 윤조에센스"),  # 설화수 → keep
        MatchedConcept("product", "70001", "헤라 블랙 쿠션", "헤라 블랙 쿠션"),        # 헤라 → drop
    ]
    kept = _apply_brand_product_guard(resolved, products)
    kept_products = {c.concept_id for c in kept if c.concept_type == "product"}
    assert kept_products == {"50165"}  # 헤라 product removed as brand-contradictory
    # The brand concept itself is untouched.
    assert any(c.concept_type == "brand" for c in kept)


def test_brand_guard_noop_without_brand_in_query():
    """No brand concept in the query → guard is a pure no-op (product kept)."""
    products = _named_products()
    resolved = [MatchedConcept("product", "70001", "헤라 블랙 쿠션", "헤라 블랙 쿠션")]
    assert _apply_brand_product_guard(resolved, products) == resolved


def test_llm_product_names_slot_resolves_product_concept():
    """The LLM ``product_names`` slot rides the existing per-term gate (already in
    _POSITIVE_FIELDS): '윤조에센스' is validated through resolve_query_concepts and
    adopted as a product concept — confirming the automatic-benefit path."""
    fake = FakeLLMClient(_fake_json(product_names=["윤조에센스"]))
    interp = understand_query("에센스 추천해줘", _named_products(), llm=fake)
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    # '윤조에센스' reverse-matches both the essence and the mist variant by name.
    assert product_ids == {"50165", "50166"}


def test_llm_brand_guard_applied_after_merge():
    """Brand guard runs AFTER the raw+LLM merge: the LLM emits a product of a
    different brand (whose NAME does not carry its brand, so the brand is not
    co-resolved) while the query resolves brand 설화수 → the product is dropped."""
    products = _named_products()
    # A 헤라 product whose NAME omits the brand token, so resolving its name yields
    # ONLY the product concept (not brand 헤라) — isolating the guard.
    products.append(_product("70002", representative_product_name="블랙 쿠션 21호",
                             brand_name="헤라", brand_concept_ids=["concept:Brand:헤라"]))
    fake = FakeLLMClient(_fake_json(brands=["설화수"], product_names=["블랙 쿠션 21호"]))
    interp = understand_query("설화수 제품", products, llm=fake)
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert product_ids == set()  # 헤라 product contradicts the resolved 설화수 brand
    # The resolved brand concept survives (only the product was dropped).
    assert any(c.concept_type == "brand" for c in interp.resolved_concepts)


def test_llm_brand_slot_does_not_pin_all_brand_products():
    """[regression] A bare brand term ('설화수') resolved through the per-term gate
    must NOT reverse-pin every 설화수-prefixed product by name — the brand browse
    intent wins over the product axis."""
    fake = FakeLLMClient(_fake_json(brands=["설화수"]))
    interp = understand_query("설화수 신제품", _named_products(), llm=fake)
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert product_ids == set()  # brand resolved, but no per-product pins
    assert any(c.concept_type == "brand" for c in interp.resolved_concepts)


def test_llm_ingredients_wanted_slot_does_not_pin_product():
    """[F3] ingredients_wanted resolves ONLY the ingredient axis — a product NAMED
    after the ingredient ("콜라겐 크림") is not pinned as a product."""
    products = [
        _product("C1", representative_product_name="콜라겐 크림",
                 ingredient_concept_ids=["concept:Ingredient:솔루블콜라겐"]),
    ]
    fake = FakeLLMClient(_fake_json(ingredients_wanted=["콜라겐"]))
    interp = understand_query("콜라겐 추천", products, llm=fake)
    types = {c.concept_type for c in interp.resolved_concepts}
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert "ingredient" in types  # 콜라겐 adopted on the ingredient axis
    assert product_ids == set()  # NOT pinned as a product (F3 type filter)


def test_llm_product_names_slot_does_not_inject_brand():
    """[F3] product_names resolves ONLY the product axis — a brand the term also
    matches ("설화수" ⊂ "설화수 윤조에센스") is NOT injected from this slot, so it
    cannot neutralise the brand-contradiction guard."""
    fake = FakeLLMClient(_fake_json(product_names=["설화수 윤조에센스"]))
    interp = understand_query("에센스", _named_products(), llm=fake)  # floor has no brand
    brands = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "brand"}
    product_ids = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"}
    assert "50165" in product_ids  # product adopted
    assert brands == set()  # brand not leaked from the product_names slot


def test_product_negation_does_not_emit_ingredient_warning():
    """[F7] A negation resolved on the PRODUCT axis ("헤라 블랙 쿠션 빼고") is not also
    flagged as a failed INGREDIENT negation — no spurious '쿠션' warning/chip."""
    products = _named_products()
    products.append(_product("70002", representative_product_name="헤라 블랙 쿠션",
                             brand_name="헤라", brand_concept_ids=["concept:Brand:헤라"]))
    interp = understand_query("헤라 블랙 쿠션 빼고 추천", products, llm=None)
    assert "70002" in interp.excluded_product_ids
    assert not any("쿠션" in w for w in interp.warnings)
    assert "쿠션" not in interp.unresolved_terms


# ---------------------------------------------------------------------------
# Search-absorption A2: polarity generalized to brand / category axes —
# excluded_brand_ids / excluded_category_ids / excluded_category_groups,
# _negated_brands / _negated_categories, LLM exclusion slots, non-cancellation.
# ---------------------------------------------------------------------------

from src.rec.query_understanding import (  # noqa: E402
    _negated_brands,
    _negated_categories,
    _resolve_excluded_category,
)


def _a2_products() -> list[dict[str, Any]]:
    """Brand-exclusion + literal-subtype + group-fallback fixture.

    - 이니스프리 보습크림 (brand to exclude) + a라네즈 rival 보습크림.
    - 설화수 선크림 with the COMPOUND catalog label "선크림 & 선블럭" (literal subtype).
    - 설화수 윤조세럼 (skincare essence — a 세럼/skincare-group carrier, deepest label
      "에센스" so it does NOT literal-match "스킨케어").
    - a makeup lipstick (out of skincare group)."""
    return [
        _product("P_inni", representative_product_name="이니스프리 보습크림",
                 brand_name="이니스프리", brand_concept_ids=["concept:Brand:이니스프리"],
                 category_name="크림", category_concept_ids=["concept:Category:크림"]),
        _product("P_rival", representative_product_name="라네즈 보습크림",
                 brand_name="라네즈", brand_concept_ids=["concept:Brand:라네즈"],
                 category_name="크림", category_concept_ids=["concept:Category:크림"]),
        _product("P_sun", representative_product_name="설화수 선크림",
                 brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"],
                 category_name="선크림 & 선블럭",
                 category_concept_ids=["concept:Category:선크림 & 선블럭"]),
        _product("P_serum", representative_product_name="설화수 윤조세럼",
                 brand_name="설화수", brand_concept_ids=["concept:Brand:설화수"],
                 category_name="에센스", category_concept_ids=["concept:Category:에센스"]),
        _product("P_lip", representative_product_name="릴리 립스틱",
                 brand_name="릴리", brand_concept_ids=["concept:Brand:릴리"],
                 category_name="립스틱", category_concept_ids=["concept:Category:립스틱"]),
    ]


def test_negated_brands_resolves_excluded_brand():
    index = _build_negation_index(_a2_products())
    excluded, consumed, _spans = _negated_brands("이니스프리 말고 보습크림", index)
    assert excluded == ["concept:Brand:이니스프리"]
    assert "이니스프리" in consumed  # F7 surface consumed


def test_negated_brands_empty_without_marker():
    index = _build_negation_index(_a2_products())
    excluded, consumed, _spans = _negated_brands("이니스프리 보습크림", index)
    assert excluded == [] and consumed == set()


def test_negated_brands_strict_equality_no_substring():
    """[F1] STRICT: a span that merely CONTAINS a brand token ("이니스프리 선크림") is
    NOT a brand exclusion — the 이니스프리 brand must not be wiped."""
    index = _build_negation_index(_a2_products())
    excluded, _consumed, _spans = _negated_brands("이니스프리 선크림 빼고 세럼", index)
    assert excluded == []


def test_resolve_excluded_category_literal_subtype_inclusion():
    """Layer 1: 표현⊂라벨 — "선크림" ⊂ "선크림 & 선블럭" → the SURFACE (not an id)."""
    index = _build_negation_index(_a2_products())
    surfaces, groups = _resolve_excluded_category("선크림", index)
    assert surfaces == ["선크림"]
    assert groups == []


def test_resolve_excluded_category_group_fallback():
    """Layer 0: "스킨케어" is a whole-group label → group exclusion (universe reconstruct)."""
    index = _build_negation_index(_a2_products())
    surfaces, groups = _resolve_excluded_category("스킨케어", index)
    assert surfaces == []
    assert groups == ["skincare"]


def test_negated_categories_literal_first():
    index = _build_negation_index(_a2_products())
    surfaces, groups, consumed, _spans = _negated_categories("선크림 빼고 세럼", index)
    assert surfaces == ["선크림"]  # literal surface, not a concept id
    assert groups == []  # literal matched → no group fallback
    assert "선크림" in consumed


def test_negated_categories_group_fallback():
    index = _build_negation_index(_a2_products())
    surfaces, groups, _consumed, _spans = _negated_categories("스킨케어 빼고", index)
    assert surfaces == []
    assert groups == ["skincare"]


def test_fallback_brand_exclusion_subtracts_positive():
    """"이니스프리 말고 보습크림": brand excluded + dropped from positive; the keyword
    survives (하드 배제는 브랜드에만, 키워드 유지)."""
    interp = understand_query("이니스프리 말고 보습크림", _a2_products(), llm=None)
    assert interp.excluded_brand_ids == ["concept:Brand:이니스프리"]
    brands = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "brand"}
    assert "concept:Brand:이니스프리" not in brands  # negative wins
    # A positive keyword/category still resolved (보습/크림) → not an empty interp.
    assert interp.resolved_concepts


def test_fallback_literal_category_group_positive_not_cancelled():
    """[non-cancellation] "선크림 빼고 세럼": skincare GROUP stays positive (from 세럼)
    while the LITERAL 선크림 category is excluded — concept-id level negative-wins."""
    interp = understand_query("선크림 빼고 세럼", _a2_products(), llm=None)
    assert interp.excluded_category_surfaces == ["선크림"]  # surface, not a concept id
    assert interp.excluded_category_groups == []
    positive = {(c.concept_type, c.concept_id) for c in interp.resolved_concepts}
    assert ("category", "concept:Category:skincare") in positive  # group양성 유지
    assert ("category", "concept:Category:선크림 & 선블럭") not in positive


def test_fallback_group_exclusion_subtracts_group_concept():
    """"스킨케어 빼고": group fallback + the positive skincare group concept subtracted
    (negative wins at the group concept-id level)."""
    interp = understand_query("스킨케어 빼고", _a2_products(), llm=None)
    assert interp.excluded_category_groups == ["skincare"]
    groups = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "category"}
    assert "concept:Category:skincare" not in groups


def test_fallback_malgo_marker_brand_exclusion():
    """[fallback marker] '말고' registers as a negation marker on the brand axis."""
    interp = understand_query("이니스프리 말고 다른거", _a2_products(), llm=None)
    assert interp.excluded_brand_ids == ["concept:Brand:이니스프리"]


def test_brand_exclusion_no_ingredient_warning():
    """[F7] A negated brand does not also raise a spurious 'not an ingredient' warning."""
    interp = understand_query("이니스프리 말고 보습크림", _a2_products(), llm=None)
    assert not any("이니스프리" in w for w in interp.warnings)
    assert "이니스프리" not in interp.unresolved_terms


def test_llm_brands_excluded_slot():
    """LLM slot path: brands_excluded resolved through the catalog gate."""
    fake = FakeLLMClient(_fake_json(brands_excluded=["이니스프리"], desired_attributes=["보습"]))
    interp = understand_query("보습크림 추천", _a2_products(), llm=fake)
    assert interp.excluded_brand_ids == ["concept:Brand:이니스프리"]


def test_llm_categories_excluded_slot_literal_and_group():
    """LLM slot path: categories_excluded uses the 2-layer literal/group resolution."""
    fake = FakeLLMClient(_fake_json(categories_excluded=["선크림"]))
    interp = understand_query("세럼 추천", _a2_products(), llm=fake)
    assert interp.excluded_category_surfaces == ["선크림"]

    fake2 = FakeLLMClient(_fake_json(categories_excluded=["스킨케어"]))
    interp2 = understand_query("추천", _a2_products(), llm=fake2)
    assert interp2.excluded_category_groups == ["skincare"]


def test_llm_unresolved_excluded_slot_surfaced():
    """A brands_excluded term that resolves to no brand is surfaced as unresolved
    (honest), never a forged exclusion."""
    fake = FakeLLMClient(_fake_json(brands_excluded=["존재하지않는브랜드zzz"]))
    interp = understand_query("보습크림", _a2_products(), llm=fake)
    assert interp.excluded_brand_ids == []
    assert "존재하지않는브랜드zzz" in interp.unresolved_terms


def test_llm_slot_union_with_raw_span():
    """The raw-query '말고' span and the LLM brands_excluded slot union (dedup)."""
    fake = FakeLLMClient(_fake_json(brands_excluded=["이니스프리"]))
    interp = understand_query("이니스프리 말고 보습크림", _a2_products(), llm=fake)
    assert interp.excluded_brand_ids == ["concept:Brand:이니스프리"]  # one, not duplicated


def test_positive_and_negative_brand_negation_wins():
    """양성∧부정 동시 언급: even if the brand is also positively resolvable, the
    exclusion wins (the brand is subtracted from the positive concepts)."""
    # "이니스프리 이니스프리 말고" — brand mentioned then negated; negation wins.
    interp = understand_query("이니스프리 말고 보습크림", _a2_products(), llm=None)
    brands = {c.concept_id for c in interp.resolved_concepts if c.concept_type == "brand"}
    assert "concept:Brand:이니스프리" not in brands
    assert "concept:Brand:이니스프리" in interp.excluded_brand_ids


def test_llm_brand_slot_dropped_when_consumed_by_product_span():
    """[F2] An LLM brands_excluded term INSIDE a span a higher raw axis already claimed
    is dropped: raw "설화수 윤조에센스 빼고" is a PRODUCT exclusion, so brands_excluded=
    ["설화수"] must NOT expand it into a whole-brand exclusion."""
    fake = FakeLLMClient(_fake_json(brands_excluded=["설화수"]))
    interp = understand_query("설화수 윤조에센스 빼고 추천", _named_products(), llm=fake)
    assert set(interp.excluded_product_ids) == {"50165", "50166"}
    assert interp.excluded_brand_ids == []  # 설화수 brand NOT wiped (consumed-span drop)


def test_iter_negation_spans_occurrence_based():
    """[F6] Two DISTINCT negations ending in the SAME group-1 token are both kept
    (occurrence-based iteration, not deduped on group1)."""
    from src.rec.query_understanding import _iter_negation_spans
    spans = _iter_negation_spans("헤라 쿠션 빼고 설화수 쿠션 빼고")
    assert [g1 for g1, _cands in spans] == ["쿠션", "쿠션"]  # both occurrences kept


def test_negated_categories_gita_maps_to_other_group():
    """[F8] "기타 빼고" resolves to the OTHER group (기타 is a group label), not caught as
    an incidental "스킨케어기타"-style literal."""
    index = _build_negation_index(_a2_products())
    surfaces, groups, _consumed, _spans = _negated_categories("기타 빼고", index)
    assert groups == ["other"]
    assert surfaces == []
