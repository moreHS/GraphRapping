"""Phase 0.3 (fable_doc/03_improvement_plan.md) — ranking snapshot regression.

Scoring/rule changes (scoring_weights.yaml weights, semantic rules, category
classification, etc.) can silently shift golden-profile recommendation
rankings. This test freezes that behavior: it rebuilds the profile x
category-tab top-N snapshot with the current code
(scripts.generate_ranking_snapshot.build_snapshot, the same in-memory
prefilter -> candidate -> scorer -> reranker pipeline the demo server uses)
and compares it against the committed baselines:

- dense_golden (32 products, 6 golden profiles):
  tests/fixtures/ranking_snapshots/dense_golden.json
- wide (517 products, 50 users) — Phase 7 A4 baseline, the regression asset
  C2's promotion-gate work must diff against:
  tests/fixtures/ranking_snapshots/wide_golden.json

On mismatch, the assertion message is the same human-readable diff
scripts/generate_ranking_snapshot.py prints on the CLI (rank moves, score
changes, new/dropped products, coverage_status flips) -- no need to
reconstruct it by hand from a raw pytest dict diff.

Updating a baseline after an INTENDED ranking change:

    python scripts/generate_ranking_snapshot.py --update
    python scripts/generate_ranking_snapshot.py --fixture wide \
        --snapshot-path tests/fixtures/ranking_snapshots/wide_golden.json --update

then review the resulting git diff of the snapshot JSON and commit it
alongside the code change that caused it. Do not update a baseline to
silence an unexplained failure.
"""

from __future__ import annotations

from pathlib import Path
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


WIDE_FIXTURE = "wide"
WIDE_SNAPSHOT_PATH = SNAPSHOT_PATH.parent / "wide_golden.json"


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


@pytest.fixture(scope="module")
def wide_baseline_snapshot() -> dict[str, Any]:
    return load_snapshot(WIDE_SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def wide_current_snapshot() -> dict[str, Any]:
    """Same as current_snapshot but over the wide (517-product, 50-user)
    fixture. One extra full-load run per test session; module scope keeps it
    to exactly one."""
    return build_snapshot(fixture=WIDE_FIXTURE, kg_mode=DEFAULT_KG_MODE, top_k=DEFAULT_TOP_K)


def _assert_baseline_metadata(baseline: dict[str, Any], *, fixture: str) -> None:
    """Catches a stale/hand-edited baseline (wrong fixture/kg_mode/top_k, or a
    combination_count that no longer matches the combinations dict) with a
    clear message instead of a wall of unrelated per-product diff lines."""
    assert baseline["fixture"] == fixture
    assert baseline["kg_mode"] == DEFAULT_KG_MODE
    assert baseline["top_k"] == DEFAULT_TOP_K
    assert baseline["combination_count"] > 0
    assert baseline["combination_count"] == len(baseline["combinations"])


def _assert_matches_baseline(
    baseline: dict[str, Any], current: dict[str, Any], *, snapshot_path: Path,
) -> None:
    diff_lines = diff_snapshots(baseline, current)
    assert not diff_lines, format_diff_report(diff_lines, snapshot_path=snapshot_path)


# ---------------------------------------------------------------------------
# dense_golden
# ---------------------------------------------------------------------------


def test_baseline_snapshot_metadata_matches_generator_defaults(
    baseline_snapshot: dict[str, Any],
) -> None:
    _assert_baseline_metadata(baseline_snapshot, fixture=DEFAULT_FIXTURE)


def test_current_ranking_matches_dense_golden_baseline(
    baseline_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
) -> None:
    """The regression check: current pipeline output vs the committed baseline.

    See the module docstring for how to update the baseline after a reviewed,
    intended ranking change.
    """
    _assert_matches_baseline(baseline_snapshot, current_snapshot, snapshot_path=SNAPSHOT_PATH)


# ---------------------------------------------------------------------------
# wide (Phase 7 A4 baseline — the asset C2 gate changes must diff against)
# ---------------------------------------------------------------------------


def test_wide_baseline_snapshot_metadata_is_consistent(
    wide_baseline_snapshot: dict[str, Any],
) -> None:
    _assert_baseline_metadata(wide_baseline_snapshot, fixture=WIDE_FIXTURE)


def test_current_ranking_matches_wide_golden_baseline(
    wide_baseline_snapshot: dict[str, Any],
    wide_current_snapshot: dict[str, Any],
) -> None:
    """Wide-catalog regression check. C2 (promotion-gate catalog-aware
    relaxation) is expected to move this snapshot intentionally — such a
    change must go through the documented re-approval workflow (review the
    regenerated JSON diff), not by silencing the failure."""
    _assert_matches_baseline(
        wide_baseline_snapshot, wide_current_snapshot, snapshot_path=WIDE_SNAPSHOT_PATH,
    )
