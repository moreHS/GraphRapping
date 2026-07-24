"""Unit tests for the A5 retrieval-evaluation harness (scripts/eval_retrieval.py).

Plan: fable_doc/plans/2026-07-23_search_absorption.md §A5.

These test the harness's PURE logic with synthetic products — no pipeline load,
no LLM call: the intent-faithful judgment engine (gain 2/1/0 + violations +
profile-brand), the metrics (ExactHit / RelevantHit with denominators, nDCG,
required_satisfaction, preference_reflected, false_zero), the gold-assembly
slot→raw mapping, the per-surface gold reference (F6), the holdout filter, and
slot P/R counting. The only LLM touchpoint (the live ``--run llm`` pass) is
exercised via the baseline script, not here (the plan mandates mocking LLM).
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import eval_retrieval as ev
from src.rec.ingredient_constraint import IngredientConstraint


# ---------------------------------------------------------------------------
# Synthetic catalog
# ---------------------------------------------------------------------------


def _product(
    pid: str,
    name: str = "",
    brand: str = "",
    category: str = "",
    ingredient_concept_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "product_id": pid,
        "product_name": name,
        "representative_product_name": name,
        "brand_name": brand,
        "brand_concept_ids": [f"concept:Brand:{brand}"] if brand else [],
        "category_name": category,
        "category_concept_ids": [f"concept:Category:{category}"] if category else [],
        "ingredient_concept_ids": ingredient_concept_ids or [],
        "ingredient_ids": [],
    }


_NO_CTX = ev.JudgmentContext()


def _item(stratum: str, gold: dict[str, Any], judgment: dict[str, Any], mode: str = "anon") -> ev.EvalQuery:
    return ev.EvalQuery(
        id="qX", stratum=stratum, query="q", mode=mode, holdout=False, gold=gold, judgment=judgment
    )


# ---------------------------------------------------------------------------
# Judgment engine — intent faithful (F1)
# ---------------------------------------------------------------------------


def test_expected_top_via_product_ids():
    products = [_product("P1", brand="설화수"), _product("P2", brand="설화수")]
    caches = ev.JudgmentCaches(products=products)
    judgment = {"expected_top": {"product_ids": ["P1"]}, "relevant": {"brand": "설화수"}}
    gains = ev.compute_gains(products, judgment, caches, _NO_CTX)
    assert gains["P1"] == 2  # expected beats relevant
    assert gains["P2"] == 1


def test_expected_top_name_tokens_are_AND():
    # A gain-2 requires ALL tokens — "워터뱅크" alone (a sunscreen) is NOT gain 2.
    products = [
        _product("P1", name="워터뱅크 수분 크림"),
        _product("P2", name="워터뱅크 선크림"),  # contains 워터뱅크 but not 수분크림
    ]
    caches = ev.JudgmentCaches(products=products)
    judgment = {"expected_top": {"product_name_contains": ["워터뱅크", "수분"]}}
    gains = ev.compute_gains(products, judgment, caches, _NO_CTX)
    assert gains["P1"] == 2
    assert gains["P2"] == 0


def test_relevant_and_combines_brand_and_category_surface():
    caches = ev.JudgmentCaches(products=[])
    judgment = {"relevant": {"brand": "헤라", "category_surface": ["쿠션"]}}
    hit = _product("P1", brand="헤라", category="쿠션")
    miss_cat = _product("P2", brand="헤라", category="립스틱")
    miss_brand = _product("P3", brand="설화수", category="쿠션")
    assert ev.product_satisfies_relevant(hit, judgment["relevant"], caches, _NO_CTX)
    assert not ev.product_satisfies_relevant(miss_cat, judgment["relevant"], caches, _NO_CTX)
    assert not ev.product_satisfies_relevant(miss_brand, judgment["relevant"], caches, _NO_CTX)


def test_profile_brand_relevant():
    ctx = ev.JudgmentContext(profile_brand_ids=frozenset({"concept:Brand:설화수"}))
    caches = ev.JudgmentCaches(products=[])
    relevant = {"profile_brand": True, "category_group": "skincare"}
    on_profile = _product("P1", brand="설화수", category="크림")
    off_profile = _product("P2", brand="헤라", category="크림")
    assert ev.product_satisfies_relevant(on_profile, relevant, caches, ctx)
    assert not ev.product_satisfies_relevant(off_profile, relevant, caches, ctx)
    # No profile context → profile_brand can never match.
    assert not ev.product_satisfies_relevant(on_profile, relevant, caches, _NO_CTX)


def test_violation_brand_ingredient_category():
    caches = ev.JudgmentCaches(products=[])
    caches._avoided["레티놀"] = {"concept:Ingredient:레티놀"}
    judgment = {
        "violations": {
            "must_not_brand": "이니스프리",
            "must_not_contain_ingredient": ["레티놀"],
            "must_not_category_group": ["makeup"],
            "must_not_category_surface": ["선크림"],
        }
    }
    assert ev.product_violates(_product("P1", brand="이니스프리"), judgment, caches)
    assert ev.product_violates(
        _product("P2", ingredient_concept_ids=["concept:Ingredient:레티놀"]), judgment, caches
    )
    assert ev.product_violates(_product("P3", category="립스틱"), judgment, caches)
    assert ev.product_violates(_product("P4", category="선크림 & 선블럭"), judgment, caches)
    assert not ev.product_violates(_product("P5", brand="헤라", category="에센스"), judgment, caches)


def test_must_contain_ingredient_via_injected_constraint():
    p = _product("P1", ingredient_concept_ids=["concept:Ingredient:히알루론산"])
    caches = ev.JudgmentCaches(products=[p])
    caches._required["히알루론"] = [
        IngredientConstraint(
            label="히알루론",
            inci_concept_ids=["concept:Ingredient:히알루론산"],
            name_surfaces=["히알루론"],
            provenance="raw",
        )
    ]
    relevant = {"must_contain_ingredient": ["히알루론"]}
    assert ev.product_satisfies_relevant(p, relevant, caches, _NO_CTX)
    other = _product("P2", ingredient_concept_ids=["concept:Ingredient:판테놀"])
    assert not ev.product_satisfies_relevant(other, relevant, caches, _NO_CTX)


# ---------------------------------------------------------------------------
# Metrics — hand-checked
# ---------------------------------------------------------------------------


def test_ndcg_matches_manual_calculation():
    # ranked gains [2, 0, 1]; ideal [2, 1, 0].
    gain_by_pid = {"a": 2, "b": 0, "c": 1}
    ranked = ["a", "b", "c"]
    # DCG = 2/1 + 0 + 1/log2(4) = 2.5 ; IDCG = 2/1 + 1/log2(3) = 2.63093
    assert ev.ndcg_at_k(ranked, gain_by_pid, 10) == pytest.approx(2.5 / 2.630930, abs=1e-4)


def test_ndcg_zero_when_no_gain():
    assert ev.ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, 10) == 0.0


def test_hit_at_k_floor():
    gain_by_pid = {"a": 1, "b": 2, "c": 0}
    # exact (floor 2): b is at rank 2 → hit@3 True, hit@1 False.
    assert ev.hit_at_k(["a", "b", "c"], gain_by_pid, 3, 2) is True
    assert ev.hit_at_k(["a", "b", "c"], gain_by_pid, 1, 2) is False
    # relevant (floor 1): a at rank 1 → hit@1 True.
    assert ev.hit_at_k(["a", "b", "c"], gain_by_pid, 1, 1) is True


def test_exact_vs_relevant_hit_denominators():
    # A relevant-only query (no gain-2 product) → exact_hit is None (not scored),
    # relevant_hit is scored. Guards against F2 (hidden denominator).
    products = [_product("P1", brand="헤라", category="쿠션"), _product("P2", brand="설화수")]
    caches = ev.JudgmentCaches(products=products)
    by_id = {p["product_id"]: p for p in products}
    item = _item("brand_category", {}, {"relevant": {"brand": "헤라", "category_surface": ["쿠션"]}})
    rec = ev.evaluate_results(["P1"], item, products, by_id, caches, _NO_CTX)
    assert rec["exact_hit@1"] is None  # no gain-2 possible → not scored
    assert rec["relevant_hit@1"] is True


def test_required_satisfaction_only_for_required():
    p1 = _product("P1", ingredient_concept_ids=["concept:Ingredient:히알루론산"])
    p2 = _product("P2", ingredient_concept_ids=["concept:Ingredient:판테놀"])
    products = [p1, p2]
    caches = ev.JudgmentCaches(products=products)
    caches._required["히알루론"] = [
        IngredientConstraint(
            label="히알루론", inci_concept_ids=["concept:Ingredient:히알루론산"],
            name_surfaces=["히알루론"], provenance="raw",
        )
    ]
    by_id = {"P1": p1, "P2": p2}
    judgment = {"relevant": {"must_contain_ingredient": ["히알루론"]}}
    # required-ingredient query → scored (1 of 2 carries).
    req_item = _item("ingredient_required", {"ingredients_required": ["히알루론"]}, judgment)
    rec = ev.evaluate_results(["P1", "P2"], req_item, products, by_id, caches, _NO_CTX)
    assert rec["required_satisfaction"] == pytest.approx(0.5)
    assert rec["preference_reflected"] is None
    # preferred-ingredient query → required_satisfaction NOT scored (F5).
    pref_item = _item("ingredient_preferred", {"ingredients_preferred": ["히알루론"]}, judgment)
    rec2 = ev.evaluate_results(["P1", "P2"], pref_item, products, by_id, caches, _NO_CTX)
    assert rec2["required_satisfaction"] is None
    assert rec2["preference_reflected"] is not None  # carrier P1 before non-carrier P2


def test_preference_reflected_ranking():
    p1 = _product("P1", ingredient_concept_ids=["concept:Ingredient:히알루론산"])  # carrier
    p2 = _product("P2", ingredient_concept_ids=[])  # non-carrier
    products = [p1, p2]
    caches = ev.JudgmentCaches(products=products)
    caches._required["히알루론"] = [
        IngredientConstraint(
            label="히알루론", inci_concept_ids=["concept:Ingredient:히알루론산"],
            name_surfaces=["히알루론"], provenance="raw",
        )
    ]
    by_id = {"P1": p1, "P2": p2}
    # carrier ahead → reflected 1.0 ; carrier behind → 0.0.
    assert ev.preference_reflected(["P1", "P2"], ["히알루론"], by_id, caches) == 1.0
    assert ev.preference_reflected(["P2", "P1"], ["히알루론"], by_id, caches) == 0.0
    # all carriers (no non-carrier) → None.
    assert ev.preference_reflected(["P1"], ["히알루론"], by_id, caches) is None


def test_false_zero_definition():
    # F3: false_zero fires when the results carry NO gain>0 while the catalog HAS a
    # relevant product — NOT only when returned==0.
    relevant_p = _product("P1", brand="헤라", category="쿠션")
    filler = _product("P2", brand="설화수", category="립스틱")
    products = [relevant_p, filler]
    caches = ev.JudgmentCaches(products=products)
    by_id = {p["product_id"]: p for p in products}
    item = _item("brand_category", {}, {"relevant": {"brand": "헤라", "category_surface": ["쿠션"]}})
    # returned only the gain-0 filler → false_zero True (old code returned False here).
    rec = ev.evaluate_results(["P2"], item, products, by_id, caches, _NO_CTX)
    assert rec["false_zero"] is True
    # returned the relevant one → not a false zero.
    rec2 = ev.evaluate_results(["P1"], item, products, by_id, caches, _NO_CTX)
    assert rec2["false_zero"] is False


def test_expect_zero_cases():
    products = [_product("P1", brand="헤라", category="크림")]
    caches = ev.JudgmentCaches(products=products)
    item = _item("legit_zero", {}, {"expect_zero": True})
    rec = ev.evaluate_results([], item, products, {}, caches, _NO_CTX)
    assert rec["correct_zero"] is True and rec["false_zero"] is None


# ---------------------------------------------------------------------------
# Gold assembly + per-surface gold reference (F6)
# ---------------------------------------------------------------------------


def test_gold_raw_payload_maps_slots():
    gold = {
        "intent": "recommend",
        "products": ["설화수 윤조에센스"],
        "brands": ["설화수"],
        "ingredients_required": ["히알루론"],
        "ingredients_preferred": ["스쿠알란"],
        "ingredients_avoided": ["레티놀"],
        "brands_excluded": ["이니스프리"],
        "categories_excluded": ["선크림"],
        "profile_refs": ["repurchase"],
    }
    raw = ev.gold_raw_payload(gold)
    assert raw["product_names"] == ["설화수 윤조에센스"]
    assert raw["ingredients_wanted"] == ["히알루론"]
    assert raw["ingredients_preferred"] == ["스쿠알란"]
    assert raw["ingredients_avoided"] == ["레티놀"]
    assert raw["brands_excluded"] == ["이니스프리"]
    assert raw["profile_refs"] == ["repurchase"]
    assert ev.gold_raw_payload({}) == {"intent": "search"}


def test_expected_slots_from_gold_is_independent_of_assembly():
    # Gold reference is resolved per-surface (F6), so it does not simply re-read the
    # assembled interpretation. profile_refs carry through verbatim.
    products = [_product("P1", name="설화수 크림", brand="설화수", category="크림")]
    gold = {"brands": ["설화수"], "profile_refs": ["repurchase"]}
    ref = ev.expected_slots_from_gold(gold, products)
    assert "concept:Brand:설화수" in ref["brands"]
    assert ref["profile_refs"] == {"repurchase"}


def test_slots_from_interpretation_dict():
    interp = {
        "resolved_concepts": [
            {"concept_type": "brand", "concept_id": "concept:Brand:설화수"},
            {"concept_type": "product", "concept_id": "62214"},
        ],
        "ingredient_constraints": [
            {"inci_concept_ids": ["concept:Ingredient:히알루론산"], "strength": "required", "label": "히알루론"},
            {"inci_concept_ids": ["concept:Ingredient:스쿠알란"], "strength": "preferred", "label": "스쿠알란"},
        ],
        "avoided_ingredient_concept_ids": ["concept:Ingredient:레티놀"],
        "excluded_brand_ids": ["concept:Brand:이니스프리"],
        "excluded_category_surfaces": ["선크림"],
        "excluded_category_groups": [],
        "profile_refs": ["repurchase"],
    }
    slots = ev.slots_from_interpretation_dict(interp)
    assert slots["brands"] == {"concept:Brand:설화수"}
    assert slots["products"] == {"62214"}
    assert slots["ingredients_required"] == {"concept:Ingredient:히알루론산"}
    assert slots["ingredients_preferred"] == {"concept:Ingredient:스쿠알란"}
    assert slots["ingredients_avoided"] == {"concept:Ingredient:레티놀"}
    assert slots["categories_excluded"] == {"surface:선크림"}
    assert slots["profile_refs"] == {"repurchase"}


# ---------------------------------------------------------------------------
# Holdout filter + dataset shape (F7)
# ---------------------------------------------------------------------------


def test_dataset_loads_holdout_and_stratum_coverage():
    dataset = ev.load_dataset()
    assert len(dataset) == 44
    holdout = [q for q in dataset if q.holdout]
    # F7: holdout ≥ 30% and ≥ 1 per stratum.
    assert len(holdout) / len(dataset) >= 0.30
    strata = {q.stratum for q in dataset}
    expected = {
        "exact_product", "brand_category", "ingredient_required", "ingredient_avoided",
        "ingredient_preferred", "brand_category_excluded", "multi_constraint",
        "profile_ref", "legit_zero", "typo",
    }
    assert expected <= strata
    for stratum in expected:
        assert any(q.holdout for q in dataset if q.stratum == stratum), stratum


# ---------------------------------------------------------------------------
# Slot P/R
# ---------------------------------------------------------------------------


def test_slot_counts_tp_fp_fn():
    gold = {name: set() for name in ev._SLOT_NAMES}
    pred = {name: set() for name in ev._SLOT_NAMES}
    gold["brands"] = {"b1", "b2"}
    pred["brands"] = {"b1", "b3"}
    counts = ev.slot_counts(gold, pred)
    assert counts["brands"] == (1, 1, 1)
    assert counts["products"] == (0, 0, 0)


def test_aggregate_slot_pr_micro_average():
    records = [
        {"slot_counts": {n: (0, 0, 0) for n in ev._SLOT_NAMES}},
        {"slot_counts": {n: (0, 0, 0) for n in ev._SLOT_NAMES}},
    ]
    records[0]["slot_counts"]["brands"] = (2, 0, 1)
    records[1]["slot_counts"]["brands"] = (1, 1, 0)
    pr = ev.aggregate_slot_pr(records)
    assert pr["brands"]["precision"] == pytest.approx(0.75)
    assert pr["brands"]["recall"] == pytest.approx(0.75)


def test_aggregate_reports_scored_denominators():
    # A relevant-only record + an exact record → exact_hit@1 scored n = 1.
    def _rec(metrics: dict[str, Any]) -> dict[str, Any]:
        base = {
            "exact_hit@1": None, "exact_hit@3": None, "relevant_hit@1": None,
            "relevant_hit@3": None, "ndcg@10": None, "required_satisfaction": None,
            "preference_reflected": None, "has_violation": None, "correct_zero": None,
            "false_zero": None,
        }
        base.update(metrics)
        return {"metrics": base}

    records = [
        _rec({"exact_hit@1": True, "relevant_hit@1": True, "ndcg@10": 1.0}),
        _rec({"relevant_hit@1": False, "ndcg@10": 0.5}),
    ]
    agg = ev.aggregate(records)
    assert agg["exact_hit@1"] == 1.0 and agg["exact_hit@1__n"] == 1
    assert agg["relevant_hit@1"] == pytest.approx(0.5) and agg["relevant_hit@1__n"] == 2
    assert agg["ndcg@10__n"] == 2
