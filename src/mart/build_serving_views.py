"""
Build serving profiles: table-based mart.

serving_product_profile: master truth + concept_ids + review signal aggregates + freshness
serving_user_profile: demographics + preference edges
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_serving_product_profile(
    product_master: dict[str, Any],
    agg_signals: list[dict[str, Any]],
    window_type: str = "all",
    concept_links: list[dict] | None = None,
    promoted_only: bool = True,
) -> dict[str, Any]:
    """Build a single serving_product_profile row.

    Args:
        product_master: Row from product_master table
        agg_signals: Rows from agg_product_signal for this product
        window_type: Which window for top signals
        concept_links: entity_concept_link rows for this product
        promoted_only: If True (default), only include signals where
            is_promoted is True.  Set to False for debug/exploration.
    """
    pid = product_master["product_id"]
    links = concept_links or []

    # Extract concept_ids by link_type (concept_id is the canonical join key)
    brand_concepts = [l["concept_id"] for l in links if l.get("link_type") == "HAS_BRAND"]
    category_concepts = [l["concept_id"] for l in links if l.get("link_type") == "IN_CATEGORY"]
    ingredient_concepts = [l["concept_id"] for l in links if l.get("link_type") == "HAS_INGREDIENT"]
    benefit_concepts = [l["concept_id"] for l in links if l.get("link_type") == "HAS_MAIN_BENEFIT"]

    # Filter signals for requested window
    window_signals = [s for s in agg_signals if s.get("window_type") == window_type]
    if promoted_only:
        window_signals = [s for s in window_signals if s.get("is_promoted", False)]

    # Defense-in-depth: exclude catalog_validation even if aggregator leaks
    window_signals = [s for s in window_signals
                      if s.get("canonical_edge_type") != "CATALOG_VALIDATION_SIGNAL"]

    # Group by edge_type and pick top-N by score
    by_edge: dict[str, list[dict]] = defaultdict(list)
    for s in window_signals:
        by_edge[s.get("canonical_edge_type", "")].append(s)

    def _top_n(edge_type: str, n: int = 10) -> list[dict]:
        items = by_edge.get(edge_type, [])
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return [{"id": i["dst_node_id"], "score": i["score"], "review_cnt": i["review_cnt"]} for i in items[:n]]

    # Freshness: get windowed counts
    signals_30d = [s for s in agg_signals if s.get("window_type") == "30d"
                   and (not promoted_only or s.get("is_promoted", False))]
    signals_90d = [s for s in agg_signals if s.get("window_type") == "90d"
                   and (not promoted_only or s.get("is_promoted", False))]
    review_count_30d = sum(s.get("review_cnt", 0) for s in signals_30d)
    review_count_90d = sum(s.get("review_cnt", 0) for s in signals_90d)
    review_count_all = sum(s.get("review_cnt", 0) for s in window_signals)

    # Last signal timestamp
    last_seen_values = [s.get("last_seen_at") for s in agg_signals if s.get("last_seen_at")]
    last_signal_at = max(last_seen_values) if last_seen_values else None

    return {
        "product_id": pid,
        # Truth columns (raw)
        "brand_id": product_master.get("brand_id"),
        "brand_name": product_master.get("brand_name"),
        "category_id": product_master.get("category_id"),
        "category_name": product_master.get("category_name"),
        "country_of_origin": product_master.get("country_of_origin"),
        "price": product_master.get("price"),
        "ingredient_ids": product_master.get("ingredients", []),
        # Concept ID fields (canonical join keys — concept_id, not raw IRI)
        "brand_concept_ids": brand_concepts,
        "category_concept_ids": category_concepts,
        "ingredient_concept_ids": ingredient_concepts,
        "main_benefit_concept_ids": benefit_concepts,
        # Signal columns
        "top_bee_attr_ids": _top_n("HAS_BEE_ATTR_SIGNAL"),
        "top_keyword_ids": _top_n("HAS_BEE_KEYWORD_SIGNAL"),
        "top_context_ids": _top_n("USED_IN_CONTEXT_SIGNAL"),
        "top_concern_pos_ids": _top_n("ADDRESSES_CONCERN_SIGNAL"),
        "top_concern_neg_ids": _top_n("MAY_CAUSE_CONCERN_SIGNAL"),
        "top_tool_ids": _top_n("USED_WITH_TOOL_SIGNAL"),
        "top_comparison_product_ids": _top_n("COMPARED_WITH_SIGNAL"),
        "top_coused_product_ids": _top_n("USED_WITH_PRODUCT_SIGNAL"),
        # Freshness
        "review_count_30d": review_count_30d,
        "review_count_90d": review_count_90d,
        "review_count_all": review_count_all,
        "last_signal_at": last_signal_at,
    }


def build_serving_user_profile(
    user_master: dict[str, Any],
    preferences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a single serving_user_profile row."""
    uid = user_master["user_id"]

    def _collect(edge_type: str) -> list[dict]:
        items = [p for p in preferences if p.get("preference_edge_type") == edge_type]
        items.sort(key=lambda x: x.get("weight", 0), reverse=True)
        return [{"id": i["dst_node_id"], "weight": i["weight"]} for i in items[:20]]

    return {
        "user_id": uid,
        "age_band": user_master.get("age_band"),
        "gender": user_master.get("gender"),
        "skin_type": user_master.get("skin_type"),
        "skin_tone": user_master.get("skin_tone"),
        "preferred_brand_ids": _collect("PREFERS_BRAND"),
        "preferred_category_ids": _collect("PREFERS_CATEGORY"),
        "preferred_ingredient_ids": _collect("PREFERS_INGREDIENT"),
        "avoided_ingredient_ids": _collect("AVOIDS_INGREDIENT"),
        "concern_ids": _collect("HAS_CONCERN"),
        "goal_ids": _collect("WANTS_GOAL"),
        "preferred_bee_attr_ids": _collect("PREFERS_BEE_ATTR"),
        "preferred_keyword_ids": _collect("PREFERS_KEYWORD"),
        "preferred_context_ids": _collect("PREFERS_CONTEXT"),
        # Behavior section (purchase-derived)
        "recent_purchase_brand_ids": _collect("RECENTLY_PURCHASED"),
        "repurchase_brand_ids": _collect("REPURCHASES_BRAND"),
        "repurchase_category_ids": _collect("REPURCHASES_CATEGORY"),
        "owned_product_ids": _collect("OWNS_PRODUCT"),
    }
