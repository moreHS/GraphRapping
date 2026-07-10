"""Phase 0.3 (fable_doc/03_improvement_plan.md) — ranking snapshot regression.

Scoring/rule changes (scoring_weights.yaml weights, semantic rules, category
classification, etc.) can silently shift golden-profile recommendation
rankings. This test freezes that behavior: it rebuilds the golden-profile x
category-tab top-N snapshot with the current code
(scripts.generate_ranking_snapshot.build_snapshot, the same in-memory
prefilter -> candidate -> scorer -> reranker pipeline the demo server uses)
and compares it against the committed baseline at
tests/fixtures/ranking_snapshots/dense_golden.json.

On mismatch, the assertion message is the same human-readable diff
scripts/generate_ranking_snapshot.py prints on the CLI (rank moves, score
changes, new/dropped products, coverage_status flips) -- no need to
reconstruct it by hand from a raw pytest dict diff.

Updating the baseline after an INTENDED ranking change:

    python scripts/generate_ranking_snapshot.py --update

then review the resulting git diff of dense_golden.json and commit it
alongside the code change that caused it. Do not update the baseline to
silence an unexplained failure.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.generate_ranking_snapshot import (
    DEFAULT_FIXTURE,
    DEFAULT_KG_MODE,
    DEFAULT_TOP_K,
    SNAPSHOT_PATH,
    build_snapshot,
    diff_snapshots,
    format_diff_report,
    load_snapshot,
)


@pytest.fixture(scope="module")
def baseline_snapshot() -> dict[str, Any]:
    return load_snapshot(SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def current_snapshot() -> dict[str, Any]:
    """Run the full in-memory recommendation pipeline once per module.

    build_snapshot() runs run_full_load over the dense golden fixture for
    every golden profile x category tab -- module scope avoids re-running
    that pipeline for each assertion in this file (same rationale as
    test_expected_evidence_family_baseline.py's scenarios_by_key fixture).
    """
    return build_snapshot(fixture=DEFAULT_FIXTURE, kg_mode=DEFAULT_KG_MODE, top_k=DEFAULT_TOP_K)


def test_baseline_snapshot_metadata_matches_generator_defaults(
    baseline_snapshot: dict[str, Any],
) -> None:
    """Sanity check on the committed fixture before diffing its contents.

    Catches a stale/hand-edited baseline (wrong fixture/kg_mode/top_k, or a
    combination_count that no longer matches the combinations dict) with a
    clear message instead of a wall of unrelated per-product diff lines.
    """
    assert baseline_snapshot["fixture"] == DEFAULT_FIXTURE
    assert baseline_snapshot["kg_mode"] == DEFAULT_KG_MODE
    assert baseline_snapshot["top_k"] == DEFAULT_TOP_K
    assert baseline_snapshot["combination_count"] > 0
    assert baseline_snapshot["combination_count"] == len(baseline_snapshot["combinations"])


def test_current_ranking_matches_dense_golden_baseline(
    baseline_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
) -> None:
    """The regression check: current pipeline output vs the committed baseline.

    See the module docstring for how to update the baseline after a reviewed,
    intended ranking change.
    """
    diff_lines = diff_snapshots(baseline_snapshot, current_snapshot)
    assert not diff_lines, format_diff_report(diff_lines, snapshot_path=SNAPSHOT_PATH)
