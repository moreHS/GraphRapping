"""
P3-2: `adapt_user_profile` must pass `last_seen_at` to basic, purchase_analysis,
and chat-derived facts so `recency_factor = exp(-λ·days_elapsed)` is not
silently 1.0.

Sources:
- chat facts + basic facts → `chat.updated_at` (basic is captured together
  with chat in the personal-agent flow; chat.updated_at is the best proxy)
- purchase_analysis facts → `purchase_features.last_seen_at` (max purchased_at)
- purchase-event facts (OWNS_*, REPURCHASES_FAMILY, REPURCHASES_BRAND,
  RECENTLY_PURCHASED) → already wired in Wave 1A, covered elsewhere
"""

from __future__ import annotations

from src.user.adapters.personal_agent_adapter import adapt_user_profile


_CHAT_TS = "2025-02-08"
_PURCHASE_TS = "2025-04-17"


def _profile(*, chat_updated_at: str | None = _CHAT_TS) -> dict:
    chat: dict = {
        "face": {"skin_concerns": ["건조함"], "skincare_goals": ["보습강화"], "preferred_texture": ["젤"]},
        "hair": {"hair_concerns": ["탈모"], "haircare_goals": ["볼륨"]},
        "scent": {"preferences": ["fresh"]},
        "ingredients": {"preferred": ["niacinamide"], "avoid": ["alcohol"], "allergy": ["fragrance"]},
    }
    if chat_updated_at is not None:
        chat["updated_at"] = chat_updated_at
    return {
        "basic": {"skin_type": "건성", "skin_tone": "웜톤"},
        "purchase_analysis": {
            "preferred_skincare_brand": ["라네즈"],
            "preferred_makeup_brand": ["nars"],
            "active_product_category": ["에센스"],
            "preferred_repurchase_category": ["크림"],
        },
        "chat": chat,
    }


def _purchase_features() -> dict:
    return {
        "last_seen_at": _PURCHASE_TS,
        "owned_product_ids": [],
        "owned_family_ids": [],
        "repurchased_family_ids": [],
        "repurchased_brand_ids": [],
        "recently_purchased_brand_ids": [],
    }


def _by_predicate(facts: list[dict], predicate: str) -> list[dict]:
    return [f for f in facts if f["predicate"] == predicate]


def test_basic_facts_carry_chat_updated_at() -> None:
    facts = adapt_user_profile("u1", _profile())
    for predicate in ("HAS_SKIN_TYPE", "HAS_SKIN_TONE"):
        rows = _by_predicate(facts, predicate)
        assert rows, f"{predicate} missing"
        for f in rows:
            assert f["last_seen_at"] == _CHAT_TS, \
                f"{predicate} last_seen_at={f['last_seen_at']} (expected {_CHAT_TS})"


def test_purchase_analysis_facts_carry_purchase_features_last_seen() -> None:
    facts = adapt_user_profile("u1", _profile(), purchase_features=_purchase_features())
    for predicate in ("PREFERS_BRAND", "ACTIVE_IN_CATEGORY", "REPURCHASES_CATEGORY"):
        rows = _by_predicate(facts, predicate)
        assert rows, f"{predicate} missing"
        for f in rows:
            assert f["last_seen_at"] == _PURCHASE_TS, \
                f"{predicate} last_seen_at={f['last_seen_at']} (expected {_PURCHASE_TS})"


def test_chat_facts_carry_chat_updated_at() -> None:
    facts = adapt_user_profile("u1", _profile())
    chat_predicates = (
        "PREFERS_INGREDIENT", "AVOIDS_INGREDIENT",
        "HAS_CONCERN", "WANTS_GOAL",
        "PREFERS_BEE_ATTR", "PREFERS_KEYWORD",
    )
    for predicate in chat_predicates:
        rows = _by_predicate(facts, predicate)
        assert rows, f"{predicate} missing"
        for f in rows:
            assert f["last_seen_at"] == _CHAT_TS, \
                f"{predicate} last_seen_at={f['last_seen_at']} (expected {_CHAT_TS})"


def test_chat_updated_at_absent_results_in_none_for_chat_and_basic() -> None:
    facts = adapt_user_profile("u1", _profile(chat_updated_at=None))
    for predicate in ("HAS_SKIN_TYPE", "HAS_SKIN_TONE", "PREFERS_INGREDIENT", "WANTS_GOAL"):
        rows = _by_predicate(facts, predicate)
        assert rows, f"{predicate} missing"
        for f in rows:
            assert f["last_seen_at"] is None, \
                f"{predicate} last_seen_at={f['last_seen_at']} (expected None)"


def test_purchase_features_absent_results_in_none_for_purchase_analysis() -> None:
    facts = adapt_user_profile("u1", _profile())  # no purchase_features
    for predicate in ("PREFERS_BRAND", "ACTIVE_IN_CATEGORY", "REPURCHASES_CATEGORY"):
        rows = _by_predicate(facts, predicate)
        assert rows, f"{predicate} missing"
        for f in rows:
            assert f["last_seen_at"] is None, \
                f"{predicate} last_seen_at={f['last_seen_at']} (expected None)"


def test_purchase_event_facts_still_use_purchase_features_ts() -> None:
    """Regression: Wave 1A wiring for OWNS_PRODUCT etc. must remain intact."""
    pf = _purchase_features() | {
        "owned_product_ids": ["P001"],
        "repurchased_brand_ids": ["brand_x"],
    }
    facts = adapt_user_profile("u1", _profile(), purchase_features=pf)
    owns = _by_predicate(facts, "OWNS_PRODUCT")
    repurchase = _by_predicate(facts, "REPURCHASES_BRAND")
    assert owns and all(f["last_seen_at"] == _PURCHASE_TS for f in owns)
    assert repurchase and all(f["last_seen_at"] == _PURCHASE_TS for f in repurchase)
