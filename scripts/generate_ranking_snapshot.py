#!/usr/bin/env python3
"""Generate and diff ranking snapshots for the dense golden fixture.

Phase 0.3 (fable_doc/03_improvement_plan.md) needs a way to see, as a diff,
how scoring_weights.yaml or semantic-rule changes shift golden-profile
recommendation rankings. Today that review happens by eyeballing the
frontend. This script fixes the golden-profile x category-tab top-N
recommendation output into a deterministic JSON snapshot, and by default
compares the current pipeline output against that stored snapshot so a
reviewer gets a human-readable diff (rank moves, score changes, new/dropped
products) instead of having to reconstruct it manually.

It reuses the same in-memory pipeline as scripts/audit_recommendation_evidence.py
(category prefilter -> candidate generation -> scorer -> reranker) via
``build_audit_report`` -- no DB, no network, no changes to that script.

Usage:
    # Compare current recommendations against the stored snapshot.
    python scripts/generate_ranking_snapshot.py

    # Accept the current recommendations as the new snapshot.
    python scripts/generate_ranking_snapshot.py --update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_recommendation_evidence import build_audit_report  # noqa: E402


DEFAULT_TOP_K = 10
DEFAULT_FIXTURE = "dense_golden"
DEFAULT_KG_MODE = "on"
SNAPSHOT_PATH = ROOT / "tests" / "fixtures" / "ranking_snapshots" / "dense_golden.json"
SNAPSHOT_SCHEMA_VERSION = 1
UPDATE_HINT = "python scripts/generate_ranking_snapshot.py --update"


def build_snapshot(
    *,
    fixture: str = DEFAULT_FIXTURE,
    kg_mode: str = DEFAULT_KG_MODE,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Run the recommendation pipeline for every golden profile x category tab.

    Returns a JSON-serializable, deterministically ordered snapshot: sorted
    combination keys, each holding a top-N product list sorted by
    (total_score desc, product_id asc) so tie-breaking never depends on
    incidental dict/list iteration order upstream.
    """
    report = build_audit_report(
        fixture=fixture,
        kg_mode=kg_mode,
        top_k=top_k,
        category_group=None,
    )

    combinations: dict[str, Any] = {}
    for scenario in report["scenarios"]:
        key = _combination_key(scenario["user_id"], scenario["category_group"])
        combinations[key] = {
            "user_id": scenario["user_id"],
            "category_group": scenario["category_group"],
            "coverage_status": scenario["coverage_status"],
            "products": _snapshot_products(scenario["top_products"]),
        }

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "fixture": fixture,
        "kg_mode": kg_mode,
        "top_k": top_k,
        "combination_count": len(combinations),
        "combinations": dict(sorted(combinations.items())),
    }


def _combination_key(user_id: str, category_group: str) -> str:
    return f"{user_id}::{category_group}"


def _snapshot_products(top_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "rank": int(row["rank"]),
            "product_id": str(row["product_id"]),
            "total_score": round(float(row["final_score"]), 4),
            "score_layers": _rounded_score_layers(row["score_layers"]),
            "evidence_families": sorted(str(f) for f in row["evidence_families"]),
        }
        for row in top_products
    ]
    # Deterministic tie-break: sort by (score desc, product_id asc) so any
    # future scoring change that produces equal scores still yields the same
    # snapshot ordering on every run, regardless of incidental upstream
    # iteration order. The `rank` field itself is left untouched -- it is the
    # reranker's own judgment of placement and is not renumbered here.
    rows.sort(key=lambda row: (-row["total_score"], row["product_id"]))
    return rows


def _rounded_score_layers(score_layers: dict[str, float]) -> dict[str, float]:
    return {str(layer): round(float(value), 4) for layer, value in sorted(score_layers.items())}


def load_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"no snapshot found at {path}. Run '{UPDATE_HINT}' to create one."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    payload = _snapshot_bytes(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def _snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    text = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def diff_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Compare two snapshots and return a list of human-readable diff lines.

    An empty list means the snapshots are equivalent (no reportable change).
    """
    lines: list[str] = []
    baseline_combos: dict[str, Any] = baseline.get("combinations", {})
    current_combos: dict[str, Any] = current.get("combinations", {})

    baseline_keys = set(baseline_combos)
    current_keys = set(current_combos)

    for key in sorted(current_keys - baseline_keys):
        lines.append(f"[{key}] NEW combination (not present in stored snapshot)")
    for key in sorted(baseline_keys - current_keys):
        lines.append(f"[{key}] REMOVED combination (present in stored snapshot only)")

    for key in sorted(baseline_keys & current_keys):
        lines.extend(_diff_combination(key, baseline_combos[key], current_combos[key]))

    return lines


def _diff_combination(key: str, baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    baseline_status = baseline.get("coverage_status")
    current_status = current.get("coverage_status")
    if baseline_status != current_status:
        lines.append(f"[{key}] coverage_status changed: {baseline_status} -> {current_status}")

    baseline_products: list[dict[str, Any]] = baseline.get("products", [])
    current_products: list[dict[str, Any]] = current.get("products", [])
    baseline_by_id = {row["product_id"]: row for row in baseline_products}
    current_by_id = {row["product_id"]: row for row in current_products}

    new_ids = sorted(set(current_by_id) - set(baseline_by_id))
    dropped_ids = sorted(set(baseline_by_id) - set(current_by_id))
    for product_id in new_ids:
        row = current_by_id[product_id]
        lines.append(
            f"[{key}] + NEW product {product_id} entered at rank {row['rank']} "
            f"(total_score={row['total_score']})"
        )
    for product_id in dropped_ids:
        row = baseline_by_id[product_id]
        lines.append(
            f"[{key}] - DROPPED product {product_id} (was rank {row['rank']}, "
            f"total_score={row['total_score']})"
        )

    for product_id in sorted(set(baseline_by_id) & set(current_by_id)):
        lines.extend(_diff_product_row(key, product_id, baseline_by_id[product_id], current_by_id[product_id]))

    return lines


def _diff_product_row(
    key: str,
    product_id: str,
    baseline_row: dict[str, Any],
    current_row: dict[str, Any],
) -> list[str]:
    lines: list[str] = []

    if baseline_row["rank"] != current_row["rank"]:
        lines.append(
            f"[{key}] ~ product {product_id} rank changed: "
            f"{baseline_row['rank']} -> {current_row['rank']}"
        )

    if baseline_row["total_score"] != current_row["total_score"]:
        lines.append(
            f"[{key}] ~ product {product_id} total_score changed: "
            f"{baseline_row['total_score']} -> {current_row['total_score']}"
        )

    layer_changes = _score_layer_changes(baseline_row.get("score_layers", {}), current_row.get("score_layers", {}))
    if layer_changes:
        lines.append(f"[{key}] ~ product {product_id} score_layers changed: {layer_changes}")

    baseline_families = baseline_row.get("evidence_families", [])
    current_families = current_row.get("evidence_families", [])
    if baseline_families != current_families:
        lines.append(
            f"[{key}] ~ product {product_id} evidence_families changed: "
            f"{baseline_families} -> {current_families}"
        )

    return lines


def _score_layer_changes(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, str]:
    changes: dict[str, str] = {}
    for layer in sorted(set(baseline) | set(current)):
        before = baseline.get(layer, 0.0)
        after = current.get(layer, 0.0)
        if before != after:
            changes[layer] = f"{before} -> {after}"
    return changes


def format_diff_report(diff_lines: list[str], *, snapshot_path: Path) -> str:
    if not diff_lines:
        return "no ranking changes detected (current output matches stored snapshot)"
    header = f"ranking snapshot diff vs {snapshot_path} ({len(diff_lines)} change(s)):"
    body = "\n".join(f"  {line}" for line in diff_lines)
    footer = f"if this change is intended, run: {UPDATE_HINT}"
    return "\n".join([header, body, footer])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE, help="fixture name passed to build_audit_report")
    parser.add_argument("--kg-mode", choices=("on", "off"), default=DEFAULT_KG_MODE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--snapshot-path",
        type=Path,
        default=SNAPSHOT_PATH,
        help="path to the stored snapshot JSON (default: tests/fixtures/ranking_snapshots/dense_golden.json)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="overwrite the stored snapshot with the current pipeline output instead of diffing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.top_k <= 0:
        print("error: --top-k must be positive", file=sys.stderr)
        return 2

    current = build_snapshot(fixture=args.fixture, kg_mode=args.kg_mode, top_k=args.top_k)

    if args.update:
        write_snapshot(current, args.snapshot_path)
        print(
            f"updated snapshot: {args.snapshot_path} "
            f"({current['combination_count']} combinations)"
        )
        return 0

    try:
        baseline = load_snapshot(args.snapshot_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    diff_lines = diff_snapshots(baseline, current)
    print(format_diff_report(diff_lines, snapshot_path=args.snapshot_path))
    return 1 if diff_lines else 0


if __name__ == "__main__":
    raise SystemExit(main())
