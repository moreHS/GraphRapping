"""
Final 906-review baseline regression for corpus promotion outcomes.

Pins down the **observable** result of the full mock dataset under each
`kg_mode`. If anyone later tunes promotion thresholds (window, confidence,
synthetic ratio), changes mock data, or shifts gate ordering, these tests
fail loudly with the new vs expected baseline — preventing silent drift.

Baselines (final 906-review source-grounded fixture):
  906 reviews / 517 distinct products / 50 users
- kg_mode=off : signal_count = 3,273, quarantine_count = 8,798
                top_bee_attr_ids on 90 products, top_keyword_ids on 30
- kg_mode=on  : signal_count = 3,248, quarantine_count = 5,626
                top_bee_attr_ids on 90 products, top_keyword_ids on 30

The active baseline is now the final 906-review source-grounded fixture; see
`DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`.

Phase 7 B1 (2026-07-13) intentionally shifted these baselines: the KG
mention_extractor candidate queue now consults the keyword dictionary before
quarantining, korean_morph folds inflected surfaces, and missing skincare
attribute stems were registered in keyword_surface_map.yaml. Net effect:
unknown_keyword quarantine down (kg_on 2,784→2,088; kg_off 2,477→2,020),
keyword signals up (top_keyword_ids 5→7). See
`DECISIONS/2026-07-13_phase7_b1_keyword_path_unification.md`.

Phase 7 B2 (2026-07-13) further shifted signal_count: the keyword canonical
alias layer folds the moisture cluster (kw_moist + MoistLike → kw_moisturizing)
at resolution time, so one surface (e.g. "촉촉한") no longer double-emits
sibling keyword ids. signal_count dropped by the deduped double-counts
(kg_on 3,340→3,248; kg_off 3,365→3,273); quarantine and top_* counts are
unchanged (the fold is id-level, not membership-level). See
`DECISIONS/2026-07-13_phase7_b2_keyword_alias.md`.

If a baseline shifts, document the cause in DECISIONS/ before changing
these numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.jobs.run_full_load import FullLoadConfig, run_full_load


_MOCK = Path(__file__).parent.parent / "mockdata"


@pytest.fixture(scope="module")
def _load_inputs() -> tuple[list[dict], dict]:
    products = json.loads((_MOCK / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = json.loads((_MOCK / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    return products, users


def _run(products: list[dict], users: dict, kg_mode: str) -> object:
    return run_full_load(FullLoadConfig(
        review_json_path=str(_MOCK / "review_triples_raw.json"),
        product_es_records=products,
        user_profiles=users,
        kg_mode=kg_mode,
    ))


_DRIFT_HINT = "Investigate before adjusting — document cause in DECISIONS/."


def test_kg_off_signal_baseline(_load_inputs: tuple[list[dict], dict]) -> None:
    products, users = _load_inputs
    r = _run(products, users, "off")
    assert r.signal_count == 3273, (
        f"kg_mode=off signal_count baseline drift: got {r.signal_count} (expected 3273). "
        f"{_DRIFT_HINT}"
    )


def test_kg_off_quarantine_baseline(_load_inputs: tuple[list[dict], dict]) -> None:
    products, users = _load_inputs
    r = _run(products, users, "off")
    assert r.quarantine_count == 8798, (
        f"kg_mode=off quarantine_count baseline drift: got {r.quarantine_count} (expected 8798). "
        f"{_DRIFT_HINT}"
    )


def test_kg_on_signal_baseline(_load_inputs: tuple[list[dict], dict]) -> None:
    products, users = _load_inputs
    r = _run(products, users, "on")
    assert r.signal_count == 3248, (
        f"kg_mode=on signal_count baseline drift: got {r.signal_count} (expected 3248). "
        f"{_DRIFT_HINT}"
    )


def test_kg_on_quarantine_baseline(_load_inputs: tuple[list[dict], dict]) -> None:
    products, users = _load_inputs
    r = _run(products, users, "on")
    assert r.quarantine_count == 5626, (
        f"kg_mode=on quarantine_count baseline drift: got {r.quarantine_count} (expected 5626). "
        f"{_DRIFT_HINT}"
    )


_TOP_SIGNAL_FIELDS = (
    "top_bee_attr_ids",
    "top_keyword_ids",
    "top_context_ids",
    "top_concern_pos_ids",
    "top_concern_neg_ids",
    "top_tool_ids",
    "top_comparison_product_ids",
    "top_coused_product_ids",
)


# The final 906 reviews are distributed over 517 source-grounded products.
# Phase 7 C2 (2026-07-13) lowered the promotion gate distinct_review_count
# threshold from >=3 to >=2 for the all/90d windows. This raised wide serving
# reach from 26 products (5.0%) to 90 (17.4%) — exactly the structural-ceiling
# projection A4 measured (every product has >=1 review). top_keyword_ids rose
# 7 -> 30 by the same promotion expansion. top_concern_* stay 0: every CONCERN
# agg row still has distinct_review_count=1, so the >=2 cross-review check
# correctly keeps single-review concern signals out of serving (evidence-first;
# surfacing them would require >=2 distinct reviews per product-concern, not a
# further gate change). signal_count / quarantine are unchanged — they are
# computed pre-aggregation and so are promotion-gate-independent.
# See DECISIONS/2026-07-13_phase7_c2_promotion_gate.md and
# DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md.
#
# kg_on also restores source-backed BEE dictionary keyword projection:
# BEEAttr -> HAS_KEYWORD -> Keyword helper facts are emitted only in kg_on.
# See DECISIONS/2026-06-22_kg_on_source_backed_keyword_repair.md.
_EXPECTED_TOP_FIELD_COUNTS = {
    "off": {
        "top_bee_attr_ids": 90,
        "top_keyword_ids": 30,
        "top_context_ids": 0,
        "top_concern_pos_ids": 0,
        "top_concern_neg_ids": 0,
        "top_tool_ids": 0,
        "top_comparison_product_ids": 0,
        "top_coused_product_ids": 0,
    },
    "on": {
        "top_bee_attr_ids": 90,
        "top_keyword_ids": 30,
        "top_context_ids": 0,
        "top_concern_pos_ids": 0,
        "top_concern_neg_ids": 0,
        "top_tool_ids": 0,
        "top_comparison_product_ids": 0,
        "top_coused_product_ids": 0,
    },
}


@pytest.mark.parametrize("kg_mode", ["off", "on"])
def test_promoted_top_field_counts_match_baseline(
    _load_inputs: tuple[list[dict], dict],
    kg_mode: str,
) -> None:
    """Measurement-driven baseline: per top_* field, exact product count.

    v260605 refresh activated promotion gates (Wave 2.8/2.9). Expected
    (after 2026-06-10 catalog 교체 → 517 distinct products; Phase 7 C2 gate
    relaxation 3→2 for all/90d):
    - top_bee_attr_ids on 90 products (both kg_modes; C2 raised this from 26)
    - top_keyword_ids on 30 products (both modes; C2 raised this from 7)
    - Other top_* fields stay at 0 (mock data has limited context/concern/tool/
      co-use signals, and every concern signal is single-review so the >=2 gate
      keeps them out)

    Any drift is a stop condition.
    """
    products, users = _load_inputs
    r = _run(products, users, kg_mode)

    actual: dict[str, int] = {}
    for field in _TOP_SIGNAL_FIELDS:
        actual[field] = sum(1 for p in r.serving_products if p.get(field))

    expected = _EXPECTED_TOP_FIELD_COUNTS[kg_mode]
    mismatches = {f: (actual[f], expected[f]) for f in expected if actual[f] != expected[f]}
    assert not mismatches, (
        f"kg_mode={kg_mode}: top_* field count drift {mismatches}. "
        f"{_DRIFT_HINT}"
    )


def test_stop_pivot_thresholds_not_breached(
    _load_inputs: tuple[list[dict], dict],
) -> None:
    """v260605 refresh floor: signals(kg_off) >= 2520 (measured 2801 × 0.9).

    quarantine ceiling removed — quarantine is entry-count (not review-count)
    based, so absolute thresholds are misleading. Quarantine_by_table
    composition is tracked via test_quarantine_composition_baseline below.
    """
    products, users = _load_inputs
    r = _run(products, users, "off")
    assert r.signal_count >= 2520, (
        f"STOP TRIPPED: kg_off signals={r.signal_count} < 2520 floor."
    )


def test_quarantine_composition_baseline(
    _load_inputs: tuple[list[dict], dict],
) -> None:
    """v260605 refresh: pin quarantine_by_table composition so any shift in
    canonicalizer / projection_registry / placeholder resolver behavior surfaces.
    """
    products, users = _load_inputs
    r = _run(products, users, "off")
    qbt = r.batch_result.get("quarantine_by_table", {})
    assert qbt.get("quarantine_placeholder") == 2303, (
        f"quarantine_placeholder drift: got {qbt.get('quarantine_placeholder')} (expected 2303). {_DRIFT_HINT}"
    )
    assert qbt.get("quarantine_projection_miss") == 4475, (
        f"quarantine_projection_miss drift: got {qbt.get('quarantine_projection_miss')} (expected 4475). {_DRIFT_HINT}"
    )
    assert qbt.get("quarantine_unknown_keyword") == 2020, (
        f"quarantine_unknown_keyword drift: got {qbt.get('quarantine_unknown_keyword')} (expected 2020). {_DRIFT_HINT}"
    )
