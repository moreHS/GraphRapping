"""
Build serving profiles: table-based mart.

serving_product_profile: master truth + concept_ids + review signal aggregates + freshness
serving_user_profile: demographics + preference edges
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def build_serving_product_profile(
    product_master: dict[str, Any],
    agg_signals: list[dict[str, Any]],
    window_type: str = "all",
    concept_links: list[dict] | None = None,
    promoted_only: bool = True,
    source_review_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single serving_product_profile row.

    Args:
        product_master: Row from product_master table
        agg_signals: Rows from agg_product_signal for this product
        window_type: Which window for top signals
        concept_links: entity_concept_link rows for this product
        promoted_only: If True (default), only include signals where
            is_promoted is True.  Set to False for debug/exploration.
        source_review_stats: Optional source-grounded review volume/rating
            stats. These are emitted as explicit source_* fields and never
            redefine review_count_* graph support fields.
    """
    pid = product_master["product_id"]
    links = concept_links or []
    stats = source_review_stats or {}

    # Extract concept_ids by link_type (concept_id is the canonical join key)
    brand_concepts = [link["concept_id"] for link in links if link.get("link_type") == "HAS_BRAND"]
    category_concepts = [link["concept_id"] for link in links if link.get("link_type") == "IN_CATEGORY"]
    ingredient_concepts = [link["concept_id"] for link in links if link.get("link_type") == "HAS_INGREDIENT"]
    benefit_concepts = [link["concept_id"] for link in links if link.get("link_type") == "HAS_MAIN_BENEFIT"]

    # P3-8 (Wave 2.10): exclude soft-deleted aggregate rows. Default True
    # so in-memory callers (no is_active field) behave unchanged; DB readers
    # honor the soft-delete contract.
    active_signals = [s for s in agg_signals if s.get("is_active", True)]

    # Filter signals for requested window
    window_signals = [s for s in active_signals if s.get("window_type") == window_type]
    if promoted_only:
        window_signals = [s for s in window_signals if s.get("is_promoted", False)]

    # Defense-in-depth: exclude catalog_validation even if aggregator leaks
    window_signals = [s for s in window_signals
                      if s.get("canonical_edge_type") != "CATALOG_VALIDATION_SIGNAL"]

    # Group by edge_type and pick top-N by score
    by_edge: dict[str, list[dict]] = defaultdict(list)
    for s in window_signals:
        by_edge[s.get("canonical_edge_type", "")].append(s)

    def _top_n(edge_type: str, n: int = 10, min_label_len: int = 0) -> list[dict]:
        items = by_edge.get(edge_type, [])
        if min_label_len > 0:
            items = [i for i in items if len((i.get("dst_node_id") or "").split(":")[-1]) >= min_label_len]
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return [{"id": i["dst_node_id"], "score": i["score"], "review_cnt": i["review_cnt"]} for i in items[:n]]

    # Freshness: get windowed counts. Reuse `active_signals` so soft-deleted
    # rows (P3-8) don't inflate freshness counts.
    signals_30d = [s for s in active_signals if s.get("window_type") == "30d"
                   and (not promoted_only or s.get("is_promoted", False))]
    signals_90d = [s for s in active_signals if s.get("window_type") == "90d"
                   and (not promoted_only or s.get("is_promoted", False))]

    def _distinct_reviews(rows: list[dict]) -> int:
        """Product-level distinct review_id count via union across signal rows.

        P3-7: prior implementation summed each row's `review_cnt`, which
        double-counts a review that contributes to multiple (edge, dst)
        groups. Now we union review_ids carried transiently on each row.
        """
        union: set[str] = set()
        for row in rows:
            union.update(rid for rid in row.get("review_ids", []) if rid)
        return len(union)

    review_count_30d = _distinct_reviews(signals_30d)
    review_count_90d = _distinct_reviews(signals_90d)
    review_count_all = _distinct_reviews(window_signals)
    # P3-7: signal_support_count_all is the prior inflated sum, exposed
    # explicitly as "signal lines × occurrences" so downstream code that
    # genuinely needs that quantity (e.g. UI badges) can still get it.
    signal_support_count_all = sum(s.get("review_cnt", 0) for s in window_signals)

    # Last signal timestamp
    # P3-8: derive freshness from active rows only — inactive (soft-deleted)
    # aggregates must not surface as the product's "last signal" timestamp.
    # Wave 4 Task 4: keep native datetime when present (asyncpg timestamptz
    # binding requires a date/datetime, not a string).
    last_seen_values = [s["last_seen_at"] for s in active_signals if s.get("last_seen_at")]
    last_signal_at = max(last_seen_values, key=str) if last_seen_values else None

    def _source_stat(name: str, fallback_name: str | None = None, default: Any = None) -> Any:
        value = stats.get(name)
        if value is None and fallback_name is not None:
            value = stats.get(fallback_name)
        return default if value is None else value

    representative_product_name = (
        product_master.get("representative_product_name")
        or product_master.get("_es_meta", {}).get("REPRESENTATIVE_PROD_NAME")
    )

    return {
        "product_id": pid,
        "source_product_id": (
            _source_stat("source_product_id")
            or product_master.get("source_product_id")
            or pid
        ),
        "source_channel": _source_stat("source_channel") or product_master.get("source_channel"),
        "source_key_type": _source_stat("source_key_type") or product_master.get("source_key_type"),
        # Truth columns (raw)
        "brand_id": product_master.get("brand_id"),
        "brand_name": product_master.get("brand_name"),
        "category_id": product_master.get("category_id"),
        "category_name": product_master.get("category_name"),
        "country_of_origin": product_master.get("country_of_origin"),
        "price": product_master.get("price"),
        # price_band: category-derived band ("low"/"mid"/"premium") — not yet
        # populated by any rule; column kept for forward compatibility.
        "price_band": product_master.get("price_band"),
        "variant_family_id": product_master.get("variant_family_id"),
        "representative_product_name": representative_product_name,
        "main_benefit_ids": product_master.get("main_benefits", []),
        "ingredient_ids": product_master.get("ingredients", []),
        # Concept ID fields (canonical join keys — concept_id, not raw IRI)
        "brand_concept_ids": brand_concepts,
        "category_concept_ids": category_concepts,
        "ingredient_concept_ids": ingredient_concepts,
        "main_benefit_concept_ids": benefit_concepts,
        # Signal columns
        "top_bee_attr_ids": _top_n("HAS_BEE_ATTR_SIGNAL"),
        "top_keyword_ids": _top_n("HAS_BEE_KEYWORD_SIGNAL", min_label_len=2),
        "top_context_ids": _top_n("USED_IN_CONTEXT_SIGNAL"),
        "top_concern_pos_ids": _top_n("ADDRESSES_CONCERN_SIGNAL"),
        "top_concern_neg_ids": _top_n("MAY_CAUSE_CONCERN_SIGNAL"),
        "top_tool_ids": _top_n("USED_WITH_TOOL_SIGNAL"),
        "top_comparison_product_ids": _top_n("COMPARED_WITH_SIGNAL"),
        "top_coused_product_ids": _top_n("USED_WITH_PRODUCT_SIGNAL"),
        # Freshness
        "last_signal_at": last_signal_at,
        "review_count_30d": review_count_30d,
        "review_count_90d": review_count_90d,
        "review_count_all": review_count_all,
        "signal_support_count_all": signal_support_count_all,
        "source_review_count_6m": _source_stat("source_review_count_6m", "review_count_6m"),
        "source_review_score_count_6m": _source_stat(
            "source_review_score_count_6m", "score_count_6m",
        ),
        "source_avg_rating_6m": _source_stat("source_avg_rating_6m", "avg_rating_6m"),
        "source_review_min_date_6m": _source_stat(
            "source_review_min_date_6m", "review_min_date_6m",
        ),
        "source_review_max_date_6m": _source_stat(
            "source_review_max_date_6m", "review_max_date_6m",
        ),
        "source_review_count_all": _source_stat("source_review_count_all", "review_count_all"),
        "source_review_score_count_all": _source_stat(
            "source_review_score_count_all", "score_count_all",
        ),
        "source_avg_rating_all": _source_stat("source_avg_rating_all", "avg_rating_all"),
        "source_review_min_date_all": _source_stat(
            "source_review_min_date_all", "review_min_date_all",
        ),
        "source_review_max_date_all": _source_stat(
            "source_review_max_date_all", "review_max_date_all",
        ),
        "source_review_stats_source": _source_stat("source_review_stats_source", "source"),
    }


def build_serving_user_profile(
    user_master: dict[str, Any],
    preferences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a single serving_user_profile row."""
    uid = user_master["user_id"]

    # P3-8 (Wave 2.10): exclude soft-deleted user-preference rows. Default
    # True so in-memory callers (no is_active field) behave unchanged.
    active_preferences = [p for p in preferences if p.get("is_active", True)]

    def _collect(edge_type: str) -> list[dict]:
        items = [p for p in active_preferences if p.get("preference_edge_type") == edge_type]
        by_id: dict[str, dict] = {}
        for item in items:
            dst_id = item.get("dst_node_id")
            if not dst_id:
                continue
            if dst_id not in by_id or (item.get("weight") or 0) > (by_id[dst_id].get("weight") or 0):
                by_id[dst_id] = item
        values = list(by_id.values())
        values.sort(key=lambda x: x.get("weight", 0), reverse=True)
        return [{"id": i["dst_node_id"], "weight": i["weight"]} for i in values[:20]]

    def _collect_scoped() -> list[dict]:
        items = list(active_preferences)
        items.sort(key=lambda x: (x.get("preference_edge_type", ""), -float(x.get("weight") or 0)))
        result: list[dict] = []
        for item in items:
            result.append({
                "edge_type": item.get("preference_edge_type", ""),
                "id": item.get("dst_node_id", ""),
                "weight": item.get("weight", 0),
                "scope_group": _preference_scope_group(item),
                "source_sections": _preference_source_sections(item),
            })
        return result

    return {
        "user_id": uid,
        "age_band": user_master.get("age_band"),
        "gender": user_master.get("gender"),
        "skin_type": user_master.get("skin_type"),
        "skin_tone": user_master.get("skin_tone"),
        "preferred_brand_ids": _collect("PREFERS_BRAND"),
        "active_category_ids": _collect("ACTIVE_IN_CATEGORY"),
        "preferred_category_ids": _collect("PREFERS_CATEGORY"),
        "preferred_ingredient_ids": _collect("PREFERS_INGREDIENT"),
        "avoided_ingredient_ids": _collect("AVOIDS_INGREDIENT"),
        "concern_ids": _collect("HAS_CONCERN"),
        "goal_ids": _collect("WANTS_GOAL"),
        "preferred_bee_attr_ids": _collect("PREFERS_BEE_ATTR"),
        "preferred_keyword_ids": _collect("PREFERS_KEYWORD"),
        "preferred_context_ids": _collect("PREFERS_CONTEXT"),
        "scoped_preference_ids": _collect_scoped(),
        # Behavior section (purchase-derived)
        "recent_purchase_brand_ids": _collect("RECENTLY_PURCHASED"),
        "repurchase_brand_ids": _collect("REPURCHASES_BRAND"),
        "repurchase_category_ids": _collect("REPURCHASES_CATEGORY"),
        "owned_product_ids": _collect("OWNS_PRODUCT"),
        "owned_family_ids": _collect("OWNS_FAMILY"),
        "repurchased_family_ids": _collect("REPURCHASES_FAMILY"),
    }


def _preference_source_mix(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("source_mix") or {}
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return value if isinstance(value, dict) else {}


def _preference_scope_group(item: dict[str, Any]) -> str | None:
    scope = item.get("scope_group")
    if not scope:
        scope = _preference_source_mix(item).get("scope_group")
    return str(scope) if scope else None


def _preference_source_sections(item: dict[str, Any]) -> list[str]:
    sections = item.get("source_sections")
    if not sections:
        sections = _preference_source_mix(item).get("source_sections")
    if isinstance(sections, str):
        return [sections]
    if isinstance(sections, list):
        return [str(section) for section in sections if section]
    return []
