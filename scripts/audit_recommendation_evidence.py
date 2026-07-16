#!/usr/bin/env python3
"""Audit recommendation evidence usage for GraphRapping fixtures.

The audit intentionally uses the same recommendation primitives as the demo
server: category prefilter -> candidate generation -> scorer -> reranker. It
does not touch DBs or networks.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.enums import RecommendationMode  # noqa: E402
from src.jobs.run_full_load import FullLoadConfig, run_full_load  # noqa: E402
from src.loaders.source_review_stats_loader import load_source_review_stats_snapshot  # noqa: E402
from src.rec.candidate_generator import (  # noqa: E402
    CandidateProduct,
    build_similar_boost_index,
    extract_owned_product_ids,
    generate_candidates_prefiltered,
)
from src.rec.category_groups import (  # noqa: E402
    RECOMMEND_CATEGORY_DEFS,
    RECOMMEND_CATEGORY_LABELS,
    classify_product_category_group,
    recommend_category_counts,
)
from src.rec.product_profile_enrichment import enrich_product_profiles_by_master  # noqa: E402
from src.rec.product_similarity import (  # noqa: E402
    SimilarProductSignal,
    build_idf,
    build_product_nodes,
    build_similarity_signals,
    keyword_signals_from_product_signals,
)
from src.rec.reranker import rerank  # noqa: E402
from src.rec.scorer import ScoredProduct, Scorer  # noqa: E402


FIXTURE_DIRS = {
    "wide": ROOT / "mockdata",
    "dense_golden": ROOT / "mockdata" / "dense_golden",
}
DEFAULT_SOURCE_REVIEW_STATS_PATH = (
    ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
)
DEFAULT_TOP_K = 10
SERVER_CANDIDATE_LIMIT = 50
SERVER_DIVERSITY_WEIGHT = 0.1
SCORE_LAYER_KEYS = (
    "master_truth_score",
    "review_graph_score",
    "review_graph_weak_evidence_score",
    "product_activity_score",
    "profile_fit_score",
    "purchase_behavior_score",
    "source_trust_score",
)
EVIDENCE_FAMILIES = (
    "PRODUCT_MASTER_TRUTH",
    "REVIEW_GRAPH_RELATION",
    "REVIEW_GRAPH_WEAK_RELATION",
    "PURCHASE_BEHAVIOR",
)


def build_audit_report(
    *,
    fixture: str = "dense_golden",
    kg_mode: str = "on",
    top_k: int = DEFAULT_TOP_K,
    user_id: str | None = None,
    category_group: str | None = None,
) -> dict[str, Any]:
    """Run the in-memory pipeline and return a JSON-serializable audit report."""
    fixture_dir = _fixture_dir(fixture)
    if kg_mode not in {"on", "off"}:
        raise ValueError("kg_mode must be 'on' or 'off'")
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    product_records = _load_json(fixture_dir / "product_catalog_es.json")
    user_profiles = _load_json(fixture_dir / "user_profiles_normalized.json")
    review_path = fixture_dir / "review_triples_raw.json"
    source_review_stats = _load_source_review_stats_for_products(product_records)

    # run_full_load prints progress; keep CLI JSON clean and tests quiet.
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(review_path),
            product_es_records=product_records,
            user_profiles=user_profiles,
            kg_mode=kg_mode,
            source_review_stats_by_product=source_review_stats,
        ))

    serving_products = enrich_product_profiles_by_master(
        result.serving_products,
        result.batch_result.get("product_masters", {}),
    )
    product_map = {str(p["product_id"]): p for p in serving_products}
    users = list(result.serving_users)
    if user_id:
        users = [u for u in users if str(u.get("user_id")) == user_id]
        if not users:
            raise ValueError(f"user_id not found in fixture '{fixture}': {user_id}")

    requested_groups = _requested_category_groups(category_group)
    category_counts = recommend_category_counts(serving_products)
    scorer = Scorer()
    scorer.load_config()

    # Phase 8 G4: build the ungated similarity index once (corpus-level), the
    # same activation state the serving stores compute at load — so the audit
    # and the snapshots it feeds see the exact web-pipeline boost behaviour.
    similar_ungated = _build_ungated_similarity(result.batch_result, serving_products)

    scenarios = [
        _build_scenario(
            user=user,
            category_group=group,
            product_map=product_map,
            scorer=scorer,
            top_k=top_k,
            similar_ungated=similar_ungated,
        )
        for user in users
        for group in requested_groups
    ]

    return {
        "fixture": fixture,
        "fixture_dir": str(fixture_dir),
        "kg_mode": kg_mode,
        "top_k": top_k,
        "mode": RecommendationMode.EXPLORE.value,
        "review_count": result.review_count,
        "product_count": result.product_count,
        "user_count": result.user_count,
        "serving_product_count": len(serving_products),
        "serving_user_count": result.serving_user_count,
        "signal_count": result.signal_count,
        "quarantine_count": result.quarantine_count,
        "category_counts": category_counts,
        "user_ids": [str(u.get("user_id")) for u in users],
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }


def _build_ungated_similarity(
    batch_result: dict[str, Any],
    serving_products: list[dict[str, Any]],
) -> dict[str, list[SimilarProductSignal]]:
    """Phase 8 G4: the ungated (category_gate=False) similarity index for the
    similar-boost channel — the same 3-function combo the serving stores run at
    load (``src/web/serving_store.build_and_attach_similarity``), so the audit /
    snapshot path sees the same activation state as the web pipeline. The
    keyword axis is sourced from the per-product signal index (the demo-side
    ``wrapped_signal`` equivalent, mirroring ``src/web/state.load_demo_data``).
    Labels are irrelevant to boost strengths, so no label index is passed."""
    product_signals: dict[str, list[dict[str, Any]]] = {}
    for review in batch_result.get("review_results", []):
        for sig in review.get("signals", []):
            pid = sig.get("target_product_id")
            if pid:
                product_signals.setdefault(str(pid), []).append(sig)
    keyword_triples = keyword_signals_from_product_signals(product_signals)
    nodes = build_product_nodes(serving_products, keyword_triples)
    return build_similarity_signals(
        nodes, serving_products, idf=build_idf(nodes), category_gate=False,
    )


def _build_scenario(
    *,
    user: dict[str, Any],
    category_group: str,
    product_map: dict[str, dict[str, Any]],
    scorer: Scorer,
    top_k: int,
    similar_ungated: dict[str, list[SimilarProductSignal]],
) -> dict[str, Any]:
    prefiltered_product_ids = _prefilter_product_ids(product_map, category_group)
    # Phase 8 G4: same assembly as the server pipeline — owned anchors × the
    # corpus-wide ungated index; empty index (no in-corpus owned anchor with
    # neighbours) stays None = dormant channel.
    similar_boost = build_similar_boost_index(
        extract_owned_product_ids(user), similar_ungated,
    ) or None
    candidates = generate_candidates_prefiltered(
        user_profile=user,
        prefiltered_product_ids=prefiltered_product_ids,
        product_profiles_by_id=product_map,
        mode=RecommendationMode.EXPLORE,
        max_candidates=SERVER_CANDIDATE_LIMIT,
        similar_boost=similar_boost,
    )

    scored_pairs: list[tuple[CandidateProduct, ScoredProduct]] = []
    for candidate in candidates:
        product = product_map.get(candidate.product_id)
        if not product:
            continue
        scored_pairs.append((
            candidate,
            scorer.score(user, product, candidate.overlap_concepts),
        ))
    scored_pairs.sort(key=lambda pair: pair[1].final_score, reverse=True)

    reranked = rerank(
        [scored for _, scored in scored_pairs],
        product_profiles=product_map,
        diversity_weight=SERVER_DIVERSITY_WEIGHT,
        top_k=top_k,
    )
    candidate_by_product = {candidate.product_id: candidate for candidate, _ in scored_pairs}
    scored_by_product = {scored.product_id: scored for _, scored in scored_pairs}
    top_products = [
        _top_product_row(
            reranked_product=reranked_product,
            candidate=candidate_by_product[reranked_product.product_id],
            scored=scored_by_product[reranked_product.product_id],
            product=product_map[reranked_product.product_id],
        )
        for reranked_product in reranked
        if reranked_product.product_id in candidate_by_product
        and reranked_product.product_id in scored_by_product
        and reranked_product.product_id in product_map
    ]

    return {
        "user_id": str(user.get("user_id")),
        "category_group": category_group,
        "category_label": RECOMMEND_CATEGORY_LABELS[category_group],
        "coverage_status": "ok" if candidates else "no_candidates",
        "category_filtered_count": len(prefiltered_product_ids),
        "candidate_count": len(candidates),
        "top_product_count": len(top_products),
        "top_products": top_products,
        "evidence_family_counts": _evidence_family_counts(top_products),
        "candidate_evidence_family_counts": _candidate_evidence_family_counts(candidates),
        "score_layer_totals": _score_layer_totals(top_products),
        "promoted_relation_hit_count": sum(
            1 for row in top_products
            if "REVIEW_GRAPH_RELATION" in row["evidence_families"]
        ),
        "weak_relation_hit_count": sum(
            1 for row in top_products
            if "REVIEW_GRAPH_WEAK_RELATION" in row["evidence_families"]
        ),
        "source_stats_contribution_count": sum(
            1 for row in top_products
            if row["score_layers"].get("source_trust_score", 0.0) > 0
        ),
        "owned_family_candidate_count": sum(
            1 for c in candidates
            if c.already_owned or c.owned_family_match or c.repurchased_family_match
        ),
        "owned_family_suppression_count": sum(
            1 for row in top_products
            if row["feature_contributions"].get("exact_owned_penalty", 0.0) < 0
            or row["feature_contributions"].get("owned_family_penalty", 0.0) < 0
        ),
        "purchase_path_count": sum(
            len(row["eligibility"].get("purchase_paths", []))
            for row in top_products
        ),
        "purchase_score_nonzero_count": sum(
            1 for row in top_products
            if _has_purchase_history_contribution(row)
        ),
        "source_stats_only_eligibility_count": sum(
            1 for row in top_products
            if row["source_trust_score"] > 0 and not row["evidence_families"]
        ),
    }


def _top_product_row(
    *,
    reranked_product: Any,
    candidate: CandidateProduct,
    scored: ScoredProduct,
    product: dict[str, Any],
) -> dict[str, Any]:
    score_layers = _normalized_score_layers(scored.score_layers)
    source_trust_score = score_layers.get("source_trust_score", 0.0)
    return {
        "rank": int(reranked_product.final_rank) + 1,
        "product_id": str(reranked_product.product_id),
        "product_name": _product_name(product),
        "brand_name": product.get("brand_name") or product.get("brand_id"),
        "category_group": classify_product_category_group(product),
        "candidate_bucket": candidate.candidate_bucket,
        "already_owned": bool(candidate.already_owned),
        "owned_family_match": bool(candidate.owned_family_match),
        "repurchased_family_match": bool(candidate.repurchased_family_match),
        "raw_score": scored.raw_score,
        "shrinked_score": scored.shrinked_score,
        "final_score": reranked_product.final_score,
        "rank_score": reranked_product.rank_score,
        "diversity_bonus": reranked_product.diversity_bonus,
        "support_count": scored.support_count,
        "score_layers": score_layers,
        "source_trust_score": source_trust_score,
        "source_review_count_6m": _number_or_none(product.get("source_review_count_6m")),
        "source_avg_rating_6m": _number_or_none(product.get("source_avg_rating_6m")),
        "feature_contributions": _rounded_mapping(scored.feature_contributions),
        "overlap_concepts": list(candidate.overlap_concepts),
        "evidence_families": list(candidate.eligibility.evidence_families),
        "eligibility": candidate.eligibility.to_dict(),
    }


def _has_purchase_history_contribution(row: dict[str, Any]) -> bool:
    purchase_history_features = {
        "purchase_loyalty_score",
        "exact_owned_penalty",
        "owned_family_penalty",
        "same_family_explore_bonus",
        "repurchase_family_affinity",
    }
    contributions = row.get("feature_contributions") or {}
    return any(abs(float(contributions.get(feature, 0.0))) > 0 for feature in purchase_history_features)


def _fixture_dir(fixture: str) -> Path:
    try:
        fixture_dir = FIXTURE_DIRS[fixture]
    except KeyError as exc:
        raise ValueError(f"unknown fixture: {fixture}") from exc
    if not fixture_dir.exists():
        raise FileNotFoundError(f"fixture directory does not exist: {fixture_dir}")
    return fixture_dir


def _requested_category_groups(category_group: str | None) -> list[str]:
    if category_group is None:
        return [str(item["group"]) for item in RECOMMEND_CATEGORY_DEFS]
    if category_group not in RECOMMEND_CATEGORY_LABELS:
        allowed = ", ".join(RECOMMEND_CATEGORY_LABELS)
        raise ValueError(f"unknown category_group: {category_group}; allowed: {allowed}")
    return [category_group]


def _prefilter_product_ids(product_map: dict[str, dict[str, Any]], category_group: str) -> list[str]:
    if category_group == "all":
        return list(product_map.keys())
    return [
        pid
        for pid, product in product_map.items()
        if classify_product_category_group(product) == category_group
    ]


def _product_name(product: dict[str, Any]) -> str:
    for key in ("product_name", "ONLINE_PROD_NAME", "prd_nm", "representative_product_name", "rprs_prd_nm"):
        value = product.get(key)
        if value:
            return str(value)
    return str(product.get("product_id", ""))


def _evidence_family_counts(top_products: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        family
        for row in top_products
        for family in row.get("evidence_families", [])
    )
    return {family: counts.get(family, 0) for family in EVIDENCE_FAMILIES}


def _candidate_evidence_family_counts(candidates: list[CandidateProduct]) -> dict[str, int]:
    counts = Counter(
        family
        for candidate in candidates
        for family in candidate.eligibility.evidence_families
    )
    return {family: counts.get(family, 0) for family in EVIDENCE_FAMILIES}


def _score_layer_totals(top_products: list[dict[str, Any]]) -> dict[str, float]:
    totals = {
        layer: sum(row["score_layers"].get(layer, 0.0) for row in top_products)
        for layer in SCORE_LAYER_KEYS
    }
    return {layer: round(value, 4) for layer, value in totals.items()}


def _normalized_score_layers(score_layers: dict[str, float]) -> dict[str, float]:
    return {
        layer: round(float(score_layers.get(layer, 0.0)), 4)
        for layer in SCORE_LAYER_KEYS
    }


def _rounded_mapping(values: dict[str, float]) -> dict[str, float]:
    return {str(k): round(float(v), 4) for k, v in sorted(values.items())}


def _number_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return numeric


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


def _print_human_summary(report: dict[str, Any]) -> None:
    print(
        f"fixture={report['fixture']} kg_mode={report['kg_mode']} "
        f"reviews={report['review_count']} products={report['product_count']} "
        f"users={report['user_count']} scenarios={report['scenario_count']}"
    )
    for scenario in report["scenarios"]:
        families = ", ".join(
            f"{family}={count}"
            for family, count in scenario["evidence_family_counts"].items()
            if count
        ) or "none"
        layers = scenario["score_layer_totals"]
        print(
            f"- {scenario['user_id']} / {scenario['category_group']}: "
            f"candidates={scenario['candidate_count']} top={scenario['top_product_count']} "
            f"relations={scenario['promoted_relation_hit_count']} "
            f"weak={scenario['weak_relation_hit_count']} "
            f"source_trust={scenario['source_stats_contribution_count']} "
            f"families=[{families}] "
            f"graph_score={layers.get('review_graph_score', 0.0):.4f}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=sorted(FIXTURE_DIRS), default="dense_golden")
    parser.add_argument("--kg-mode", choices=("on", "off"), default="on")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--user-id")
    parser.add_argument("--category-group", choices=tuple(RECOMMEND_CATEGORY_LABELS))
    parser.add_argument("--json", action="store_true", help="emit JSON report to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_audit_report(
        fixture=args.fixture,
        kg_mode=args.kg_mode,
        top_k=args.top_k,
        user_id=args.user_id,
        category_group=args.category_group,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
