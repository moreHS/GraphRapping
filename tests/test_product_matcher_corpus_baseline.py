"""Phase 3.3 corpus regression baseline.

Phase 3.3 (Korean-aware jamo-decomposition fuzzy matching, see
`src/link/product_matcher.py`) has a completion criterion that previously
existed only as prose in code comments: true-positive auto-accepts should go
up, and false-positive auto-accepts must not increase. This test makes that
criterion mechanically checkable by running `product_matcher.match_product`
directly over every review in the full 906-review mockdata fixture and
comparing the resulting distribution / true-positive / false-positive counts
against a baseline pinned to the current implementation's measured output.

Methodology:
- Candidates: `ProductIndex` built from `mockdata/product_catalog_es.json`
  via the same loader (`load_products_from_json`) already used for this
  purpose in test_pipeline_stage_logging.py's `_pipeline_deps()`. No aliases
  are added (same precedent), so this measures `product_matcher`'s
  name-only matching in isolation.
- Ground truth oracle: each review carries the source system's own
  `channel` + `source_product_id`; each catalog row carries the same
  identity via `SOURCE_CHANNEL` + `SOURCE_PRODUCT_ID` (which resolves to
  `ONLINE_PROD_SERIAL_NUMBER`, the product_id). Cross-referencing these two
  — independent of product_matcher's name-based fuzzy logic — gives an
  oracle for whether an auto-accepted match is actually correct, without
  requiring hand-labeled ground truth.
- "auto-accept" = `match_status != QUARANTINE` (NORM / ALIAS / FUZZY-auto,
  i.e. score >= FUZZY_AUTO_ACCEPT). Of those:
    - true positive: `matched_product_id` equals the ground-truth product_id.
    - false positive: it does not.
    - unknown-truth auto-accept: the review's own (channel, source_product_id)
      does not resolve to a unique catalog product (e.g. missing fields, or
      duplicate product names under different product_ids) — conservatively
      tracked as its own bucket rather than silently counted as either a true
      or false positive.

NOTE: production's `process_review` (src/jobs/run_daily_pipeline.py) tries
`_match_product_by_source_id` FIRST and only falls back to `match_product`
(this module's target) when that lookup misses. This test intentionally
bypasses that short-circuit and calls `match_product` directly for every
review, matching the same "run the matcher against the whole fixture"
measurement methodology already narrated in product_matcher.py's
`_FUZZY_NOISE_RE` docstring (the "wrong auto-accepts rose from 24 to 122 on
the 906-review fixture" experiment).

Updating the baseline: if a deliberate matching-logic change shifts these
numbers, re-run this test's counts (e.g. via a scratch script using the same
helpers) and update the EXPECTED_* constants below in the same change, with
the reasoning captured in the commit / DECISIONS entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.link.product_matcher import MatchStatus, ProductIndex, match_product
from src.loaders.product_loader import load_products_from_json

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_CATALOG_PATH = REPO_ROOT / "mockdata" / "product_catalog_es.json"
REVIEW_FIXTURE_PATH = REPO_ROOT / "mockdata" / "review_triples_raw.json"

# Baseline measured against the current implementation (906-review /
# 517-product mockdata fixture, post candidate-side _has_hangul gate on the
# Korean-aware fuzzy blend). See module docstring for update instructions.
EXPECTED_TOTAL_REVIEWS = 906
EXPECTED_TOTAL_PRODUCTS = 517
EXPECTED_DISTRIBUTION = {
    "norm": 0,
    "alias": 0,
    "fuzzy_auto": 893,
    "fuzzy_manual_review": 8,
    "no_match": 5,
}
EXPECTED_TRUE_POSITIVE = 869
EXPECTED_FALSE_POSITIVE = 23
EXPECTED_UNKNOWN_TRUTH_AUTO_ACCEPT = 1


def _load_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_ground_truth(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Map (channel, source_product_id) -> true product_id from raw catalog fields.

    Deliberately reads the raw ES-shaped fixture fields directly rather than
    going through `src.loaders.product_truth_merge` / `product_loader`'s
    field-precedence rules, so this oracle stays independent of loader
    internals and of the module under test.
    """
    ground_truth: dict[tuple[str, str], str] = {}
    for row in catalog:
        product_id = str(row.get("ONLINE_PROD_SERIAL_NUMBER") or "").strip()
        channel = str(row.get("SOURCE_CHANNEL") or "").strip()
        source_product_id = str(row.get("SOURCE_PRODUCT_ID") or "").strip()
        if not product_id or not channel or not source_product_id:
            continue
        key = (channel, source_product_id)
        existing = ground_truth.get(key)
        assert existing is None or existing == product_id, (
            f"ambiguous ground truth for {key}: {existing} vs {product_id} "
            "-- fixture is no longer a clean one-source-identity-per-product catalog"
        )
        ground_truth[key] = product_id
    return ground_truth


@pytest.mark.timeout(60)
def test_product_matcher_corpus_baseline() -> None:
    reviews = _load_json(REVIEW_FIXTURE_PATH)
    catalog = _load_json(PRODUCT_CATALOG_PATH)
    assert len(reviews) == EXPECTED_TOTAL_REVIEWS, (
        "mockdata/review_triples_raw.json row count changed -- re-measure "
        "the corpus baseline before updating this assertion"
    )
    assert len(catalog) == EXPECTED_TOTAL_PRODUCTS, (
        "mockdata/product_catalog_es.json row count changed -- re-measure "
        "the corpus baseline before updating this assertion"
    )

    product_result = load_products_from_json(str(PRODUCT_CATALOG_PATH))
    index: ProductIndex = product_result.product_index
    ground_truth = _build_ground_truth(catalog)

    distribution = {
        "norm": 0, "alias": 0, "fuzzy_auto": 0,
        "fuzzy_manual_review": 0, "no_match": 0,
    }
    true_positive = 0
    false_positive = 0
    unknown_truth_auto_accept = 0

    for review in reviews:
        channel = str(review.get("channel") or "").strip()
        source_product_id = str(review.get("source_product_id") or "").strip()
        truth_product_id = ground_truth.get((channel, source_product_id))

        result = match_product(review.get("brnd_nm"), review.get("prod_nm"), index)

        if result.match_status == MatchStatus.NORM:
            distribution["norm"] += 1
        elif result.match_status == MatchStatus.ALIAS:
            distribution["alias"] += 1
        elif result.match_status == MatchStatus.FUZZY:
            distribution["fuzzy_auto"] += 1
        elif result.match_method == "fuzzy_manual_review":
            distribution["fuzzy_manual_review"] += 1
        else:
            distribution["no_match"] += 1

        if result.match_status == MatchStatus.QUARANTINE:
            continue  # not auto-accepted (manual-review band or true no-match)

        if truth_product_id is None:
            unknown_truth_auto_accept += 1
        elif result.matched_product_id == truth_product_id:
            true_positive += 1
        else:
            false_positive += 1

    assert distribution == EXPECTED_DISTRIBUTION, (
        f"match_status/method distribution drifted: {distribution} != {EXPECTED_DISTRIBUTION}"
    )
    assert unknown_truth_auto_accept == EXPECTED_UNKNOWN_TRUTH_AUTO_ACCEPT, (
        f"unknown-ground-truth auto-accepts drifted: {unknown_truth_auto_accept} "
        f"!= {EXPECTED_UNKNOWN_TRUTH_AUTO_ACCEPT}"
    )
    assert true_positive == EXPECTED_TRUE_POSITIVE, (
        f"true-positive auto-accepts changed to {true_positive} "
        f"(baseline {EXPECTED_TRUE_POSITIVE}) -- if this is a deliberate "
        f"improvement, re-measure and update the baseline consciously"
    )
    assert false_positive <= EXPECTED_FALSE_POSITIVE, (
        f"false-positive auto-accepts rose to {false_positive} "
        f"(baseline {EXPECTED_FALSE_POSITIVE}) -- Phase 3.3 requires the "
        f"false-positive rate to be non-increasing"
    )
