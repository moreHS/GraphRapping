"""Purchase-history backfill (fable_doc §C1) — mock-data unit tests.

No live DB / no personalization import: exercises the pure resolution helpers
in scripts/fetch_user_profiles_pg.py, the loader extraction helper + fallback,
and the server opt-in override. The default demo path staying byte-identical is
proved by (a) the untouched fixture yielding no embedded events and (b) the rest
of the suite passing unmodified.

Cross-review round (2026-07-18): occurrence-based events (no member-SKU
expansion — repurchase-contamination tests), 9-digit code rule, pseudonym
validation/collision abort, output-path confinement, query source coverage,
boundary hardening, and a run_full_load regression.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.fetch_user_profiles_pg import (
    BACKFILL_QUERY,
    MAX_LIMIT,
    _limit_type,
    build_profile_record,
    build_rep_code_index,
    extract_purchase_codes,
    pseudonymize_incs_no,
    register_pseudonym,
    resolve_purchase_events,
    validate_output_path,
    write_output_atomic,
)
from src.loaders.user_loader import (
    extract_purchase_events_from_profiles,
    load_users_from_profiles,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 9-digit codes for fixtures (the rep-code rule rejects short synthetic ids).
_F1 = "111111111"
_F2 = "222222222"
_F3 = "333333333"


# ── build_rep_code_index ────────────────────────────────────────────────────

def test_build_rep_code_index_groups_dedups_and_enforces_9_digits():
    catalog = [
        {"REPRESENTATIVE_PROD_CODE": _F1, "ONLINE_PROD_SERIAL_NUMBER": "102"},
        {"REPRESENTATIVE_PROD_CODE": _F1, "ONLINE_PROD_SERIAL_NUMBER": "101"},
        {"REPRESENTATIVE_PROD_CODE": _F1, "ONLINE_PROD_SERIAL_NUMBER": "101"},  # dup
        {"REPRESENTATIVE_PROD_CODE": _F2, "ONLINE_PROD_SERIAL_NUMBER": "200"},
        {"REPRESENTATIVE_PROD_CODE": None, "ONLINE_PROD_SERIAL_NUMBER": "999"},  # missing
        {"REPRESENTATIVE_PROD_CODE": "Z_Z", "ONLINE_PROD_SERIAL_NUMBER": "300"},  # non-numeric
        {"REPRESENTATIVE_PROD_CODE": "69797", "ONLINE_PROD_SERIAL_NUMBER": "301"},  # 5 digits
        {"REPRESENTATIVE_PROD_CODE": _F3, "ONLINE_PROD_SERIAL_NUMBER": None},  # missing SKU
    ]
    index, skipped = build_rep_code_index(catalog)
    assert index == {_F1: ["101", "102"], _F2: ["200"]}
    assert skipped == 4


def test_build_rep_code_index_matches_real_catalog_shape():
    catalog = json.loads(
        (_PROJECT_ROOT / "mockdata" / "product_catalog_es.json").read_text(encoding="utf-8")
    )
    index, skipped = build_rep_code_index(catalog)
    # Session-measured baseline: 517 records → 381 valid 9-digit rep codes /
    # 511 member SKUs; 6 records skipped (4x 'Z_Z', 1x 5-digit, 1x null).
    assert len(index) == 381
    assert sum(len(v) for v in index.values()) == 511
    assert skipped == 6


# ── extract_purchase_codes ──────────────────────────────────────────────────

def test_extract_purchase_codes_across_all_three_sources():
    raw = {
        "purchase_profile": {
            "use_expected_product_summary": {
                "스킨케어": {"토너": [{"rprs_prd_cd": _F1, "purchase_date": "2025-01-01"}]}
            }
        },
        "repurchase_category_affinity": {
            "preferred_repurchase_product_summary": {
                "메이크업": {"립": [{"rprs_prd_cd": _F2, "recent_purchase_date": "2025-02-02"}]}
            }
        },
        "seasonal_affinity": {
            "seasonal_product_summary": {
                "여름": {"선케어": [{"rprs_prd_cd": _F1, "purchase_date": "2025-06-01"}]}
            }
        },
    }
    codes, invalid = extract_purchase_codes(raw)
    assert set(codes) == {_F1, _F2}
    assert invalid == set()
    assert codes[_F1]["dates"] == {"2025-01-01", "2025-06-01"}
    assert codes[_F1]["kinds"] == {
        "use_expected_product_summary",
        "seasonal_product_summary",
    }
    assert codes[_F2]["dates"] == {"2025-02-02"}


def test_extract_purchase_codes_flags_non_9_digit_codes_as_invalid():
    raw = {
        "purchase_profile": {
            "use_expected_product_summary": {
                "c": {"s": [
                    {"rprs_prd_cd": "A1"},           # non-numeric
                    {"rprs_prd_cd": "12345"},        # 5 digits
                    {"rprs_prd_cd": "1234567890"},   # 10 digits
                    {"rprs_prd_cd": _F1, "purchase_date": "2025-01-01"},
                ]}
            }
        }
    }
    codes, invalid = extract_purchase_codes(raw)
    assert set(codes) == {_F1}
    assert invalid == {"A1", "12345", "1234567890"}


def test_extract_purchase_codes_ignores_entries_without_code():
    raw = {
        "purchase_profile": {
            "use_expected_product_summary": {
                "c": {"s": [{"rprs_prd_nm": "no-code-here"}, {"rprs_prd_cd": ""}]}
            }
        }
    }
    codes, invalid = extract_purchase_codes(raw)
    assert codes == {} and invalid == set()


def test_extract_purchase_codes_empty_when_no_summaries():
    codes, invalid = extract_purchase_codes({"purchase_profile": {}, "seasonal_affinity": {}})
    assert codes == {} and invalid == set()


# ── resolve_purchase_events (occurrence semantics, cross-review P0-3) ────────

def test_resolve_one_occurrence_yields_one_event_on_representative_member():
    """Multi-member family, single occurrence → exactly ONE event (sorted-first SKU)."""
    rep_index = {_F1: ["100", "101", "102"]}
    codes = {_F1: {"dates": {"2025-01-01"}, "kinds": set()}}
    events, stats = resolve_purchase_events("real_u", codes, rep_index)

    assert len(events) == 1
    assert events[0]["product_id"] == "100"  # deterministic representative
    assert events[0]["purchased_at"] == "2025-01-01"
    assert events[0]["quantity"] == 1
    assert stats["matched_families"] == [_F1]
    assert stats["anchor_skus"] == ["100"]


def test_resolve_two_distinct_dates_yield_two_events_same_family():
    rep_index = {_F1: ["100", "101"]}
    codes = {_F1: {"dates": {"2025-01-01", "2025-06-01"}, "kinds": set()}}
    events, _ = resolve_purchase_events("real_u", codes, rep_index)
    assert [(e["product_id"], e["purchased_at"]) for e in events] == [
        ("100", "2025-01-01"),
        ("100", "2025-06-01"),
    ]


def test_resolve_dateless_code_is_single_occurrence_and_unmatched_dropped():
    rep_index = {_F2: ["200"]}
    codes = {
        _F2: {"dates": set(), "kinds": set()},
        _F3: {"dates": {"2025-09-09"}, "kinds": set()},  # not in catalog → dropped
    }
    events, stats = resolve_purchase_events("real_u", codes, rep_index)
    assert len(events) == 1
    assert events[0]["product_id"] == "200"
    assert events[0]["purchased_at"] is None
    assert events[0]["purchase_event_id"] == "real_u::200::na"
    assert stats["dropped_codes"] == [_F3]


# ── repurchase-contamination guarantees (mandated tests) ─────────────────────

def _facts_for(profiles, family_lookup, brand_lookup):
    events_by_user = extract_purchase_events_from_profiles(profiles)
    result = load_users_from_profiles(
        profiles,
        purchase_events_by_user=events_by_user,
        family_lookup=family_lookup,
        brand_lookup=brand_lookup,
    )
    (facts,) = result.user_adapted_facts.values()
    return {(f["predicate"], f["concept_value"]) for f in facts}


def test_single_occurrence_multi_member_family_creates_no_repurchase_facts():
    """One purchase of a 2-member family must NOT fabricate REPURCHASES_*."""
    rep_index = {_F1: ["100", "101"]}
    codes = {_F1: {"dates": {"2025-01-01"}, "kinds": set()}}
    events, _ = resolve_purchase_events("u", codes, rep_index)
    profiles = {"u": {"basic": {}, "purchase_analysis": {}, "chat": None,
                      "purchase_events": events}}
    pairs = _facts_for(profiles, {"100": _F1, "101": _F1}, {"100": "b1", "101": "b1"})

    assert ("OWNS_PRODUCT", "100") in pairs
    assert ("OWNS_FAMILY", _F1) in pairs
    assert not any(pred == "REPURCHASES_FAMILY" for pred, _ in pairs)
    assert not any(pred == "REPURCHASES_BRAND" for pred, _ in pairs)


def test_two_occurrences_same_family_create_repurchase_facts():
    """Two distinct purchase dates of the same family → repurchase facts fire."""
    rep_index = {_F1: ["100", "101"]}
    codes = {_F1: {"dates": {"2025-01-01", "2025-06-01"}, "kinds": set()}}
    events, _ = resolve_purchase_events("u", codes, rep_index)
    profiles = {"u": {"basic": {}, "purchase_analysis": {}, "chat": None,
                      "purchase_events": events}}
    pairs = _facts_for(profiles, {"100": _F1, "101": _F1}, {"100": "b1", "101": "b1"})

    assert ("REPURCHASES_FAMILY", _F1) in pairs
    assert ("REPURCHASES_BRAND", "b1") in pairs


# ── pseudonymization ────────────────────────────────────────────────────────

def test_pseudonymize_incs_no_prefix_and_truncation():
    assert pseudonymize_incs_no("HASHVALUE1234567890") == "real_HASHVALUE123"  # 12-char prefix
    assert pseudonymize_incs_no("short") == "real_short"


def test_register_pseudonym_valid_and_skip_rules():
    prefix_map: dict[str, str] = {}
    assert register_pseudonym("HASHVALUE1234567890", prefix_map) == "real_HASHVALUE123"
    # Same incs_no again → same pseudonym, no collision.
    assert register_pseudonym("HASHVALUE1234567890", prefix_map) == "real_HASHVALUE123"
    # Missing / blank / shorter than 12 chars → None (row skipped + counted).
    assert register_pseudonym(None, prefix_map) is None
    assert register_pseudonym("   ", prefix_map) is None
    assert register_pseudonym("ABCDEF", prefix_map) is None


def test_register_pseudonym_collision_aborts():
    prefix_map: dict[str, str] = {}
    register_pseudonym("HASHVALUE123AAA", prefix_map)
    with pytest.raises(RuntimeError, match="prefix collision"):
        register_pseudonym("HASHVALUE123BBB", prefix_map)  # same 12-char prefix


# ── query source coverage (cross-review P1-7) ────────────────────────────────

def test_backfill_query_predicates_cover_all_three_summary_sources():
    for column in ("purchase_profile", "repurchase_category_affinity", "seasonal_affinity"):
        assert f"{column}::text LIKE '%rprs_prd_cd%'" in BACKFILL_QUERY
    assert "ORDER BY incs_no" in BACKFILL_QUERY
    assert "LIMIT $1" in BACKFILL_QUERY


# ── output-path confinement + atomic write (cross-review P0-2) ───────────────

def test_validate_output_path_accepts_inside_and_rejects_outside(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    ok = validate_output_path(real_dir / "profiles.json", real_dir=real_dir)
    assert ok.parent == real_dir.resolve()

    with pytest.raises(ValueError, match="inside"):
        validate_output_path(tmp_path / "elsewhere.json", real_dir=real_dir)
    with pytest.raises(ValueError, match="inside"):  # nested subdir also rejected
        validate_output_path(real_dir / "sub" / "profiles.json", real_dir=real_dir)
    with pytest.raises(ValueError, match="inside"):  # .. traversal escapes
        validate_output_path(real_dir / ".." / "escape.json", real_dir=real_dir)


def test_validate_output_path_rejects_symlinks(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = real_dir / "link.json"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        validate_output_path(link, real_dir=real_dir)

    linked_dir = tmp_path / "linked_real"
    linked_dir.symlink_to(real_dir)
    with pytest.raises(ValueError, match="symlink"):
        validate_output_path(linked_dir / "profiles.json", real_dir=linked_dir)


def test_write_output_atomic_sets_0600_and_writes_content(tmp_path):
    target = tmp_path / "real" / "profiles.json"
    write_output_atomic(target, '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    # No leftover temp files.
    assert [p.name for p in target.parent.iterdir()] == ["profiles.json"]


def test_limit_type_enforces_bounds():
    assert _limit_type("50") == 50
    assert _limit_type(str(MAX_LIMIT)) == MAX_LIMIT
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        _limit_type("0")
    with pytest.raises(argparse.ArgumentTypeError):
        _limit_type(str(MAX_LIMIT + 1))


# ── build_profile_record ────────────────────────────────────────────────────

def _fake_normalize(raw):
    return {"basic": {"gender": "F"}, "purchase_analysis": {}, "chat": None}


def test_build_profile_record_embeds_events_when_matched():
    raw = {
        "purchase_profile": {
            "use_expected_product_summary": {
                "c": {"s": [{"rprs_prd_cd": _F1, "purchase_date": "2025-01-01"}]}
            }
        }
    }
    profile, stats = build_profile_record(
        "real_HASHVALUE123", raw, {_F1: ["100", "101"]}, _fake_normalize
    )
    assert profile["basic"] == {"gender": "F"}  # normalizer output preserved
    assert profile["purchase_events"] == [
        {
            "purchase_event_id": "real_HASHVALUE123::100::2025-01-01",
            "product_id": "100",
            "purchased_at": "2025-01-01",
            "quantity": 1,
        }
    ]
    assert stats["matched_families"] == [_F1]
    assert stats["invalid_codes"] == []


def test_build_profile_record_omits_events_when_unmatched_or_invalid():
    raw = {
        "purchase_profile": {
            "use_expected_product_summary": {
                "c": {"s": [{"rprs_prd_cd": _F3}, {"rprs_prd_cd": "NOPE"}]}
            }
        }
    }
    profile, stats = build_profile_record("X", raw, {_F1: ["100"]}, _fake_normalize)
    assert "purchase_events" not in profile
    assert stats["dropped_codes"] == [_F3]      # valid format, no catalog match
    assert stats["invalid_codes"] == ["NOPE"]   # fails 9-digit rule


# ── extract_purchase_events_from_profiles (loader helper) ────────────────────

def test_extract_events_injects_user_id_and_default_shape():
    profiles = {
        "u1": {"basic": {}, "purchase_events": [{"product_id": "100", "purchased_at": "2025-01-01"}]}
    }
    events = extract_purchase_events_from_profiles(profiles)
    assert events is not None and set(events) == {"u1"}
    ev = events["u1"][0]
    assert ev.user_id == "u1"
    assert ev.product_id == "100"
    assert ev.purchased_at == "2025-01-01"
    assert ev.quantity == 1
    assert ev.purchase_event_id == "u1::100::0"  # synthesized from key+pid+idx


def test_extract_events_returns_none_when_no_profile_has_events():
    assert extract_purchase_events_from_profiles({"u1": {"basic": {}}, "u2": {"chat": None}}) is None


def test_extract_events_quantity_boundary_rules():
    """P1-10: quantity must be a positive int; violations skip the EVENT."""
    profiles = {
        "u1": {
            "purchase_events": [
                {"product_id": "A"},                    # absent → default 1
                {"product_id": "B", "quantity": 3},     # valid
                {"product_id": "C", "quantity": 0},     # zero → skip
                {"product_id": "D", "quantity": -2},    # negative → skip
                {"product_id": "E", "quantity": "2"},   # non-int → skip
                {"product_id": "F", "quantity": 2.0},   # float → skip
                {"product_id": "G", "quantity": True},  # bool → skip
            ]
        }
    }
    events = extract_purchase_events_from_profiles(profiles)
    assert [(e.product_id, e.quantity) for e in events["u1"]] == [("A", 1), ("B", 3)]


def test_extract_events_skips_malformed_and_nullifies_ill_typed_fields():
    profiles = {
        "u1": {
            "purchase_events": [
                {"quantity": 2},          # no product_id → skip
                "junk",                   # not a mapping → skip
                {"product_id": "  "},     # blank product_id → skip
                {                          # ill-typed aux fields → nulled, event kept
                    "product_id": "X",
                    "purchased_at": 20250101,
                    "price": "9000원",
                    "channel": ["app"],
                },
            ]
        }
    }
    events = extract_purchase_events_from_profiles(profiles)
    assert len(events["u1"]) == 1
    ev = events["u1"][0]
    assert ev.product_id == "X"
    assert ev.purchased_at is None
    assert ev.price is None
    assert ev.channel is None


def test_extract_events_honors_explicit_event_id():
    profiles = {"u1": {"purchase_events": [{"product_id": "100", "purchase_event_id": "custom::1"}]}}
    events = extract_purchase_events_from_profiles(profiles)
    assert events["u1"][0].purchase_event_id == "custom::1"


def test_default_fixture_carries_no_embedded_events():
    """Byte-identical guarantee: the standard demo fixture has no purchase_events."""
    fixture = _PROJECT_ROOT / "mockdata" / "user_profiles_normalized.json"
    profiles = json.loads(fixture.read_text(encoding="utf-8"))
    assert extract_purchase_events_from_profiles(profiles) is None


# ── loader fallback (cross-review P1-9) ──────────────────────────────────────

def test_loader_fallback_auto_extracts_embedded_events():
    """Caller passes NO purchase_events_by_user → embedded events still build OWNS facts."""
    profiles = {
        "real_abc": {
            "basic": {"gender": "F"},
            "purchase_analysis": {},
            "chat": None,
            "purchase_events": [{"product_id": "100317", "purchased_at": "2025-03-01"}],
        }
    }
    result = load_users_from_profiles(
        profiles,
        family_lookup={"100317": "131172879"},
    )
    facts = result.user_adapted_facts["real_abc"]
    pairs = {(f["predicate"], f["concept_value"]) for f in facts}
    assert ("OWNS_PRODUCT", "100317") in pairs
    assert ("OWNS_FAMILY", "131172879") in pairs


def test_loader_explicit_events_still_win_over_fallback():
    """Explicit purchase_events_by_user suppresses the embedded fallback."""
    from src.ingest.purchase_ingest import PurchaseEvent

    profiles = {
        "u": {
            "basic": {},
            "purchase_analysis": {},
            "chat": None,
            "purchase_events": [{"product_id": "999"}],  # would yield OWNS_PRODUCT 999
        }
    }
    explicit = {"u": [PurchaseEvent("e1", "u", "111")]}
    result = load_users_from_profiles(profiles, purchase_events_by_user=explicit)
    pairs = {(f["predicate"], f["concept_value"]) for f in result.user_adapted_facts["u"]}
    assert ("OWNS_PRODUCT", "111") in pairs
    assert ("OWNS_PRODUCT", "999") not in pairs


def test_run_full_load_regression_embedded_events_reach_serving(tmp_path):
    """Full-load regression: embedded purchase_events flow through run_full_load
    (no config.purchase_events_by_user) into serving_users owned fields, and a
    standard profile without the key yields no owned fields."""
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    review_path = tmp_path / "reviews.json"
    review_path.write_text("[]", encoding="utf-8")
    products = [
        {
            "ONLINE_PROD_SERIAL_NUMBER": "100",
            "prd_nm": "테스트 토너",
            "BRAND_NAME": "테스트브랜드",
            "REPRESENTATIVE_PROD_CODE": _F1,
        },
        {
            "ONLINE_PROD_SERIAL_NUMBER": "101",
            "prd_nm": "테스트 토너 리필",
            "BRAND_NAME": "테스트브랜드",
            "REPRESENTATIVE_PROD_CODE": _F1,
        },
    ]
    users = {
        "real_with_events": {
            "basic": {"gender": "F"},
            "purchase_analysis": {},
            "chat": None,
            "purchase_events": [{"product_id": "100", "purchased_at": "2025-03-01"}],
        },
        "plain_user": {"basic": {"gender": "M"}, "purchase_analysis": {}, "chat": None},
    }
    result = run_full_load(FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=products,
        user_profiles=users,
        kg_mode="off",
    ))
    by_id = {u["user_id"]: u for u in result.serving_users}
    owned_products = [e["id"] for e in by_id["real_with_events"]["owned_product_ids"]]
    owned_families = [e["id"] for e in by_id["real_with_events"]["owned_family_ids"]]
    assert any(pid.endswith("100") for pid in owned_products)
    assert any(fid.endswith(_F1) for fid in owned_families)
    assert by_id["plain_user"]["owned_product_ids"] == []
    assert by_id["plain_user"]["owned_family_ids"] == []


# ── server opt-in override ──────────────────────────────────────────────────

def test_resolve_user_default_path_env_override(monkeypatch, tmp_path):
    from src.web.server import _resolve_user_default_path

    fixture_dir = tmp_path / "fx"

    monkeypatch.delenv("GRAPHRAPPING_USER_PROFILES_JSON", raising=False)
    assert _resolve_user_default_path(fixture_dir) == fixture_dir / "user_profiles_normalized.json"

    monkeypatch.setenv("GRAPHRAPPING_USER_PROFILES_JSON", "/data/real_profiles.json")
    assert _resolve_user_default_path(fixture_dir) == Path("/data/real_profiles.json")
