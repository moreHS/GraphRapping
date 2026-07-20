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
    }
    assert payload["intent"] == "recommend"
    assert payload["llm_used"] is True
    assert isinstance(payload["resolved_concepts"], list)
    assert all("concept_id" in c for c in payload["resolved_concepts"])
    # Frontend contract: warnings is always present and a list (default []).
    assert payload["warnings"] == []
    # [F4-c''] profile_refs always present and a list (default []).
    assert payload["profile_refs"] == []


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
