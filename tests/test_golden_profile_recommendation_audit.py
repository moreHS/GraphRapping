from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_recommendation_evidence import build_audit_report


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_recommendation_evidence.py"
GOLDEN_PROFILE_IDS = {
    "user_dry_30f",
    "user_brand_null_cat",
    "user_sensitive_40f",
    "user_scalp_care_50m",
    "user_fragrance_60f",
    "user_makeup_matte_50m",
}
EVIDENCE_FAMILIES = {
    "PRODUCT_MASTER_TRUTH",
    "REVIEW_GRAPH_RELATION",
    "REVIEW_GRAPH_WEAK_RELATION",
    "PURCHASE_BEHAVIOR",
}
SCORE_LAYERS = {
    "master_truth_score",
    "review_graph_score",
    "review_graph_weak_evidence_score",
    "product_activity_score",
    "profile_fit_score",
    "purchase_behavior_score",
    "source_trust_score",
}


@pytest.fixture(scope="module")
def dense_all_report() -> dict:
    return build_audit_report(
        fixture="dense_golden",
        kg_mode="on",
        top_k=5,
        category_group="all",
    )


def test_dense_golden_audit_supports_final_six_profiles(dense_all_report: dict) -> None:
    assert dense_all_report["fixture"] == "dense_golden"
    assert dense_all_report["kg_mode"] == "on"
    assert dense_all_report["review_count"] == 906
    assert 30 <= dense_all_report["product_count"] <= 45
    assert dense_all_report["user_count"] == 6
    assert set(dense_all_report["user_ids"]) == GOLDEN_PROFILE_IDS
    assert dense_all_report["scenario_count"] == 6


def test_dense_golden_audit_has_non_empty_scenarios_and_evidence_fields(
    dense_all_report: dict,
) -> None:
    scenarios = dense_all_report["scenarios"]
    assert scenarios
    assert {scenario["user_id"] for scenario in scenarios} == GOLDEN_PROFILE_IDS
    assert any(scenario["candidate_count"] > 0 for scenario in scenarios)

    for scenario in scenarios:
        assert scenario["category_group"] == "all"
        assert scenario["category_filtered_count"] == dense_all_report["serving_product_count"]
        assert scenario["coverage_status"] in {"ok", "no_candidates"}
        assert set(scenario["evidence_family_counts"]) == EVIDENCE_FAMILIES
        assert set(scenario["candidate_evidence_family_counts"]) == EVIDENCE_FAMILIES
        assert set(scenario["score_layer_totals"]) == SCORE_LAYERS
        assert isinstance(scenario["promoted_relation_hit_count"], int)
        assert isinstance(scenario["weak_relation_hit_count"], int)
        assert isinstance(scenario["source_stats_contribution_count"], int)
        assert isinstance(scenario["owned_family_suppression_count"], int)
        assert isinstance(scenario["purchase_path_count"], int)
        assert isinstance(scenario["purchase_score_nonzero_count"], int)

        if scenario["coverage_status"] == "ok":
            assert scenario["candidate_count"] > 0
            assert scenario["top_product_count"] > 0
        else:
            assert scenario["candidate_count"] == 0
            assert scenario["top_product_count"] == 0

        for product in scenario["top_products"]:
            assert product["evidence_families"]
            assert set(product["score_layers"]) == SCORE_LAYERS
            assert "SOURCE_REVIEW_STATS" not in product["evidence_families"]
            assert set(product["evidence_families"]) <= EVIDENCE_FAMILIES


def test_source_stats_are_trust_tiebreak_not_eligibility(dense_all_report: dict) -> None:
    source_contributing_rows = []
    for scenario in dense_all_report["scenarios"]:
        assert scenario["source_stats_only_eligibility_count"] == 0
        for product in scenario["top_products"]:
            if product["source_trust_score"] > 0:
                source_contributing_rows.append(product)
                assert product["evidence_families"]
                assert "SOURCE_REVIEW_STATS" not in product["eligibility"]["evidence_families"]
                assert "SOURCE_REVIEW_STATS" not in product["overlap_concepts"]

    assert source_contributing_rows


def test_dense_audit_exposes_review_graph_relation_when_present(
    dense_all_report: dict,
) -> None:
    promoted_hits = sum(
        scenario["promoted_relation_hit_count"]
        for scenario in dense_all_report["scenarios"]
    )
    candidate_review_graph_hits = sum(
        scenario["candidate_evidence_family_counts"]["REVIEW_GRAPH_RELATION"]
        for scenario in dense_all_report["scenarios"]
    )

    assert candidate_review_graph_hits > 0
    assert promoted_hits > 0


def test_cli_json_output_for_single_scenario_is_parseable() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--fixture",
            "dense_golden",
            "--kg-mode",
            "on",
            "--top-k",
            "3",
            "--user-id",
            "user_dry_30f",
            "--category-group",
            "skincare",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert report["fixture"] == "dense_golden"
    assert report["kg_mode"] == "on"
    assert report["user_ids"] == ["user_dry_30f"]
    assert report["scenario_count"] == 1
    scenario = report["scenarios"][0]
    assert scenario["category_group"] == "skincare"
    assert scenario["candidate_count"] > 0
    assert scenario["top_product_count"] <= 3
