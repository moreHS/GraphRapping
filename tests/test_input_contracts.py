"""Input-connector contract tests (IC-1 / plan §2·§6).

Golden proof: the four checked-in source fixtures each satisfy their contract
(contract ↔ reality alignment). Rejection proof: per-field missing/type/
structure violations are reported. Plus the RS↔Relation mapping-table single
source of truth and the product joinability report (9-digit rep code = report
only, never a rejection).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest import input_contracts as ic

MOCK = Path("mockdata")


def _load(name: str):
    return json.loads((MOCK / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Golden: current fixtures satisfy their contract
# ---------------------------------------------------------------------------

def test_golden_rs_jsonl_passes() -> None:
    report = ic.validate_records(_load("review_rs_samples.json"), "rs_jsonl")
    assert report.total == 20
    assert report.violations == 0
    assert report.passed == 20


def test_golden_relation_landing_passes() -> None:
    report = ic.validate_records(_load("review_triples_raw.json"), "relation")
    assert report.total == 906
    assert report.violations == 0


def test_golden_product_catalog_passes_despite_bad_rep_codes() -> None:
    # codex #4: 6 non-9-digit REP_CODEs exist in the golden and must NOT fail.
    report = ic.validate_records(_load("product_catalog_es.json"), "product_catalog")
    assert report.total == 517
    assert report.violations == 0


def test_golden_user_profiles_pass() -> None:
    report = ic.validate_records(_load("user_profiles_normalized.json"), "user_profile")
    assert report.total == 50
    assert report.violations == 0


@pytest.mark.parametrize(
    "name,kind",
    [
        ("review_triples_raw.json", "relation"),
        ("product_catalog_es.json", "product_catalog"),
        ("user_profiles_normalized.json", "user_profile"),
    ],
)
def test_dense_golden_fixtures_pass(name: str, kind: str) -> None:
    report = ic.validate_records(_load(f"dense_golden/{name}"), kind)
    assert report.violations == 0


# ---------------------------------------------------------------------------
# Product joinability report (aggregate only)
# ---------------------------------------------------------------------------

def test_product_joinability_reports_six_nonconforming() -> None:
    report = ic.report_rep_code_joinability(_load("product_catalog_es.json"))
    assert report.total == 517
    assert report.nonconforming == 6
    assert report.joinable_9digit == 511
    assert report.missing == 1
    assert report.nonconforming_reason_counts == {
        "non_numeric": 4,
        "missing_or_empty": 1,
        "wrong_length": 1,
    }


# ---------------------------------------------------------------------------
# Rejection: per-record violations
# ---------------------------------------------------------------------------

def test_rs_jsonl_rejects_missing_and_typed_fields() -> None:
    reasons = ic.validate_rs_jsonl_record(
        {"text": "t", "date": "2026-01-01", "product_id": "", "channel": 31,
         "ner_spans": "nope", "prd_apal_scr": "x"}
    )
    joined = " | ".join(reasons)
    assert "missing required field: id" in joined
    assert "product_id must be a non-empty identifier" in joined
    assert "channel must be str" in joined
    assert "ner_spans must be a list" in joined
    assert "prd_apal_scr must be a number or null" in joined


def test_rs_jsonl_accepts_nullable_planned_fields() -> None:
    # brnd_nm/relation/prd_apal_scr absent or null; demographics optional.
    assert ic.validate_rs_jsonl_record(
        {"id": "R1", "text": "좋아요", "date": "2026-01-01",
         "product_id": "100", "channel": "031", "ner_spans": [], "bee_spans": []}
    ) == []


def test_relation_landing_rejects_missing_required_and_nonlist() -> None:
    reasons = ic.validate_relation_landing_record(
        {"drup_dt": "2026-01-01", "channel": "031", "text": "t",
         "source_product_id": "100", "ner": {}, "bee": [], "relation": []}
    )
    joined = " | ".join(reasons)
    assert "missing required field: source_review_key" in joined
    assert "field ner must be a list" in joined


def test_relation_landing_rejects_bad_nested_reviewer_profile() -> None:
    reasons = ic.validate_relation_landing_record(
        {"source_review_key": "K", "drup_dt": "2026-01-01", "channel": "031",
         "text": "t", "source_product_id": "100", "ner": [], "bee": [], "relation": [],
         "reviewer_profile": {"age_sctn_cd": 40}}
    )
    assert any("reviewer_profile.age_sctn_cd must be str" in r for r in reasons)


def test_product_catalog_rejects_missing_identity_keys() -> None:
    reasons = ic.validate_product_catalog_record(
        {"SOURCE_CHANNEL": "036", "SOURCE_PRODUCT_ID": "  ",
         "SOURCE_COMPAT_COLLAPSED": "yes"}
    )
    joined = " | ".join(reasons)
    assert "missing required field: SOURCE_KEY_TYPE" in joined
    assert "missing required field: ONLINE_PROD_SERIAL_NUMBER" in joined
    assert "SOURCE_PRODUCT_ID must be a non-empty identifier" in joined
    assert "SOURCE_COMPAT_COLLAPSED must be a boolean" in joined


def test_product_catalog_never_rejects_for_rep_code() -> None:
    # Z_Z / 5-digit / null rep codes all pass the record contract.
    for rep in ("Z_Z", "69797", None, ""):
        rec = {
            "SOURCE_CHANNEL": "031", "SOURCE_KEY_TYPE": "ecp_onln_prd_srno",
            "SOURCE_PRODUCT_ID": "1", "ONLINE_PROD_SERIAL_NUMBER": "1",
            "REPRESENTATIVE_PROD_CODE": rep,
        }
        assert ic.validate_product_catalog_record(rec) == []


def test_user_profile_rejects_raw_seven_column_and_missing_basic() -> None:
    raw = ic.validate_user_profile({"user_profile": {}, "skin_profile": {}})
    assert any("raw 7-column" in r for r in raw)
    assert ic.validate_user_profile({"purchase_analysis": {}}) == ["missing required key: basic"]


def test_user_profile_validates_optional_purchase_events() -> None:
    reasons = ic.validate_user_profile(
        {"basic": {}, "purchase_analysis": {}, "chat": None,
         "purchase_events": [{"product_id": ""}, {"product_id": "100", "quantity": 0}]}
    )
    joined = " | ".join(reasons)
    assert "purchase_events[0] missing product_id" in joined
    assert "purchase_events[1].quantity must be a positive int" in joined


def test_user_profile_golden_shape_with_events_passes() -> None:
    assert ic.validate_user_profile(
        {"basic": {"gender": "F"}, "purchase_analysis": {}, "chat": None,
         "purchase_events": [{"product_id": "100", "purchased_at": "2025-01-01"}]}
    ) == []


# ---------------------------------------------------------------------------
# RS↔Relation mapping table (single source of truth, matches the loader)
# ---------------------------------------------------------------------------

def test_mapping_table_matches_loader_intent() -> None:
    m = ic.RS_TO_RELATION_FIELD_MAP
    assert m["id"] == "source_review_key"
    assert m["date"] == "drup_dt"
    assert m["product_id"] == "source_product_id"
    assert m["prd_nm"] == "prod_nm"
    assert m["channel"] == "source_channel"
    assert m["ner_spans"] == "ner"
    assert m["bee_spans"] == "bee"
    assert m["prd_apal_scr"] == "source_rating"
    # own-source demographics collapse into the nested reviewer_profile.
    for f in ("age_sctn_cd", "sex_cd", "sktp_nm", "sktr_nm"):
        assert m[f] == f"reviewer_profile.{f}"


def test_mapping_targets_are_real_rawreviewrecord_paths() -> None:
    """Every non-nested mapping target must be a field the loaders actually set.

    Guards the map against drift from RawReviewRecord (id→source_review_key,
    channel→source_channel, etc.). Nested reviewer_profile.* targets are checked
    structurally (prefix) since they land on the relation JSON, not the record.
    """
    from dataclasses import fields as dc_fields

    from src.ingest.review_ingest import RawReviewRecord

    record_fields = {f.name for f in dc_fields(RawReviewRecord)}
    # created_at is the meeting point for date/drup_dt (loaders rename to it).
    allowed = record_fields | {"drup_dt", "prod_nm", "text"}
    for rs_field, target in ic.RS_TO_RELATION_FIELD_MAP.items():
        if target.startswith("reviewer_profile."):
            continue
        assert target in allowed, f"{rs_field}→{target} not a known landing field"


# ---------------------------------------------------------------------------
# validate_records aggregation: keys/reasons only, never payload
# ---------------------------------------------------------------------------

def test_validate_records_report_excludes_payload() -> None:
    secret = "SECRET_REVIEW_TEXT_should_not_appear"
    records = [
        {"id": "good", "text": secret, "date": "2026-01-01",
         "product_id": "1", "channel": "031"},
        {"text": secret, "date": "2026-01-01", "product_id": "1", "channel": "031"},  # missing id
    ]
    report = ic.validate_records(records, "rs_jsonl")
    assert report.total == 2
    assert report.violations == 1
    assert report.passed == 1
    assert report.violation_keys == ["#1"]  # index key, since 'id' is missing
    blob = json.dumps(report.to_dict(), ensure_ascii=False)
    assert secret not in blob
    manifest_blob = json.dumps(report.to_manifest_dict(), ensure_ascii=False)
    assert secret not in manifest_blob


def test_validate_records_user_mapping_key_is_user_id() -> None:
    profiles = {"user_ok": {"basic": {}}, "user_bad": {"purchase_analysis": {}}}
    report = ic.validate_records(profiles, "user_profile")
    assert report.violations == 1
    assert report.violation_keys == ["user_bad"]


def test_validate_records_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown contract kind"):
        ic.validate_records([], "nope")
