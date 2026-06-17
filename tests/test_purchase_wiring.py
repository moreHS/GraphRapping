"""
Sub-task 1A (P0-1) wiring tests.

Verifies that purchase events flow through the loader → adapter → user fact build
→ serving_user_profile → scorer chain. Before this sub-task, OWNS_*/REPURCHASES_*/
RECENTLY_PURCHASED facts were never generated in production wiring (helpers existed
but were not called from any entry point).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.ingest.purchase_ingest import (
    PurchaseEvent,
    PurchaseFeatures,
    derive_purchase_features,
    purchase_features_to_adapter_dict,
)
from src.loaders.user_loader import load_users_from_profiles


# ---------------------------------------------------------------------------
# TC1: derive_purchase_features extension fields
# ---------------------------------------------------------------------------

def test_derive_purchase_features_repurchased_family_and_last_seen() -> None:
    """Two purchases in the same family should mark fam as repurchased + capture
    max purchased_at as last_seen_at."""
    purchases = [
        PurchaseEvent("e1", "u1", "P1", purchased_at="2026-04-01"),
        PurchaseEvent("e2", "u1", "P2", purchased_at="2026-04-15"),
    ]
    family_lookup = {"P1": "fam_A", "P2": "fam_A"}
    pf = derive_purchase_features(purchases, family_lookup=family_lookup)

    assert "fam_A" in pf.repurchased_family_ids
    assert pf.owned_family_ids == {"fam_A"}
    assert pf.last_seen_at == "2026-04-15"


def test_derive_purchase_features_single_purchase_no_repurchase() -> None:
    """A single purchase per family must not produce repurchased_family_ids."""
    purchases = [PurchaseEvent("e1", "u1", "P1", purchased_at="2026-04-01")]
    family_lookup = {"P1": "fam_A"}
    pf = derive_purchase_features(purchases, family_lookup=family_lookup)
    assert pf.repurchased_family_ids == set()
    assert pf.owned_family_ids == {"fam_A"}


def test_derive_purchase_features_no_purchased_at_returns_none() -> None:
    """When no event has purchased_at, last_seen_at is None."""
    purchases = [PurchaseEvent("e1", "u1", "P1")]
    pf = derive_purchase_features(purchases)
    assert pf.last_seen_at is None


# ---------------------------------------------------------------------------
# TC2: purchase_features_to_adapter_dict contract
# ---------------------------------------------------------------------------

def test_adapter_dict_conversion_shape() -> None:
    """Dict conversion must expose all keys adapt_user_profile() reads via .get()."""
    pf = PurchaseFeatures(
        owned_product_ids={"P1"},
        owned_family_ids={"fam_A"},
        recently_purchased_brand_ids={"b1"},
        repurchased_brand_ids=set(),
        repurchased_category_ids=set(),
        repurchased_family_ids={"fam_A"},
        last_seen_at="2026-04-15",
    )
    d = purchase_features_to_adapter_dict(pf)

    assert d["last_seen_at"] == "2026-04-15"
    assert d["repurchased_family_ids"] == ["fam_A"]
    assert d["owned_product_ids"] == ["P1"]
    # dict shape supports adapter's .get() pattern
    assert d.get("owned_family_ids") == ["fam_A"]
    assert d.get("missing_key", "default") == "default"


def test_adapter_dict_sorted_lists_are_deterministic() -> None:
    """Lists must be sorted for test stability."""
    pf = PurchaseFeatures(
        owned_product_ids={"P3", "P1", "P2"},
        owned_family_ids=set(),
        recently_purchased_brand_ids=set(),
        repurchased_brand_ids=set(),
        repurchased_category_ids=set(),
        repurchased_family_ids=set(),
        last_seen_at=None,
    )
    d = purchase_features_to_adapter_dict(pf)
    assert d["owned_product_ids"] == ["P1", "P2", "P3"]


# ---------------------------------------------------------------------------
# TC3: load_users_from_profiles integrated wiring (with purchase events)
# ---------------------------------------------------------------------------

def test_load_users_with_purchase_events_generates_owned_facts() -> None:
    """purchase_events_by_user must flow through to OWNS_PRODUCT/OWNS_FAMILY facts."""
    user_profiles = {
        "u1": {"basic": {"skin_type": "건성"}, "purchase_analysis": {}},
    }
    purchase_events = {
        "u1": [PurchaseEvent("e1", "u1", "P1", purchased_at="2026-04-01")],
    }
    family_lookup = {"P1": "fam_A"}

    result = load_users_from_profiles(
        user_profiles,
        purchase_events_by_user=purchase_events,
        family_lookup=family_lookup,
    )
    facts = result.user_adapted_facts["u1"]
    predicates = {f["predicate"] for f in facts}

    assert "OWNS_PRODUCT" in predicates
    assert "OWNS_FAMILY" in predicates


# ---------------------------------------------------------------------------
# TC4: backward compat — no purchase events → no purchase facts
# ---------------------------------------------------------------------------

def test_load_users_without_purchase_events_skips_owned() -> None:
    """When purchase_events_by_user is None, no OWNS_* facts are generated, but
    basic profile facts still flow through."""
    user_profiles = {
        "u1": {"basic": {"skin_type": "건성"}, "purchase_analysis": {}},
    }
    result = load_users_from_profiles(user_profiles)
    facts = result.user_adapted_facts["u1"]
    predicates = {f["predicate"] for f in facts}

    assert "OWNS_PRODUCT" not in predicates
    assert "OWNS_FAMILY" not in predicates
    # basic.skin_type still produces HAS_SKIN_TYPE
    assert "HAS_SKIN_TYPE" in predicates


# ---------------------------------------------------------------------------
# TC5: run_full_load smoke — FullLoadResult exposes serving_users
# ---------------------------------------------------------------------------

def test_run_full_load_exposes_serving_users(tmp_path: Path) -> None:
    """run_full_load() must populate result.serving_users so downstream verification
    (and TC6) can inspect them. Pre-P0-1 the result only exposed counts."""
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    review_path = tmp_path / "empty_reviews.json"
    review_path.write_text("[]", encoding="utf-8")

    config = FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[
            {
                "ONLINE_PROD_SERIAL_NUMBER": "P1",
                "BRAND_NAME": "B1",
                "REPRESENTATIVE_PROD_CODE": "fam_A",
                "SALE_STATUS": "판매중",
                "prd_nm": "Product 1",
                "CTGR_SS_NAME": "스킨케어",
            }
        ],
        user_profiles={
            "u1": {"basic": {"skin_type": "건성"}, "purchase_analysis": {}},
        },
        purchase_events_by_user={
            "u1": [PurchaseEvent("e1", "u1", "P1", purchased_at="2026-04-01")],
        },
    )
    result = run_full_load(config)

    assert result.serving_users, "serving_users must be exposed for verification"
    u1 = next(u for u in result.serving_users if u["user_id"] == "u1")
    owned_pids = {
        e["id"].replace("product:", "") for e in u1["owned_product_ids"]
    }
    assert "P1" in owned_pids


# ---------------------------------------------------------------------------
# TC6: full chain — purchase → serving → scorer feature non-zero
# ---------------------------------------------------------------------------

def test_full_chain_repurchased_family_affects_scorer(tmp_path: Path) -> None:
    """End-to-end: 2 purchases in same family/brand →
        REPURCHASES_FAMILY / REPURCHASES_BRAND / RECENTLY_PURCHASED →
        serving_user_profile fields →
        scorer features (repurchase_family_affinity, purchase_loyalty_score) > 0.

    Brand normalization: product_loader stores brand_id as normalize_text(brand_name),
    so "B1" → "b1". Serving brand prefs are concept IRI "concept:Brand:b1".
    scorer._strip_brand strips that prefix, so P3.brand_id should be raw "b1".
    """
    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    from src.rec.scorer import Scorer

    review_path = tmp_path / "empty_reviews.json"
    review_path.write_text("[]", encoding="utf-8")

    config = FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[
            {
                "ONLINE_PROD_SERIAL_NUMBER": "P1",
                "BRAND_NAME": "B1",
                "REPRESENTATIVE_PROD_CODE": "fam_A",
                "SALE_STATUS": "판매중",
                "prd_nm": "Product 1",
                "CTGR_SS_NAME": "스킨케어",
            },
            {
                "ONLINE_PROD_SERIAL_NUMBER": "P2",
                "BRAND_NAME": "B1",
                "REPRESENTATIVE_PROD_CODE": "fam_A",
                "SALE_STATUS": "판매중",
                "prd_nm": "Product 2",
                "CTGR_SS_NAME": "스킨케어",
            },
        ],
        user_profiles={
            "u1": {"basic": {"skin_type": "건성"}, "purchase_analysis": {}},
        },
        purchase_events_by_user={
            "u1": [
                PurchaseEvent("e1", "u1", "P1", purchased_at="2026-04-01"),
                PurchaseEvent("e2", "u1", "P2", purchased_at="2026-04-15"),
            ],
        },
    )
    result = run_full_load(config)
    u1 = next(u for u in result.serving_users if u["user_id"] == "u1")

    # Helper: strip "concept:Brand:" prefix from serving brand id entries.
    def stripped_brand_ids(entries: list) -> set[str]:
        out: set[str] = set()
        for e in entries:
            if isinstance(e, dict):
                raw = e["id"]
                out.add(raw[len("concept:Brand:"):] if raw.startswith("concept:Brand:") else raw)
        return out

    # OWNS_PRODUCT — product IRI ("product:P1") → strip prefix
    owned_pids = {e["id"].replace("product:", "") for e in u1["owned_product_ids"]}
    assert {"P1", "P2"} <= owned_pids

    # OWNS_FAMILY / REPURCHASES_FAMILY — _make_product_ref wraps family_id as product:fam_A
    owned_families = {e["id"].replace("product:", "") for e in u1["owned_family_ids"]}
    assert "fam_A" in owned_families
    repurchased_families = {
        e["id"].replace("product:", "") for e in u1["repurchased_family_ids"]
    }
    assert "fam_A" in repurchased_families

    # REPURCHASES_BRAND — concept IRI "concept:Brand:b1"
    assert "b1" in stripped_brand_ids(u1["repurchase_brand_ids"])

    # RECENTLY_PURCHASED — concept IRI "concept:Brand:b1"
    assert "b1" in stripped_brand_ids(u1["recent_purchase_brand_ids"])

    # Scorer: a different product P3 in same family/brand (not owned) should
    # earn non-zero repurchase_family_affinity AND purchase_loyalty_score.
    scorer = Scorer()
    scorer.load_config()
    p3_profile = {
        "product_id": "P3",
        "variant_family_id": "fam_A",
        "brand_id": "b1",  # raw normalized (matches scorer._strip_brand comparison)
        "review_count_all": 0,
    }
    scored = scorer.score(u1, p3_profile, overlap_concepts=[])

    assert scored.feature_contributions.get("repurchase_family_affinity", 0) > 0
    assert scored.feature_contributions.get("purchase_loyalty_score", 0) > 0


# ---------------------------------------------------------------------------
# TC7: run_full_load entry point forwards purchase_events to run_batch
#       (brand-confidence weighting must reach serving profile)
# ---------------------------------------------------------------------------

def test_run_full_load_forwards_purchase_events_to_run_batch(tmp_path: Path) -> None:
    """End-to-end regression: if run_full_load() ever stops forwarding
    purchase_events_by_user to run_batch, this test catches it.

    Strategy: run two full loads with the SAME PREFERS_BRAND fact (from
    purchase_analysis), once with purchase events and once without. The
    purchase-side serving profile must show a strictly higher weight for that
    brand because derive_brand_confidence() bumps max_confidence 0.8 → 1.0.
    """
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    review_path = tmp_path / "empty_reviews.json"
    review_path.write_text("[]", encoding="utf-8")

    product_records = [
        {
            "ONLINE_PROD_SERIAL_NUMBER": "P1",
            "BRAND_NAME": "B1",
            "SALE_STATUS": "판매중",
            "prd_nm": "Product 1",
            "CTGR_SS_NAME": "스킨케어",
        },
    ]
    # user_profile produces a PREFERS_BRAND fact via purchase_analysis path
    # (adapter._make_pref → confidence=0.8, source="purchase" tag in fact, but no
    # purchase-event-derived brand_confidence boost until run_batch path runs).
    user_profiles = {
        "u1": {
            "basic": {"skin_type": "건성"},
            "purchase_analysis": {"preferred_skincare_brand": ["B1"]},
        },
    }

    def _b1_pref(serving_users: list[dict]) -> dict:
        u1 = next(u for u in serving_users if u["user_id"] == "u1")
        return next(
            e for e in u1["preferred_brand_ids"]
            if e["id"].endswith(":b1")
        )

    # Without purchases
    cfg_without = FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=product_records,
        user_profiles=user_profiles,
    )
    result_without = run_full_load(cfg_without)
    pref_without = _b1_pref(result_without.serving_users)

    # With purchases on same brand. Dates must be recent enough that
    # recency_factor (exp(-λ·days)) does not eat the 0.8→1.0 brand_confidence
    # boost. P3-2 activated recency on purchase_analysis facts; with λ=0.01
    # the boost dominates when days_elapsed < ~22.
    today = datetime.now(timezone.utc).date()
    recent = (today - timedelta(days=2)).isoformat()
    cfg_with = FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=product_records,
        user_profiles=user_profiles,
        purchase_events_by_user={
            "u1": [
                PurchaseEvent("e1", "u1", "P1", purchased_at=recent),
                PurchaseEvent("e2", "u1", "P1", purchased_at=recent),
            ],
        },
    )
    result_with = run_full_load(cfg_with)
    pref_with = _b1_pref(result_with.serving_users)

    # Purchase-event forwarding to run_batch must materialize in serving weight.
    assert pref_with["weight"] > pref_without["weight"], (
        f"Purchase boost did not reach serving profile via run_batch: "
        f"with={pref_with['weight']} vs without={pref_without['weight']}"
    )
