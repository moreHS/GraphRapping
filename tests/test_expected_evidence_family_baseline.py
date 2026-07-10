"""Phase 0.1 — golden-profile expected evidence-family baseline.

This test freezes the *expected* evidence-family contract for the dense_golden
fixture. It reuses the existing audit primitive
(scripts.audit_recommendation_evidence.build_audit_report) — the same
prefilter -> candidate -> scorer -> reranker path the demo server uses — and
asserts each profile x category-tab combination against the spec file
tests/fixtures/golden_expected_evidence.yaml.

Two layers of assertions:

1. Per-combination contract (from the spec file):
   - allowed_no_candidate: whether a no-candidate result is permitted here.
   - required_families: evidence families that MUST appear in the candidate
     pool (top_k-stable regression anchor).
   - forbidden_families: families that must NOT appear (documented leakage
     contracts, e.g. makeup-scoped keywords must not leak into the skincare tab).

2. Global invariants (enforced on every combination regardless of spec):
   (a) any eligible result carries >= 1 evidence family;
   (b) no result is eligible via source_trust (source_review_*) alone;
   (c) SOURCE_REVIEW_STATS / active_category / review_summary are never an
       evidence family;
   (d) top-N families are a subset of the candidate-pool families;
   (e) no-candidate only occurs where allowed_no_candidate is true.

The spec must stay green at HEAD; it is a contract baseline, not an
aspirational target.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.audit_recommendation_evidence import build_audit_report


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "tests" / "fixtures" / "golden_expected_evidence.yaml"

GOLDEN_PROFILE_IDS = {
    "user_dry_30f",
    "user_brand_null_cat",
    "user_sensitive_40f",
    "user_scalp_care_50m",
    "user_fragrance_60f",
    "user_makeup_matte_50m",
}
CATEGORY_TABS = (
    "all",
    "skincare",
    "makeup",
    "bodycare",
    "haircare",
    "fragrance",
    "other",
)
KNOWN_FAMILIES = {
    "PRODUCT_MASTER_TRUTH",
    "REVIEW_GRAPH_RELATION",
    "REVIEW_GRAPH_WEAK_RELATION",
    "PURCHASE_BEHAVIOR",
}
# Overlap-concept prefixes that are, by contract, NOT eligibility evidence.
# They may show up as scoring/context/trust signals but must never be
# classified into an evidence family.
NON_EVIDENCE_PREFIXES = ("active_category:", "source_review", "review_summary")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    data = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "spec file must parse to a mapping"
    return data


@pytest.fixture(scope="module")
def scenarios_by_key() -> dict[tuple[str, str], dict[str, Any]]:
    """Run the audit once and index scenarios by (user_id, category_tab)."""
    report = build_audit_report(fixture="dense_golden", kg_mode="on", top_k=5)
    assert report["fixture"] == "dense_golden"
    assert report["kg_mode"] == "on"
    return {
        (str(s["user_id"]), str(s["category_group"])): s
        for s in report["scenarios"]
    }


def _observed_candidate_families(scenario: dict[str, Any]) -> set[str]:
    return {
        family
        for family, count in scenario["candidate_evidence_family_counts"].items()
        if count > 0
    }


def _observed_top_families(scenario: dict[str, Any]) -> set[str]:
    return {
        family
        for family, count in scenario["evidence_family_counts"].items()
        if count > 0
    }


# --------------------------------------------------------------------------
# Spec-file integrity — the contract file itself must be well-formed and
# cover exactly the golden profiles x tabs, so a silently-dropped entry can
# never make a combination go unchecked.
# --------------------------------------------------------------------------

def test_spec_covers_all_profile_tab_combinations(spec: dict[str, Any]) -> None:
    assert spec["fixture"] == "dense_golden"
    assert spec["kg_mode"] == "on"
    assert set(spec["known_families"]) == KNOWN_FAMILIES

    profiles = spec["profiles"]
    assert set(profiles) == GOLDEN_PROFILE_IDS, (
        "spec profiles must match the six golden profiles exactly"
    )
    for profile_id, tabs in profiles.items():
        assert set(tabs) == set(CATEGORY_TABS), (
            f"{profile_id}: spec must cover every category tab exactly once"
        )
        for tab, entry in tabs.items():
            assert set(entry) == {
                "allowed_no_candidate",
                "required_families",
                "forbidden_families",
            }, f"{profile_id}/{tab}: unexpected keys {sorted(entry)}"
            assert isinstance(entry["allowed_no_candidate"], bool)
            required = set(entry["required_families"])
            forbidden = set(entry["forbidden_families"])
            assert required <= KNOWN_FAMILIES, f"{profile_id}/{tab}: bad required family"
            assert forbidden <= KNOWN_FAMILIES, f"{profile_id}/{tab}: bad forbidden family"
            assert not (required & forbidden), (
                f"{profile_id}/{tab}: a family is both required and forbidden"
            )
            if entry["allowed_no_candidate"] is False:
                assert required, (
                    f"{profile_id}/{tab}: a tab that must produce candidates "
                    "needs at least one required family"
                )


def test_audit_produces_every_spec_combination(
    spec: dict[str, Any],
    scenarios_by_key: dict[tuple[str, str], dict[str, Any]],
) -> None:
    expected_keys = {
        (profile_id, tab)
        for profile_id, tabs in spec["profiles"].items()
        for tab in tabs
    }
    assert expected_keys <= set(scenarios_by_key), (
        "audit did not emit a scenario for every spec combination"
    )
    assert len(expected_keys) == len(GOLDEN_PROFILE_IDS) * len(CATEGORY_TABS)


# --------------------------------------------------------------------------
# Per-combination contract assertions.
# --------------------------------------------------------------------------

def _spec_combinations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (profile_id, tab, entry)
        for profile_id, tabs in spec["profiles"].items()
        for tab, entry in tabs.items()
    ]


def test_per_combination_evidence_contract(
    spec: dict[str, Any],
    scenarios_by_key: dict[tuple[str, str], dict[str, Any]],
) -> None:
    failures: list[str] = []
    for profile_id, tab, entry in _spec_combinations(spec):
        scenario = scenarios_by_key[(profile_id, tab)]
        label = f"{profile_id}/{tab}"
        coverage = scenario["coverage_status"]
        candidate_families = _observed_candidate_families(scenario)
        top_families = _observed_top_families(scenario)

        # (e) no-candidate only where allowed.
        if coverage == "no_candidates":
            if not entry["allowed_no_candidate"]:
                failures.append(
                    f"{label}: no candidates but allowed_no_candidate=false"
                )
            # A no-candidate scenario must be genuinely empty.
            if scenario["candidate_count"] != 0 or scenario["top_product_count"] != 0:
                failures.append(f"{label}: no_candidates status but non-zero counts")
            continue

        # coverage == "ok"
        if scenario["candidate_count"] <= 0:
            failures.append(f"{label}: coverage ok but candidate_count<=0")

        # required families must all be present in the candidate pool.
        missing = set(entry["required_families"]) - candidate_families
        if missing:
            failures.append(
                f"{label}: required families missing from candidate pool "
                f"{sorted(missing)} (observed {sorted(candidate_families)})"
            )

        # forbidden families must not appear anywhere (candidate pool or top-N).
        present_forbidden = set(entry["forbidden_families"]) & (
            candidate_families | top_families
        )
        if present_forbidden:
            failures.append(
                f"{label}: forbidden families present {sorted(present_forbidden)}"
            )

        # (d) top-N families are always a subset of the candidate-pool families.
        top_not_in_pool = top_families - candidate_families
        if top_not_in_pool:
            failures.append(
                f"{label}: top-N families not in candidate pool {sorted(top_not_in_pool)}"
            )

    assert not failures, "evidence-family contract violations:\n" + "\n".join(failures)


# --------------------------------------------------------------------------
# Global invariants — enforced on every emitted scenario, independent of the
# spec, so a broken eligibility rule fails even for combinations the spec is
# lenient about.
# --------------------------------------------------------------------------

def test_global_invariants_hold_for_all_scenarios(
    scenarios_by_key: dict[tuple[str, str], dict[str, Any]],
) -> None:
    failures: list[str] = []
    saw_eligible_product = False

    for (profile_id, tab), scenario in scenarios_by_key.items():
        label = f"{profile_id}/{tab}"

        # source-stats-only eligibility must never happen (audit's own counter).
        if scenario["source_stats_only_eligibility_count"] != 0:
            failures.append(
                f"{label}: source_stats_only_eligibility_count="
                f"{scenario['source_stats_only_eligibility_count']}"
            )

        for product in scenario["top_products"]:
            families = product["evidence_families"]
            eligibility = product["eligibility"]

            # (a) eligible => at least one evidence family.
            if eligibility["eligible"]:
                saw_eligible_product = True
                if not families:
                    failures.append(f"{label}: eligible product with no family")

            # (b) source_trust alone must not qualify.
            if product["source_trust_score"] > 0 and not families:
                failures.append(
                    f"{label}: source_trust_score>0 with no evidence family"
                )

            # (c) families must be a subset of the known families.
            unknown = set(families) - KNOWN_FAMILIES
            if unknown:
                failures.append(f"{label}: unknown evidence families {sorted(unknown)}")

            # (c) non-evidence signals must never be classified into a family.
            classified_paths = (
                eligibility["master_truth_paths"]
                + eligibility["review_graph_paths"]
                + eligibility["weak_review_graph_paths"]
                + eligibility["purchase_paths"]
            )
            leaked = [
                path
                for path in classified_paths
                if path.startswith(NON_EVIDENCE_PREFIXES)
            ]
            if leaked:
                failures.append(f"{label}: non-evidence signal classified as evidence {leaked}")

            # SOURCE_REVIEW_STATS must not appear as a family or overlap concept.
            if "SOURCE_REVIEW_STATS" in families:
                failures.append(f"{label}: SOURCE_REVIEW_STATS treated as evidence family")
            if "SOURCE_REVIEW_STATS" in product["overlap_concepts"]:
                failures.append(f"{label}: SOURCE_REVIEW_STATS in overlap concepts")

    assert saw_eligible_product, "expected at least one eligible product across scenarios"
    assert not failures, "global invariant violations:\n" + "\n".join(failures)


def test_no_candidate_combinations_match_spec_count(
    spec: dict[str, Any],
    scenarios_by_key: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Sanity: the number of no-candidate scenarios equals the number of spec
    combinations that permit it. Guards against the audit silently gaining or
    losing coverage without the spec being updated in step."""
    observed_no_candidate = {
        key
        for key, scenario in scenarios_by_key.items()
        if scenario["coverage_status"] == "no_candidates"
    }
    allowed_no_candidate = {
        (profile_id, tab)
        for profile_id, tabs in spec["profiles"].items()
        for tab, entry in tabs.items()
        if entry["allowed_no_candidate"]
    }
    # Every observed no-candidate must be permitted (also checked per-combination),
    # and the counts must line up so a newly-empty combination cannot pass silently.
    assert observed_no_candidate <= allowed_no_candidate
    assert observed_no_candidate == allowed_no_candidate, (
        "spec permits no-candidate for combinations that currently DO produce "
        "candidates; tighten the spec:\n"
        f"  only-in-spec (permitted but has candidates): "
        f"{sorted(allowed_no_candidate - observed_no_candidate)}"
    )
