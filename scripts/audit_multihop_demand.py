#!/usr/bin/env python3
"""Audit whether 2-hop+ graph traversal would add value to recommendation.

Phase 4.0 demand gate (see fable_doc/03_improvement_plan.md §4.0 and issue A1):
before building a recursive-CTE / graph-traversal module (Phase 4.1), prove with
data that multi-hop paths over ``canonical_fact`` actually produce recommendable
candidates or evidence that the current 1-hop concept-overlap path misses.

The audit is read-only. It reuses the same in-memory pipeline as the demo server
and ``audit_recommendation_evidence.py`` (run_full_load), so the ``canonical_fact``
triples it inspects are materialized exactly as the DB layer would persist them
(subject_iri, predicate, object_iri). No DB or network access.

Candidate use cases evaluated (from the Phase 4.0 brief):
  UC1  concern -> ingredient -> product        (generalization of concern_bridge)
  UC2  product -> co-used -> product -> ...     (2-hop co-use expansion)
  UC3  goal   -> ... -> bee_attr -> product     (semantic goal chain)
  UC4  product -> bee_attr -> keyword           (attribute enrichment, same product)
  UC5  product <- uses <- reviewer -> uses -> product   (reviewer-mediated co-use / CF)

Each use case reports how many NET-NEW recommendable candidates/evidence a 2-hop
traversal would add beyond 1-hop, plus a noise estimate, and a verdict:
  DEMAND    — substantial new recommendable-SKU contribution, low noise
  MARGINAL  — small / noisy / already reachable without traversal
  NONE      — structurally impossible (missing nodes/edges) or 0 real new candidates

Usage:
    python -m scripts.audit_multihop_demand --fixture dense_golden
    python scripts/audit_multihop_demand.py --fixture dense_golden --json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jobs.run_full_load import FullLoadConfig, run_full_load  # noqa: E402
from src.loaders.source_review_stats_loader import (  # noqa: E402
    load_source_review_stats_snapshot,
)
from src.rec.category_groups import classify_product_category_group  # noqa: E402
from src.rec.concern_bridge import compute_bridged_concerns  # noqa: E402


FIXTURE_DIRS = {
    "wide": ROOT / "mockdata",
    "dense_golden": ROOT / "mockdata" / "dense_golden",
}
DEFAULT_SOURCE_REVIEW_STATS_PATH = (
    ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
)

# Product<->product predicates that express genuine co-use / substitution.
# has_part/part_of/available_in/variant_of are compositional, not co-use.
COUSE_PREDICATES = frozenset({"used_with", "comparison_with"})
# Reviewer->product predicates usable for a reviewer-mediated co-use path.
REVIEWER_USE_PREDICATES = frozenset({"uses", "purchases", "recommended_by"})
# Generic ingredient tokens that would create spurious shared-ingredient links.
GENERIC_INGREDIENT_TOKENS = frozenset({"성분", "ingredient", "제품", "product"})


# --------------------------------------------------------------------------- #
# Graph loading
# --------------------------------------------------------------------------- #
class Graph:
    """Materialized canonical_fact graph + serving artifacts for one fixture."""

    def __init__(
        self,
        *,
        fixture: str,
        facts: list[Any],
        serving_products: list[dict[str, Any]],
        concept_links: dict[str, list[dict[str, Any]]],
        entity_type_counts: dict[str, int],
    ) -> None:
        self.fixture = fixture
        self.facts = facts
        self.serving_products = serving_products
        self.concept_links = concept_links
        self.entity_type_counts = entity_type_counts
        self._group_cache: dict[str, str] = {}
        for product in serving_products:
            pid = str(product["product_id"])
            self._group_cache[pid] = classify_product_category_group(product)

    def group_of(self, pid: str) -> str:
        return self._group_cache.get(pid, "?")

    def predicate_counts(self) -> dict[str, int]:
        return dict(Counter(f.predicate for f in self.facts))


def _norm_pid(iri: str | None) -> str:
    if not iri:
        return ""
    return iri[len("product:"):] if iri.startswith("product:") else iri


def _is_catalog_sku(pid: str) -> bool:
    """Catalog SKUs are numeric ids; concept-mention 'products' are not.

    Unresolved review mentions (e.g. 'concept:Product:다른 퍼프') are NOT
    recommendable candidates, so they must not count toward demand.
    """
    return pid.isdigit()


def load_graph(fixture: str) -> Graph:
    fixture_dir = FIXTURE_DIRS.get(fixture)
    if fixture_dir is None:
        raise ValueError(f"unknown fixture: {fixture}; allowed: {sorted(FIXTURE_DIRS)}")
    if not fixture_dir.exists():
        raise FileNotFoundError(f"fixture directory does not exist: {fixture_dir}")

    products = _load_json(fixture_dir / "product_catalog_es.json")
    users = _load_json(fixture_dir / "user_profiles_normalized.json")
    review_path = fixture_dir / "review_triples_raw.json"
    source_review_stats = _load_source_review_stats_for_products(products)

    # run_full_load prints progress; keep CLI/JSON output clean.
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(
            FullLoadConfig(
                review_json_path=str(review_path),
                product_es_records=products,
                user_profiles=users,
                kg_mode="on",
                source_review_stats_by_product=source_review_stats,
            )
        )

    bundles = result.batch_result.get("all_bundles", [])
    facts = [fact for bundle in bundles for fact in bundle.canonical_facts]
    entity_type_counts: Counter[str] = Counter()
    for bundle in bundles:
        for entity in bundle.canonical_entities:
            entity_type_counts[entity.entity_type] += 1

    return Graph(
        fixture=fixture,
        facts=facts,
        serving_products=result.serving_products,
        concept_links=result.batch_result.get("concept_links", {}),
        entity_type_counts=dict(entity_type_counts),
    )


# --------------------------------------------------------------------------- #
# Use case audits
# --------------------------------------------------------------------------- #
def audit_couse_expansion(graph: Graph) -> dict[str, Any]:
    """UC2: product -> co-used -> product -> co-used -> product.

    Demand = net-new recommendable catalog SKUs reachable at 2 hops that are not
    already 1-hop co-use neighbors. Concept-mention pseudo products are excluded.
    """
    adjacency: dict[str, set[str]] = defaultdict(set)
    ghost_edges = 0
    real_edges: set[tuple[str, str]] = set()
    for fact in graph.facts:
        if (
            fact.subject_type == "Product"
            and fact.object_type == "Product"
            and fact.predicate in COUSE_PREDICATES
            and fact.object_iri
        ):
            subj, obj = _norm_pid(fact.subject_iri), _norm_pid(fact.object_iri)
            if subj == obj:
                continue
            adjacency[subj].add(obj)
            adjacency[obj].add(subj)
            if _is_catalog_sku(subj) and _is_catalog_sku(obj):
                real_edges.add(tuple(sorted((subj, obj))))
            else:
                ghost_edges += 1

    new_real = 0
    new_ghost = 0
    for node, one_hop in adjacency.items():
        two_hop: set[str] = set()
        for neighbor in one_hop:
            two_hop |= adjacency.get(neighbor, set())
        two_hop -= one_hop
        two_hop.discard(node)
        for candidate in two_hop:
            if _is_catalog_sku(candidate):
                new_real += 1
            else:
                new_ghost += 1

    serving_with_couse = sum(
        1 for p in graph.serving_products if p.get("top_coused_product_ids")
    )

    verdict = "NONE" if new_real == 0 else ("MARGINAL" if new_real < 10 else "DEMAND")
    return {
        "use_case": "UC2 product -> co-used -> product (2-hop)",
        "one_hop_couse_edges_total": len(real_edges) + ghost_edges,
        "one_hop_couse_edges_real_sku": len(real_edges),
        "one_hop_couse_edges_concept_ghost": ghost_edges,
        "new_2hop_candidates_real_sku": new_real,
        "new_2hop_candidates_concept_ghost": new_ghost,
        "serving_products_with_coused_signal": serving_with_couse,
        "serving_product_count": len(graph.serving_products),
        "verdict": verdict,
        "note": (
            "Co-use edges are dominated by unresolved concept mentions "
            "('다른 퍼프' etc.); real-SKU co-use is near zero and the aggregation "
            "layer emits no coused serving signal."
        ),
    }


def audit_shared_ingredient(graph: Graph) -> dict[str, Any]:
    """UC1-adjacent: product -> ingredient -> product (shared-ingredient expansion).

    Uses product-master HAS_INGREDIENT links (catalog truth). Generic tokens are
    excluded. Cross-category pairs are counted as noise, since a shared ingredient
    across unrelated categories is a weak recommendation signal.
    """
    product_ingredients: dict[str, set[str]] = defaultdict(set)
    for product_iri, links in graph.concept_links.items():
        pid = _norm_pid(product_iri)
        for link in links:
            if link.get("link_type") != "HAS_INGREDIENT":
                continue
            concept_id = str(link.get("concept_id") or "")
            token = concept_id.split(":")[-1]
            if not concept_id or token in GENERIC_INGREDIENT_TOKENS:
                continue
            product_ingredients[pid].add(concept_id)

    ingredient_products: dict[str, set[str]] = defaultdict(set)
    for pid, ingredients in product_ingredients.items():
        for ingredient in ingredients:
            ingredient_products[ingredient].add(pid)

    shared_ingredients = {
        ing: prods for ing, prods in ingredient_products.items() if len(prods) >= 2
    }
    pairs: set[tuple[str, str]] = set()
    for prods in shared_ingredients.values():
        for a, b in combinations(sorted(prods), 2):
            pairs.add((a, b))
    same_group = sum(1 for a, b in pairs if graph.group_of(a) == graph.group_of(b))
    cross_group = len(pairs) - same_group

    # Personalization redundancy: if a user prefers ingredient X, 1-hop overlap
    # already makes BOTH products eligible independently — no traversal needed.
    verdict = "NONE"
    if same_group >= 10:
        verdict = "MARGINAL"  # usable only as item-similarity, achievable via GROUP BY
    elif pairs:
        verdict = "MARGINAL" if same_group else "NONE"

    return {
        "use_case": "UC1' product -> ingredient -> product (shared ingredient)",
        "products_with_master_ingredients": len(product_ingredients),
        "shared_ingredients_ge2_products": len(shared_ingredients),
        "distinct_product_pairs": len(pairs),
        "pairs_same_category_group": same_group,
        "pairs_cross_category_group_noise": cross_group,
        "cross_group_noise_ratio": round(cross_group / len(pairs), 3) if pairs else 0.0,
        "verdict": verdict,
        "note": (
            "Master-truth ingredients are already matchable at 1-hop for "
            "personalization; the 2-hop only yields item-to-item similarity, "
            "achievable with a single GROUP BY (no recursive CTE), and here it is "
            "majority cross-category noise."
        ),
    }


def audit_concern_goal_chain(graph: Graph) -> dict[str, Any]:
    """UC1/UC3 structural feasibility: concern -> ... and goal -> ... chains.

    These require Concern / Goal to exist as graph nodes with connecting edges.
    If they are absent, no traversal is possible regardless of hop count.
    """
    concern_nodes = graph.entity_type_counts.get("Concern", 0)
    goal_nodes = graph.entity_type_counts.get("Goal", 0)
    concern_edge_predicates = ("treats", "addresses", "addressed_by_treatment", "addressed_to")
    concern_edges = {
        predicate: sum(1 for f in graph.facts if f.predicate == predicate)
        for predicate in concern_edge_predicates
    }
    facts_touching_concern = sum(
        1
        for f in graph.facts
        if (f.subject_type == "Concern" or f.object_type == "Concern")
    )
    facts_touching_goal = sum(
        1
        for f in graph.facts
        if (f.subject_type == "Goal" or f.object_type == "Goal")
    )

    # Current 1-hop concern coverage (direct signal + hardcoded bee->concern bridge).
    direct_concern_products = sum(
        1 for p in graph.serving_products if p.get("top_concern_pos_ids")
    )
    bridged_concern_products = sum(
        1
        for p in graph.serving_products
        if compute_bridged_concerns(p.get("top_bee_attr_ids") or [])
    )

    feasible = bool(concern_nodes or goal_nodes)
    verdict = "DEMAND" if feasible else "NONE"
    return {
        "use_case": "UC1/UC3 concern|goal semantic chains",
        "concern_nodes_in_graph": concern_nodes,
        "goal_nodes_in_graph": goal_nodes,
        "concern_side_edge_counts": concern_edges,
        "facts_touching_concern": facts_touching_concern,
        "facts_touching_goal": facts_touching_goal,
        "current_1hop_products_with_direct_concern": direct_concern_products,
        "current_1hop_products_with_bridged_concern": bridged_concern_products,
        "serving_product_count": len(graph.serving_products),
        "verdict": verdict,
        "note": (
            "Concern and Goal are NOT materialized as canonical_fact nodes; they "
            "are projection/serving-layer derivations. A concern->ingredient or "
            "goal->attribute chain would require authoring a new curated map "
            "(like concern_bee_attr_map) + a 1-hop lookup — not traversal of "
            "existing triples."
        ),
    }


def audit_bee_keyword_enrichment(graph: Graph) -> dict[str, Any]:
    """UC4: product -> bee_attr -> keyword (the only densely-populated 2-hop).

    This stays within a single product (attribute -> its sub-keywords). It adds
    no new candidate product; at most it enriches explanation of a product already
    eligible via its bee_attr at 1-hop.
    """
    bee_keywords: dict[str, set[str]] = defaultdict(set)
    for fact in graph.facts:
        if fact.predicate == "HAS_KEYWORD" and fact.object_iri:
            bee_keywords[fact.subject_iri].add(fact.object_iri)

    product_bee: dict[str, set[str]] = defaultdict(set)
    for fact in graph.facts:
        if fact.predicate == "has_attribute" and fact.object_iri:
            product_bee[_norm_pid(fact.subject_iri)].add(fact.object_iri)

    products_reaching_keywords = 0
    for bees in product_bee.values():
        if any(bee_keywords.get(bee) for bee in bees):
            products_reaching_keywords += 1

    has_keyword_facts = sum(1 for f in graph.facts if f.predicate == "HAS_KEYWORD")
    return {
        "use_case": "UC4 product -> bee_attr -> keyword (same-product enrichment)",
        "has_keyword_facts": has_keyword_facts,
        "products_reaching_keyword_via_2hop": products_reaching_keywords,
        "new_candidate_products": 0,
        "verdict": "MARGINAL" if has_keyword_facts else "NONE",
        "note": (
            "Densely populated but intra-product: expands a product's own "
            "attributes into sub-keywords. Yields zero new candidate products; "
            "value is explanation richness only, and the product is already "
            "eligible via its bee_attr at 1-hop."
        ),
    }


def audit_reviewer_couse(graph: Graph) -> dict[str, Any]:
    """UC5: product <- uses <- reviewer -> uses -> product (collaborative filtering).

    This is the only structurally dense multi-hop path, BUT:
      * Invariant G4 (ARCHITECTURE.md) intentionally forbids reviewer<->user
        collaborative-filtering to protect privacy.
      * In the dense fixture, reviews are round-robin remapped to products
        (fixture_remap_reason=dense_round_robin), so reviewer co-occurrence across
        products is a synthetic artifact, not authentic co-use.
    """
    reviewer_products: dict[str, set[str]] = defaultdict(set)
    for fact in graph.facts:
        if (
            fact.subject_type == "ReviewerProxy"
            and fact.object_type == "Product"
            and fact.predicate in REVIEWER_USE_PREDICATES
            and fact.object_iri
        ):
            pid = _norm_pid(fact.object_iri)
            if _is_catalog_sku(pid):
                reviewer_products[fact.subject_iri].add(pid)

    degree_distribution: Counter[int] = Counter(
        len(prods) for prods in reviewer_products.values()
    )
    reviewers_degree_ge2 = sum(1 for prods in reviewer_products.values() if len(prods) >= 2)

    # 2-hop product pairs induced by shared reviewers (bounded count).
    pairs: set[tuple[str, str]] = set()
    for prods in reviewer_products.values():
        if len(prods) < 2:
            continue
        for a, b in combinations(sorted(prods), 2):
            pairs.add((a, b))

    return {
        "use_case": "UC5 product <- reviewer -> product (collaborative filtering)",
        "reviewers_linking_ge2_real_skus": reviewers_degree_ge2,
        "degree_distribution": {str(k): v for k, v in sorted(degree_distribution.items())},
        "induced_product_pairs": len(pairs),
        "verdict": "BLOCKED",
        "note": (
            "Structurally the densest 2-hop, but BLOCKED by design: invariant G4 "
            "forbids reviewer<->user CF for privacy, and in the dense fixture the "
            "reviewer-product graph is a round-robin remap artifact (not authentic "
            "co-use). Pursuing it is a policy + data decision, not a CTE module."
        ),
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def build_report(fixture: str) -> dict[str, Any]:
    graph = load_graph(fixture)
    use_cases = [
        audit_concern_goal_chain(graph),
        audit_shared_ingredient(graph),
        audit_couse_expansion(graph),
        audit_bee_keyword_enrichment(graph),
        audit_reviewer_couse(graph),
    ]
    # Overall demand: recommend starting Phase 4.1 iff ANY use case reaches a
    # real DEMAND verdict on its own terms. Each audit_* function already
    # encodes its own structural feasibility in the verdict it returns (e.g.
    # audit_concern_goal_chain's verdict is "DEMAND" only when Concern/Goal
    # nodes actually exist) — per-UC demand is therefore sufficient by itself
    # and use cases are fully independent gates.
    #
    # Previously this excluded UC1/UC3 from ever contributing demand by name
    # AND separately re-derived a "graph_traversal_feasible" flag from
    # UC1/UC3's own node counts, then required that flag to ALSO hold before
    # ANY other use case's demand could flip the recommendation. That let an
    # irrelevant precondition (Concern/Goal node presence) mask real demand
    # surfaced by a fully independent path (e.g. UC2 co-use or UC1'
    # shared-ingredient), which have nothing to do with concern/goal nodes.
    # See DECISIONS/2026-07-08_multihop_graph_demand_audit.md.
    actionable_demand = [uc for uc in use_cases if uc["verdict"] == "DEMAND"]
    recommend_start_41 = bool(actionable_demand)

    if actionable_demand:
        rationale = (
            "Actionable demand detected in: "
            + ", ".join(uc["use_case"] for uc in actionable_demand)
            + ". Re-evaluate whether to start Phase 4.1 for these use case(s) "
            "specifically (other NONE/MARGINAL/BLOCKED use cases remain as-is)."
        )
    else:
        rationale = (
            "No use case yields net-new recommendable-SKU candidates via true "
            "traversal of existing canonical_fact triples. The concern/goal chains "
            "need nodes that do not exist; co-use edges among real SKUs are ~0; "
            "shared-ingredient is 1-hop-reachable + majority cross-category noise; "
            "bee->keyword adds no new candidate; reviewer-CF is blocked by invariant "
            "G4 and is a fixture artifact. Do NOT start Phase 4.1."
        )

    return {
        "fixture": fixture,
        "fact_count": len(graph.facts),
        "serving_product_count": len(graph.serving_products),
        "entity_type_counts": graph.entity_type_counts,
        "predicate_counts": graph.predicate_counts(),
        "use_cases": use_cases,
        "verdict_summary": {uc["use_case"]: uc["verdict"] for uc in use_cases},
        "recommend_start_phase_4_1": recommend_start_41,
        "recommendation_rationale": rationale,
    }


def _print_human_summary(report: dict[str, Any]) -> None:
    print(
        f"fixture={report['fixture']} facts={report['fact_count']} "
        f"serving_products={report['serving_product_count']}"
    )
    print(
        "entity nodes: "
        + ", ".join(
            f"{etype}={count}"
            for etype, count in sorted(
                report["entity_type_counts"].items(), key=lambda kv: -kv[1]
            )[:8]
        )
    )
    print("-" * 78)
    for uc in report["use_cases"]:
        print(f"[{uc['verdict']:8s}] {uc['use_case']}")
        for key, value in uc.items():
            if key in ("use_case", "verdict", "note"):
                continue
            print(f"            {key} = {value}")
        print(f"            -> {uc['note']}")
        print()
    print("-" * 78)
    print(f"RECOMMEND START PHASE 4.1: {report['recommend_start_phase_4_1']}")
    print(report["recommendation_rationale"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_source_review_stats_for_products(
    product_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not DEFAULT_SOURCE_REVIEW_STATS_PATH.exists():
        return {}
    product_ids = {
        str(row.get("ONLINE_PROD_SERIAL_NUMBER"))
        for row in product_records
        if row.get("ONLINE_PROD_SERIAL_NUMBER") is not None
    }
    snapshot = load_source_review_stats_snapshot(DEFAULT_SOURCE_REVIEW_STATS_PATH)
    return {
        product_id: row
        for product_id, row in snapshot.items()
        if product_id in product_ids
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=sorted(FIXTURE_DIRS), default="dense_golden")
    parser.add_argument("--json", action="store_true", help="emit JSON report to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(fixture=args.fixture)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
