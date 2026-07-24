"""Retrieval-quality evaluation harness — gold-vs-LLM loss decomposition (A5).

Plan: fable_doc/plans/2026-07-23_search_absorption.md §A5.

Measures the query pipeline's search quality by running each dataset query twice:

  (a) --run gold  — the interpretation is ASSEMBLED from the dataset's gold slots
      (surface strings) through the SAME validation gate the real LLM path uses
      (``query_understanding._interpret_with_llm``), then fed to the pipeline. This
      is the "perfect extraction" reference: any miss here is a SEARCH loss.
  (b) --run llm   — the real ``understand_query`` runs (LLM when
      ``GRAPHRAPPING_QUERY_LLM`` is configured, else the dictionary fallback). The
      gap between (a) and (b) is the EXTRACTION loss.

Grading is objective and hand-label-free: declarative, intent-faithful judgment
rules in the dataset are applied to the SERVING catalog (517 products) to derive
each product's gain (2 = exact expected product, 1 = relevant to ALL query
constraints, 0 = otherwise) and any hard-constraint violation.

Metrics (each reported WITH its scored denominator, per stratum + overall):
  - ExactHit@1/@3   — a gain-2 product in the top k (scored only where the query
                      names an exact expected product that exists in the catalog).
  - RelevantHit@1/@3 — a gain≥1 product in the top k.
  - nDCG@10          — gain 2/1/0 discounted cumulative gain vs the ideal.
  - required_satisfaction — fraction of results carrying every REQUIRED ingredient
                      (required-ingredient queries only; preferred excluded).
  - preference_reflected  — do preferred-ingredient carriers rank ahead of
                      non-carriers (preferred queries only).
  - violation_rate   — queries with ≥1 hard-constraint breach in the results.
  - correct/false_zero — false_zero = 0 gain>0 results returned while the catalog
                      HAS a relevant product (not merely returned==0).
  - interpretation slot P/R — the SERVER's interpretation (assembled+gated for gold,
                      real for actual) vs the gold slots resolved per-surface from the
                      YAML: measures extraction/assembly fidelity (not a tautology).

Determinism: no dates in the metric path, no randomness; results are
pipeline-deterministic and every emitted collection is sorted / order-fixed.

Usage:
    python -m scripts.eval_retrieval --run both            # dev set only (default)
    python -m scripts.eval_retrieval --run gold --holdout  # include holdout
    GRAPHRAPPING_QUERY_LLM=azure python -m scripts.eval_retrieval --run both
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("eval_retrieval")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATASET_PATH = _REPO_ROOT / "tests" / "eval" / "retrieval_queries.yaml"
_REPORTS_DIR = _REPO_ROOT / "tests" / "eval" / "reports"
_CATALOG_PATH = _REPO_ROOT / "mockdata" / "product_catalog_es.json"
_REAL_PROFILES_PATH = (
    _REPO_ROOT / "mockdata" / "real" / "users" / "user_profiles_real_20260720.json"
)
_FIXTURE_PROFILES_PATH = _REPO_ROOT / "mockdata" / "user_profiles_normalized.json"
_REVIEW_PATH = _REPO_ROOT / "mockdata" / "review_triples_raw.json"
_PREFERRED_LOGIN_USER_ID = "real_00000debf05b"
_MAX_REVIEWS = 906
_TOP_K = 10

# Interpretation slots compared for extraction accuracy (server interp vs gold).
_SLOT_NAMES = (
    "products",
    "brands",
    "categories",
    "ingredients_required",
    "ingredients_preferred",
    "ingredients_avoided",
    "brands_excluded",
    "categories_excluded",
    "profile_refs",
)


# ===========================================================================
# Dataset model
# ===========================================================================


@dataclass
class EvalQuery:
    id: str
    stratum: str
    query: str
    mode: str  # "anon" | "login"
    holdout: bool
    gold: dict[str, Any]  # gold_interpretation slots (surface strings)
    judgment: dict[str, Any]

    @property
    def is_login(self) -> bool:
        return self.mode == "login"

    @property
    def is_required_ingredient(self) -> bool:
        return bool(self.gold.get("ingredients_required"))

    @property
    def is_preferred_ingredient(self) -> bool:
        return bool(self.gold.get("ingredients_preferred"))


def load_dataset(path: str | Path = _DATASET_PATH) -> list[EvalQuery]:
    """Parse the YAML dataset into ``EvalQuery`` rows (order preserved)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows: list[EvalQuery] = []
    for item in raw:
        rows.append(
            EvalQuery(
                id=str(item["id"]),
                stratum=str(item["stratum"]),
                query=str(item["query"]),
                mode=str(item.get("mode", "anon")),
                holdout=bool(item.get("holdout", False)),
                gold=dict(item.get("gold_interpretation") or {}),
                judgment=dict(item.get("judgment") or {}),
            )
        )
    return rows


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ===========================================================================
# Judgment context (per-query, for profile-aware grading)
# ===========================================================================


@dataclass
class JudgmentContext:
    """Query-level facts the judgment engine needs beyond the product itself."""

    profile_brand_ids: frozenset[str] = frozenset()


# ===========================================================================
# Gold interpretation assembly
# ===========================================================================
#
# The gold slots are surface strings. Rather than hand-build a QueryInterpretation
# (and risk diverging from the production resolution/gating), the assembly maps the
# gold slots onto the exact ``raw`` JSON shape the LLM would emit and runs the real
# ``_interpret_with_llm`` — so gold concepts pass the SAME dictionary/catalog gate,
# constraint builder, negation/exclusion resolver and brand-contradiction guard as
# the live path.

# gold slot key -> LLM raw field name.
_GOLD_TO_RAW = {
    "products": "product_names",
    "brands": "brands",
    "categories": "categories",
    "ingredients_required": "ingredients_wanted",
    "ingredients_preferred": "ingredients_preferred",
    "ingredients_avoided": "ingredients_avoided",
    "brands_excluded": "brands_excluded",
    "categories_excluded": "categories_excluded",
    "concerns": "concerns",
    "goals": "goals",
    "desired_attributes": "desired_attributes",
    "profile_refs": "profile_refs",
}


def gold_raw_payload(gold: dict[str, Any]) -> dict[str, Any]:
    """Translate gold slots into the LLM ``raw`` JSON dict ``_interpret_with_llm`` reads."""
    raw: dict[str, Any] = {"intent": gold.get("intent", "search")}
    for gold_key, raw_key in _GOLD_TO_RAW.items():
        value = gold.get(gold_key)
        if value:
            raw[raw_key] = list(value)
    return raw


def assemble_gold_interpretation(
    query: str, gold: dict[str, Any], products: list[dict[str, Any]]
) -> Any:
    """Build a QueryInterpretation from gold slots via the production gate."""
    from src.rec.query_understanding import _interpret_with_llm

    return _interpret_with_llm(query, products, gold_raw_payload(gold))


# ===========================================================================
# Judgment engine (catalog-fact based, intent-faithful — F1)
# ===========================================================================


@dataclass
class JudgmentCaches:
    """Per-run caches so ingredient-surface resolution is done once."""

    products: list[dict[str, Any]]
    _required: dict[str, list[Any]] = field(default_factory=dict)
    _avoided: dict[str, set[str]] = field(default_factory=dict)

    def required_constraints(self, surface: str) -> list[Any]:
        """Constraints for an ingredient surface (the pipeline's own builder)."""
        if surface not in self._required:
            from src.rec.query_understanding import _build_ingredient_constraints
            from src.rec.search import resolve_query_concepts

            concepts = resolve_query_concepts(surface, self.products)
            self._required[surface] = _build_ingredient_constraints(
                surface, self.products, concepts, {}
            )
        return self._required[surface]

    def avoided_ids(self, surface: str) -> set[str]:
        """Avoided concept ids for a surface — mirrors the server's avoided path."""
        if surface not in self._avoided:
            from src.rec.search import resolve_query_concepts

            self._avoided[surface] = {
                c.concept_id
                for c in resolve_query_concepts(surface, self.products)
                if c.concept_type == "ingredient"
            }
        return self._avoided[surface]


def product_contains_ingredient(
    product: dict[str, Any], surface: str, caches: JudgmentCaches
) -> bool:
    from src.rec.ingredient_constraint import product_passes_constraints

    constraints = caches.required_constraints(surface)
    if not constraints:
        return False
    return product_passes_constraints(product, constraints)


def product_carries_avoided(
    product: dict[str, Any], surface: str, caches: JudgmentCaches
) -> bool:
    avoided = caches.avoided_ids(surface)
    if not avoided:
        return False
    return bool({str(v) for v in (product.get("ingredient_concept_ids") or [])} & avoided)


def _name_contains_all(product: dict[str, Any], subs: list[str]) -> bool:
    """AND — every token must be a substring of the product name (intent-faithful)."""
    name = str(product.get("product_name") or "")
    return all(sub in name for sub in subs)


def _brand_matches(product: dict[str, Any], brand: str) -> bool:
    return str(product.get("brand_name") or "") == brand


def _group_matches(product: dict[str, Any], group: str) -> bool:
    from src.rec.category_groups import classify_product_category_group

    return classify_product_category_group(product) == group


def _label_has_surface(product: dict[str, Any], surfaces: list[str]) -> bool:
    from src.common.text_normalize import normalize_text

    label = normalize_text(str(product.get("category_name") or ""))
    return any(normalize_text(s) in label for s in surfaces)


def _profile_brand_matches(product: dict[str, Any], ctx: JudgmentContext) -> bool:
    if not ctx.profile_brand_ids:
        return False
    return bool(
        {str(b) for b in (product.get("brand_concept_ids") or [])} & ctx.profile_brand_ids
    )


def expected_top_pids(
    judgment: dict[str, Any], products: list[dict[str, Any]]
) -> set[str]:
    """Gain-2 product ids: explicit ``product_ids`` and/or ALL-token name match."""
    expected = judgment.get("expected_top") or {}
    pids: set[str] = {str(x) for x in expected.get("product_ids", [])}
    tokens = [str(t) for t in expected.get("product_name_contains", [])]
    if tokens:
        for p in products:
            if _name_contains_all(p, tokens):
                pids.add(str(p.get("product_id")))
    catalog_ids = {str(p.get("product_id")) for p in products}
    return pids & catalog_ids


def product_satisfies_relevant(
    product: dict[str, Any],
    relevant: dict[str, Any],
    caches: JudgmentCaches,
    ctx: JudgmentContext,
) -> bool:
    """Relevant = ALL present relevant keys hold (AND — every query constraint)."""
    if not relevant:
        return False
    if "brand" in relevant and not _brand_matches(product, str(relevant["brand"])):
        return False
    if "category_group" in relevant and not _group_matches(
        product, str(relevant["category_group"])
    ):
        return False
    if "category_surface" in relevant and not _label_has_surface(
        product, [str(s) for s in relevant["category_surface"]]
    ):
        return False
    if "name_contains" in relevant and not _name_contains_all(
        product, [str(s) for s in relevant["name_contains"]]
    ):
        return False
    if relevant.get("profile_brand") and not _profile_brand_matches(product, ctx):
        return False
    for surface in relevant.get("must_contain_ingredient", []) or []:
        if not product_contains_ingredient(product, str(surface), caches):
            return False
    return True


def product_violates(
    product: dict[str, Any], judgment: dict[str, Any], caches: JudgmentCaches
) -> bool:
    violations = judgment.get("violations") or {}
    if not violations:
        return False
    must_not_brand = violations.get("must_not_brand")
    if must_not_brand and _brand_matches(product, str(must_not_brand)):
        return True
    for surface in violations.get("must_not_contain_ingredient", []) or []:
        if product_carries_avoided(product, str(surface), caches):
            return True
    surfaces = [str(s) for s in violations.get("must_not_category_surface", []) or []]
    if surfaces and _label_has_surface(product, surfaces):
        return True
    for group in violations.get("must_not_category_group", []) or []:
        if _group_matches(product, str(group)):
            return True
    return False


def compute_gains(
    products: list[dict[str, Any]],
    judgment: dict[str, Any],
    caches: JudgmentCaches,
    ctx: JudgmentContext,
) -> dict[str, int]:
    """Gain (2/1/0) for every catalog product. Gain-2 (expected) beats gain-1."""
    gain2 = expected_top_pids(judgment, products)
    relevant = judgment.get("relevant") or {}
    gains: dict[str, int] = {}
    for p in products:
        pid = str(p.get("product_id"))
        if not pid:
            continue
        if pid in gain2:
            gains[pid] = 2
        elif relevant and product_satisfies_relevant(p, relevant, caches, ctx):
            gains[pid] = 1
        else:
            gains[pid] = 0
    return gains


# ===========================================================================
# Metrics
# ===========================================================================


def _dcg(gains: list[int]) -> float:
    import math

    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked_pids: list[str], gain_by_pid: dict[str, int], k: int) -> float:
    ranked_gains = [gain_by_pid.get(pid, 0) for pid in ranked_pids[:k]]
    dcg = _dcg(ranked_gains)
    ideal = _dcg(sorted(gain_by_pid.values(), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return dcg / ideal


def hit_at_k(
    ranked_pids: list[str], gain_by_pid: dict[str, int], k: int, floor: int
) -> bool:
    """A product with gain ≥ ``floor`` appears within the top k."""
    return any(gain_by_pid.get(pid, 0) >= floor for pid in ranked_pids[:k])


def _has_gain(gain_by_pid: dict[str, int], floor: int) -> bool:
    return any(g >= floor for g in gain_by_pid.values())


def required_satisfaction(
    ranked_pids: list[str],
    judgment: dict[str, Any],
    products_by_id: dict[str, dict[str, Any]],
    caches: JudgmentCaches,
) -> float | None:
    """Fraction of returned results satisfying the required-ingredient rule.
    None when there is no such rule or nothing was returned."""
    relevant = judgment.get("relevant") or {}
    surfaces = [str(s) for s in relevant.get("must_contain_ingredient", []) or []]
    if not surfaces or not ranked_pids:
        return None
    ok = 0
    for pid in ranked_pids:
        product = products_by_id.get(pid)
        if product is not None and all(
            product_contains_ingredient(product, s, caches) for s in surfaces
        ):
            ok += 1
    return ok / len(ranked_pids)


def preference_reflected(
    ranked_pids: list[str],
    preferred_surfaces: list[str],
    products_by_id: dict[str, dict[str, Any]],
    caches: JudgmentCaches,
) -> float | None:
    """1.0 if the preferred-ingredient carriers, on average, rank AHEAD of the
    non-carriers among the returned results; 0.0 otherwise; None when there is no
    mix to compare (0 carriers or 0 non-carriers)."""
    if not preferred_surfaces or not ranked_pids:
        return None
    carrier_ranks: list[int] = []
    other_ranks: list[int] = []
    for rank, pid in enumerate(ranked_pids):
        product = products_by_id.get(pid)
        if product is None:
            continue
        carries = any(
            product_contains_ingredient(product, s, caches) for s in preferred_surfaces
        )
        (carrier_ranks if carries else other_ranks).append(rank)
    if not carrier_ranks or not other_ranks:
        return None
    mean_carrier = sum(carrier_ranks) / len(carrier_ranks)
    mean_other = sum(other_ranks) / len(other_ranks)
    return 1.0 if mean_carrier < mean_other else 0.0


def count_violations(
    ranked_pids: list[str],
    judgment: dict[str, Any],
    products_by_id: dict[str, dict[str, Any]],
    caches: JudgmentCaches,
) -> int:
    return sum(
        1
        for pid in ranked_pids
        if (p := products_by_id.get(pid)) is not None
        and product_violates(p, judgment, caches)
    )


def evaluate_results(
    ranked_pids: list[str],
    item: EvalQuery,
    products: list[dict[str, Any]],
    products_by_id: dict[str, dict[str, Any]],
    caches: JudgmentCaches,
    ctx: JudgmentContext,
) -> dict[str, Any]:
    """Compute the per-query metric record from a ranked result-id list."""
    judgment = item.judgment
    gain_by_pid = compute_gains(products, judgment, caches, ctx)
    returned_gains = [gain_by_pid.get(pid, 0) for pid in ranked_pids]
    expect_zero = bool(judgment.get("expect_zero"))
    has_gain2 = _has_gain(gain_by_pid, 2)
    has_gain1 = _has_gain(gain_by_pid, 1)
    record: dict[str, Any] = {
        "returned": len(ranked_pids),
        "expect_zero": expect_zero,
        "top_gains": returned_gains[:_TOP_K],
        # ExactHit@k — scored only where the query names an exact product present
        # in the catalog (denominator honesty, F2).
        "exact_hit@1": hit_at_k(ranked_pids, gain_by_pid, 1, 2) if has_gain2 else None,
        "exact_hit@3": hit_at_k(ranked_pids, gain_by_pid, 3, 2) if has_gain2 else None,
        # RelevantHit@k — a gain≥1 product in the top k (F2 auxiliary).
        "relevant_hit@1": hit_at_k(ranked_pids, gain_by_pid, 1, 1) if has_gain1 else None,
        "relevant_hit@3": hit_at_k(ranked_pids, gain_by_pid, 3, 1) if has_gain1 else None,
        "ndcg@10": (
            ndcg_at_k(ranked_pids, gain_by_pid, 10) if (has_gain1 or has_gain2) else None
        ),
    }
    # required_satisfaction — required-ingredient queries only (F5: preferred excluded).
    record["required_satisfaction"] = (
        required_satisfaction(ranked_pids, judgment, products_by_id, caches)
        if item.is_required_ingredient
        else None
    )
    # preference_reflected — preferred-ingredient queries only (F5).
    record["preference_reflected"] = (
        preference_reflected(
            ranked_pids,
            [str(s) for s in item.gold.get("ingredients_preferred", [])],
            products_by_id,
            caches,
        )
        if item.is_preferred_ingredient
        else None
    )
    if judgment.get("violations"):
        n_viol = count_violations(ranked_pids, judgment, products_by_id, caches)
        record["violations_count"] = n_viol
        record["has_violation"] = n_viol > 0
    else:
        record["violations_count"] = 0
        record["has_violation"] = None
    if expect_zero:
        record["correct_zero"] = len(ranked_pids) == 0
        record["false_zero"] = None
    else:
        record["correct_zero"] = None
        # F3: false_zero = the results contain NO gain>0 product while the catalog
        # DOES have a relevant one (not merely returned==0).
        n_gain_returned = sum(1 for g in returned_gains if g > 0)
        record["false_zero"] = has_gain1 and n_gain_returned == 0
    return record


# ===========================================================================
# Interpretation slot P/R (F6: server interp vs gold slots resolved per-surface)
# ===========================================================================


def expected_slots_from_gold(
    gold: dict[str, Any], products: list[dict[str, Any]]
) -> dict[str, set[str]]:
    """The gold REFERENCE slot sets, resolved PER-SURFACE from the YAML gold (each
    surface resolved independently — NOT the whole-query assembly), so comparing
    against the server's assembled/real interpretation actually measures fidelity
    rather than re-reading the same object (F6)."""
    from src.common.text_normalize import normalize_text
    from src.rec.query_understanding import (
        _build_ingredient_constraints,
        _build_negation_index,
        _resolve_excluded_category,
    )
    from src.rec.search import resolve_query_concepts

    slots: dict[str, set[str]] = {name: set() for name in _SLOT_NAMES}

    def _resolve_ids(surface: str, ctype: str) -> set[str]:
        return {
            c.concept_id
            for c in resolve_query_concepts(surface, products)
            if c.concept_type == ctype
        }

    for surface in gold.get("products", []):
        slots["products"] |= _resolve_ids(str(surface), "product")
    for surface in gold.get("brands", []):
        slots["brands"] |= _resolve_ids(str(surface), "brand")
    for surface in gold.get("categories", []):
        slots["categories"] |= _resolve_ids(str(surface), "category")
    for surface in gold.get("ingredients_avoided", []):
        slots["ingredients_avoided"] |= _resolve_ids(str(surface), "ingredient")

    def _ingredient_keys(surface: str) -> set[str]:
        concepts = resolve_query_concepts(surface, products)
        constraints = _build_ingredient_constraints(surface, products, concepts, {})
        keys: set[str] = set()
        for c in constraints:
            keys |= set(c.inci_concept_ids) or {f"label:{c.label}"}
        return keys

    for surface in gold.get("ingredients_required", []):
        slots["ingredients_required"] |= _ingredient_keys(str(surface))
    for surface in gold.get("ingredients_preferred", []):
        slots["ingredients_preferred"] |= _ingredient_keys(str(surface))

    index = _build_negation_index(products)
    for surface in gold.get("brands_excluded", []):
        bids = index.brand_surfaces.get(normalize_text(str(surface)))
        if bids:
            slots["brands_excluded"] |= set(bids)
    for surface in gold.get("categories_excluded", []):
        surfs, grps = _resolve_excluded_category(normalize_text(str(surface)), index)
        slots["categories_excluded"] |= {f"surface:{s}" for s in surfs}
        slots["categories_excluded"] |= {f"group:{g}" for g in grps}

    slots["profile_refs"] = {str(x) for x in gold.get("profile_refs", [])}
    return slots


def slots_from_interpretation_dict(interp_dict: dict[str, Any]) -> dict[str, set[str]]:
    """Slot sets from a serialized QueryInterpretation (payload.interpretation)."""
    slots: dict[str, set[str]] = {name: set() for name in _SLOT_NAMES}
    for concept in interp_dict.get("resolved_concepts", []):
        ctype = concept.get("concept_type")
        cid = str(concept.get("concept_id"))
        if ctype == "product":
            slots["products"].add(cid)
        elif ctype == "brand":
            slots["brands"].add(cid)
        elif ctype == "category":
            slots["categories"].add(cid)
    for constraint in interp_dict.get("ingredient_constraints", []):
        target = (
            "ingredients_preferred"
            if constraint.get("strength") == "preferred"
            else "ingredients_required"
        )
        keys = set(constraint.get("inci_concept_ids") or []) or {
            f"label:{constraint.get('label')}"
        }
        slots[target].update(str(k) for k in keys)
    slots["ingredients_avoided"] = {
        str(x) for x in interp_dict.get("avoided_ingredient_concept_ids", [])
    }
    slots["brands_excluded"] = {str(x) for x in interp_dict.get("excluded_brand_ids", [])}
    slots["categories_excluded"] = {
        *(f"surface:{s}" for s in interp_dict.get("excluded_category_surfaces", [])),
        *(f"group:{g}" for g in interp_dict.get("excluded_category_groups", [])),
    }
    slots["profile_refs"] = {str(x) for x in interp_dict.get("profile_refs", [])}
    return slots


def slot_counts(
    gold_slots: dict[str, set[str]], pred_slots: dict[str, set[str]]
) -> dict[str, tuple[int, int, int]]:
    """Per-slot (tp, fp, fn) for micro-averaged P/R aggregation."""
    out: dict[str, tuple[int, int, int]] = {}
    for name in _SLOT_NAMES:
        gold = gold_slots.get(name, set())
        pred = pred_slots.get(name, set())
        out[name] = (len(gold & pred), len(pred - gold), len(gold - pred))
    return out


def _pr(tp: int, fp: int, fn: int) -> tuple[float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall


# ===========================================================================
# Aggregation
# ===========================================================================


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _rate(bools: list[Any]) -> tuple[float | None, int]:
    scored = [v for v in bools if v is not None]
    if not scored:
        return None, 0
    return sum(1.0 if v else 0.0 for v in scored) / len(scored), len(scored)


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-query metric records, each with its scored denominator."""

    def col(key: str) -> list[Any]:
        return [r["metrics"][key] for r in records]

    out: dict[str, Any] = {"n": len(records)}
    for key in ("exact_hit@1", "exact_hit@3", "relevant_hit@1", "relevant_hit@3"):
        rate, n = _rate(col(key))
        out[key] = rate
        out[f"{key}__n"] = n
    for key in ("ndcg@10", "required_satisfaction", "preference_reflected"):
        vals = [v for v in col(key) if v is not None]
        out[key] = _mean(vals)
        out[f"{key}__n"] = len(vals)
    for key, alias in (
        ("has_violation", "violation_rate"),
        ("correct_zero", "correct_zero_rate"),
        ("false_zero", "false_zero_rate"),
    ):
        rate, n = _rate(col(key))
        out[alias] = rate
        out[f"{alias}__n"] = n
    return out


def aggregate_by_stratum(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by.setdefault(r["stratum"], []).append(r)
    return {stratum: aggregate(rows) for stratum, rows in sorted(by.items())}


def aggregate_slot_pr(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Micro-averaged per-slot P/R over the run's slot_counts."""
    totals: dict[str, list[int]] = {name: [0, 0, 0] for name in _SLOT_NAMES}
    for r in records:
        for name, counts in (r.get("slot_counts") or {}).items():
            tp, fp, fn = counts
            totals[name][0] += tp
            totals[name][1] += fp
            totals[name][2] += fn
    out: dict[str, dict[str, float]] = {}
    for name, (tp, fp, fn) in totals.items():
        precision, recall = _pr(tp, fp, fn)
        out[name] = {
            "precision": precision,
            "recall": recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    return out


# ===========================================================================
# Pipeline driver (impure — TestClient over a real load_demo_data run)
# ===========================================================================


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(_REPO_ROOT)
            )
            .decode()
            .strip()
        )
    except Exception:  # pragma: no cover - git absent
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(_REPO_ROOT)
        ).decode()
        return bool(out.strip())
    except Exception:  # pragma: no cover
        return False


def _extract_brand_ids(user: dict[str, Any]) -> frozenset[str]:
    """Union of the user's profile brand concept ids (preferred / repurchase / recent)."""
    ids: set[str] = set()
    for key in (
        "preferred_brand_ids",
        "repurchase_brand_ids",
        "recent_purchase_brand_ids",
    ):
        for entry in user.get(key, []) or []:
            cid = entry.get("id") if isinstance(entry, dict) else entry
            if cid:
                ids.add(str(cid))
    return frozenset(ids)


def load_pipeline() -> tuple[Any, list[dict[str, Any]], str, str]:
    """Load the in-process pipeline once.

    Returns (TestClient, serving_products, login_user_id, profile_source).
    Real pseudonymized profiles are used when present; otherwise the harness
    degrades to the checked-in fixture profiles so a clean checkout can still run
    (F8) — the login user is then the first serving user with brand preferences.
    """
    from fastapi.testclient import TestClient

    from src.web import server
    from src.web.state import load_demo_data

    os.environ["GRAPHRAPPING_ENABLE_PIPELINE_RUN"] = "1"

    if _REAL_PROFILES_PATH.exists():
        profile_source = "real"
        profiles_path = _REAL_PROFILES_PATH
        os.environ["GRAPHRAPPING_USER_PROFILES_JSON"] = str(_REAL_PROFILES_PATH)
    else:
        profile_source = "fixture"
        profiles_path = _FIXTURE_PROFILES_PATH
        os.environ.pop("GRAPHRAPPING_USER_PROFILES_JSON", None)

    products = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    users = json.loads(profiles_path.read_text(encoding="utf-8"))
    state = load_demo_data(
        str(_REVIEW_PATH),
        products,
        users,
        max_reviews=_MAX_REVIEWS,
        source="a5_eval",
        review_format="relation",
        kg_mode="on",
    )
    server.demo_state = state
    server._serving_store = None
    client = TestClient(server.app)

    serving = list(state.serving_products)
    login_user_id = _resolve_login_user(state, profile_source)
    return client, serving, login_user_id, profile_source


def _resolve_login_user(state: Any, profile_source: str) -> str:
    serving_users = list(state.serving_users)
    ids = [str(u.get("user_id")) for u in serving_users]
    if profile_source == "real" and _PREFERRED_LOGIN_USER_ID in ids:
        return _PREFERRED_LOGIN_USER_ID
    # Prefer a user with brand preferences so profile_ref judgment is meaningful.
    for u in serving_users:
        if _extract_brand_ids(u):
            return str(u.get("user_id"))
    return ids[0] if ids else _PREFERRED_LOGIN_USER_ID


def _post_ask(client: Any, item: EvalQuery, login_user_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {"query": item.query, "top_k": _TOP_K}
    if item.is_login:
        body["user_id"] = login_user_id
    resp = client.post("/api/ask", json=body)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


def _result_pids(payload: dict[str, Any]) -> list[str]:
    return [
        str(r["product_id"]) for r in payload.get("results", []) if r.get("product_id")
    ]


def _result_top10(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in payload.get("results", [])[:_TOP_K]:
        product = r.get("product") or {}
        out.append(
            {
                "product_id": str(r.get("product_id")),
                "name": product.get("product_name"),
                "score": r.get("relevance_score") or r.get("score"),
            }
        )
    return out


def run_pass(
    client: Any,
    dataset: list[EvalQuery],
    products: list[dict[str, Any]],
    run_mode: str,
    login_user_id: str,
) -> list[dict[str, Any]]:
    """Run one pass (``gold`` or ``llm``) over the dataset; return per-query records."""
    import asyncio

    from src.rec import query_understanding
    from src.web import server

    products_by_id = {str(p.get("product_id")): p for p in products}
    caches = JudgmentCaches(products=products)
    real_understand = query_understanding.understand_query
    store = server.get_serving_store()
    records: list[dict[str, Any]] = []

    for item in dataset:
        # Profile-brand context for login queries (profile_ref grading, F4).
        ctx = JudgmentContext()
        if item.is_login:
            user = asyncio.get_event_loop().run_until_complete(
                store.get_user(login_user_id)
            )
            if user:
                ctx = JudgmentContext(profile_brand_ids=_extract_brand_ids(user))

        gold_interp = assemble_gold_interpretation(item.query, item.gold, products)

        if run_mode == "gold":

            def _gold_understand(
                _q: str, _p: list[dict[str, Any]], _gi: Any = gold_interp, **_kw: Any
            ) -> Any:
                return _gi

            server.understand_query = _gold_understand  # type: ignore[assignment]
        else:
            server.understand_query = real_understand  # type: ignore[assignment]
            query_understanding.clear_query_cache()

        payload = _post_ask(client, item, login_user_id)
        used_interp_dict = payload.get("interpretation", {})
        ranked = _result_pids(payload)
        metrics = evaluate_results(ranked, item, products, products_by_id, caches, ctx)

        # F6: gold reference from the YAML resolved per-surface; pred = server interp.
        gold_ref_slots = expected_slots_from_gold(item.gold, products)
        pred_slots = slots_from_interpretation_dict(used_interp_dict)
        record: dict[str, Any] = {
            "id": item.id,
            "stratum": item.stratum,
            "mode": item.mode,
            "run": run_mode,
            "query": item.query,
            "resolved_mode": payload.get("resolved_mode"),
            "relaxed": payload.get("relaxed"),
            "ingredient_filter": payload.get("ingredient_filter"),
            "pinned_product_ids": payload.get("pinned_product_ids"),
            "applied_profile_refs": payload.get("applied_profile_refs"),
            "excluded": payload.get("excluded"),
            "interpretation": used_interp_dict,
            "results_top10": _result_top10(payload),
            "result_ids": ranked,
            "metrics": metrics,
            "slot_counts": slot_counts(gold_ref_slots, pred_slots),
        }
        # F4: profile_ref personalization gap — flag when the profile classes resolved
        # to NOTHING (applied_profile_refs empty) on a profile-ref query.
        if item.stratum == "profile_ref":
            record["profile_applied"] = bool(payload.get("applied_profile_refs"))
        records.append(record)

    server.understand_query = real_understand  # type: ignore[assignment]
    return records


# ===========================================================================
# Reporting
# ===========================================================================


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def build_markdown_report(
    manifest: dict[str, Any], per_run: dict[str, dict[str, Any]]
) -> str:
    lines: list[str] = []
    lines.append("# Retrieval evaluation baseline")
    lines.append("")
    lines.append("## Run manifest")
    lines.append("")
    for key in (
        "git_sha",
        "git_dirty",
        "command_line",
        "dataset_sha256",
        "harness_sha256",
        "catalog_sha256",
        "profiles_sha256",
        "profile_source",
        "login_user_id",
        "runs",
        "llm_mode",
        "llm_actually_used",
        "actual_extraction_path",
        "catalog_size",
        "n_queries",
        "include_holdout",
    ):
        lines.append(f"- **{key}**: {manifest.get(key)}")
    lines.append("")
    if manifest.get("llm_mode") not in (None, "off") and not manifest.get(
        "llm_actually_used"
    ):
        lines.append(
            "> NOTE: `GRAPHRAPPING_QUERY_LLM` was set but the provider never answered "
            "(unreachable from this host — private endpoint / no VPN). The `llm`/actual "
            "pass below is the **dictionary fallback**, so its gap vs gold measures the "
            "fallback's extraction loss, not a live LLM's."
        )
        lines.append("")

    runs = list(per_run.keys())

    lines.append("## Overall (gold vs actual) — value (scored n)")
    lines.append("")
    lines.append("| metric | " + " | ".join(runs) + " |")
    lines.append("|" + "---|" * (len(runs) + 1))
    metric_keys = [
        "exact_hit@1",
        "exact_hit@3",
        "relevant_hit@1",
        "relevant_hit@3",
        "ndcg@10",
        "required_satisfaction",
        "preference_reflected",
        "violation_rate",
        "correct_zero_rate",
        "false_zero_rate",
    ]
    for mk in metric_keys:
        cells = []
        for r in runs:
            ov = per_run[r]["overall"]
            cells.append(f"{_fmt(ov.get(mk))} ({ov.get(mk + '__n', 0)})")
        lines.append(f"| {mk} | " + " | ".join(cells) + " |")
    lines.append(
        "| n | " + " | ".join(str(per_run[r]["overall"]["n"]) for r in runs) + " |"
    )
    lines.append("")

    lines.append("## Per-stratum (nDCG@10 / RelevantHit@1 / n)")
    lines.append("")
    strata = sorted({s for r in runs for s in per_run[r]["by_stratum"].keys()})
    lines.append(
        "| stratum | "
        + " | ".join(f"{r} nDCG" for r in runs)
        + " | "
        + " | ".join(f"{r} RelHit@1" for r in runs)
        + " | n |"
    )
    lines.append("|" + "---|" * (2 + 2 * len(runs)))
    for stratum in strata:
        ndcgs = [
            _fmt(per_run[r]["by_stratum"].get(stratum, {}).get("ndcg@10")) for r in runs
        ]
        relhits = [
            _fmt(per_run[r]["by_stratum"].get(stratum, {}).get("relevant_hit@1"))
            for r in runs
        ]
        n = next(
            (
                per_run[r]["by_stratum"][stratum]["n"]
                for r in runs
                if stratum in per_run[r]["by_stratum"]
            ),
            0,
        )
        lines.append(
            "| "
            + stratum
            + " | "
            + " | ".join(ndcgs)
            + " | "
            + " | ".join(relhits)
            + f" | {n} |"
        )
    lines.append("")

    lines.append("## Interpretation slot P/R vs gold (extraction accuracy)")
    lines.append("")
    for r in runs:
        lines.append(f"### run = {r}")
        lines.append("")
        lines.append("| slot | precision | recall | tp | fp | fn |")
        lines.append("|---|---|---|---|---|---|")
        slot_pr = per_run[r]["slot_pr"]
        for name in _SLOT_NAMES:
            s = slot_pr[name]
            lines.append(
                f"| {name} | {_fmt(s['precision'])} | {_fmt(s['recall'])} | "
                f"{s['tp']} | {s['fp']} | {s['fn']} |"
            )
        lines.append("")

    if "gold" in per_run and "llm" in per_run:
        lines.append("## Loss decomposition (gold − actual)")
        lines.append("")
        lines.append(
            "- **Search loss** = 1 − gold metric (a miss under perfect extraction)."
        )
        lines.append(
            "- **Extraction loss** = gold metric − actual metric (the LLM/fallback gap)."
        )
        lines.append("")
        lines.append("| metric | gold | actual | extraction loss |")
        lines.append("|---|---|---|---|")
        for mk in (
            "exact_hit@1",
            "exact_hit@3",
            "relevant_hit@1",
            "ndcg@10",
            "required_satisfaction",
            "preference_reflected",
        ):
            g = per_run["gold"]["overall"].get(mk)
            a = per_run["llm"]["overall"].get(mk)
            delta = (g - a) if (g is not None and a is not None) else None
            lines.append(f"| {mk} | {_fmt(g)} | {_fmt(a)} | {_fmt(delta)} |")
        lines.append("")
        worst = _worst_extraction_stratum(per_run)
        if worst:
            lines.append(
                f"- **Largest extraction loss (nDCG@10)**: `{worst[0]}` "
                f"(gold {_fmt(worst[1])} → actual {_fmt(worst[2])}, Δ {_fmt(worst[3])})"
            )
            lines.append("")
    return "\n".join(lines)


def _worst_extraction_stratum(
    per_run: dict[str, dict[str, Any]],
) -> tuple[str, float, float, float] | None:
    gold_by = per_run["gold"]["by_stratum"]
    actual_by = per_run["llm"]["by_stratum"]
    worst: tuple[str, float, float, float] | None = None
    for stratum, g in gold_by.items():
        gv = g.get("ndcg@10")
        av = actual_by.get(stratum, {}).get("ndcg@10")
        if gv is None or av is None:
            continue
        delta = gv - av
        if worst is None or delta > worst[3]:
            worst = (stratum, gv, av, delta)
    return worst


def write_reports(
    manifest: dict[str, Any],
    per_run: dict[str, dict[str, Any]],
    all_records: list[dict[str, Any]],
    out_dir: Path = _REPORTS_DIR,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.date.today().strftime("%Y%m%d")
    jsonl_path = out_dir / f"baseline_{stamp}.jsonl"
    md_path = out_dir / f"baseline_{stamp}.md"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"_manifest": manifest}, ensure_ascii=False) + "\n")
        for record in all_records:
            serializable = dict(record)
            serializable["slot_counts"] = {
                k: list(v) for k, v in record["slot_counts"].items()
            }
            fh.write(json.dumps(serializable, ensure_ascii=False) + "\n")

    md_path.write_text(build_markdown_report(manifest, per_run), encoding="utf-8")
    return jsonl_path, md_path


# ===========================================================================
# main
# ===========================================================================


def _summarize_run(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": aggregate(records),
        "by_stratum": aggregate_by_stratum(records),
        "slot_pr": aggregate_slot_pr(records),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="A5 retrieval-quality eval harness")
    parser.add_argument("--run", choices=("gold", "llm", "both"), default="both")
    parser.add_argument(
        "--holdout",
        action="store_true",
        help="include holdout queries (default: dev only)",
    )
    parser.add_argument("--out-dir", default=str(_REPORTS_DIR))
    args = parser.parse_args(argv)

    from src.common.env_file import load_env_file

    load_env_file(_REPO_ROOT / ".env")

    dataset = load_dataset()
    if not args.holdout:
        dataset = [q for q in dataset if not q.holdout]

    llm_mode = os.environ.get("GRAPHRAPPING_QUERY_LLM", "").strip().lower() or "off"
    runs = ["gold", "llm"] if args.run == "both" else [args.run]

    client, products, login_user_id, profile_source = load_pipeline()
    print(
        f"pipeline loaded: {len(products)} products; runs={runs}; "
        f"llm_mode={llm_mode}; profile_source={profile_source}; login_user={login_user_id}"
    )

    per_run: dict[str, dict[str, Any]] = {}
    all_records: list[dict[str, Any]] = []
    llm_used_any = False
    for run_mode in runs:
        print(f"running pass: {run_mode} ...")
        records = run_pass(client, dataset, products, run_mode, login_user_id)
        per_run[run_mode] = _summarize_run(records)
        all_records.extend(records)
        if run_mode == "llm":
            llm_used_any = any(r["interpretation"].get("llm_used") for r in records)

    actual_extraction = (
        "llm"
        if (llm_mode != "off" and llm_used_any)
        else ("dictionary_fallback" if "llm" in runs else "n/a")
    )
    profiles_path = (
        _REAL_PROFILES_PATH if profile_source == "real" else _FIXTURE_PROFILES_PATH
    )
    manifest = {
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "command_line": " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]),
        "dataset_sha256": _sha256(_DATASET_PATH),
        "harness_sha256": _sha256(Path(__file__)),
        "catalog_sha256": _sha256(_CATALOG_PATH),
        "profiles_sha256": _sha256(profiles_path),
        "profile_source": profile_source,
        "login_user_id": login_user_id,
        "runs": runs,
        "llm_mode": llm_mode,
        "llm_actually_used": llm_used_any if "llm" in runs else None,
        "actual_extraction_path": actual_extraction,
        "catalog_size": len(products),
        "n_queries": len(dataset),
        "include_holdout": args.holdout,
    }

    jsonl_path, md_path = write_reports(
        manifest, per_run, all_records, out_dir=Path(args.out_dir)
    )
    print(f"wrote {jsonl_path}")
    print(f"wrote {md_path}")
    print("\n--- overall ---")
    for run_mode in runs:
        print(run_mode, json.dumps(per_run[run_mode]["overall"], ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
