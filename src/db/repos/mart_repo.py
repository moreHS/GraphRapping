"""
Mart repository: agg_product_signal + agg_user_preference + serving profiles.
"""

from __future__ import annotations

import json
from typing import Any

from src.db.unit_of_work import UnitOfWork


async def upsert_agg_product_signal(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO agg_product_signal (target_product_id, canonical_edge_type,
            dst_node_type, dst_node_id, window_type, review_cnt, pos_cnt, neg_cnt,
            neu_cnt, support_count, score, recent_score, recent_support_count,
            last_seen_at, window_start, window_end, evidence_sample, updated_at,
            distinct_review_count, avg_confidence, synthetic_ratio, corpus_weight, is_promoted)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
        ON CONFLICT (target_product_id, canonical_edge_type, dst_node_id, window_type) DO UPDATE SET
            review_cnt=EXCLUDED.review_cnt, pos_cnt=EXCLUDED.pos_cnt,
            neg_cnt=EXCLUDED.neg_cnt, neu_cnt=EXCLUDED.neu_cnt,
            support_count=EXCLUDED.support_count, score=EXCLUDED.score,
            last_seen_at=EXCLUDED.last_seen_at, evidence_sample=EXCLUDED.evidence_sample,
            updated_at=EXCLUDED.updated_at,
            distinct_review_count=EXCLUDED.distinct_review_count,
            avg_confidence=EXCLUDED.avg_confidence,
            synthetic_ratio=EXCLUDED.synthetic_ratio,
            corpus_weight=EXCLUDED.corpus_weight,
            is_promoted=EXCLUDED.is_promoted,
            -- P3-8: re-upsert reactivates a previously soft-deleted row
            is_active=true
    """,
        row["target_product_id"], row["canonical_edge_type"],
        row["dst_node_type"], row["dst_node_id"], row["window_type"],
        row["review_cnt"], row["pos_cnt"], row["neg_cnt"],
        row.get("neu_cnt", 0), row["support_count"], row["score"],
        row.get("recent_score"), row.get("recent_support_count"),
        row.get("last_seen_at"), row.get("window_start"), row.get("window_end"),
        # evidence_sample is jsonb: asyncpg cannot bind a list directly.
        json.dumps(row.get("evidence_sample")) if row.get("evidence_sample") is not None else None,
        uow.as_of_ts,
        row.get("distinct_review_count", 0), row.get("avg_confidence", 0.0),
        row.get("synthetic_ratio", 0.0), row.get("corpus_weight", 0.0),
        row.get("is_promoted", False),
    )


async def batch_aggregate_product_signals_sql(
    uow: UnitOfWork,
    dirty_product_ids: set[str],
    windows: list[str] | None = None,
) -> int:
    """Batch re-aggregate product signals via SQL group-by for dirty products.

    This is the SQL-first path (P1-1). Falls back to Python aggregate for debug.
    Returns number of upserted rows.
    """
    if not dirty_product_ids:
        return 0

    windows = windows or ["30d", "90d", "all"]
    product_list = list(dirty_product_ids)
    count = 0

    for window_type in windows:
        if window_type == "all":
            window_clause = "TRUE"
        elif window_type == "30d":
            window_clause = "window_ts >= now() - interval '30 days'"
        else:
            window_clause = "window_ts >= now() - interval '90 days'"

        # P3-8 (Wave 2.10) + P3-6/P3-7 (Wave 3.9): this SQL path must populate
        #  - last_seen_at + is_active=true (P3-8: cleanup compatibility)
        #  - distinct_review_count + avg_confidence + synthetic_ratio (P3-6/P3-7:
        #    fact-level metadata so a post-hoc Python step does not need to
        #    re-read wrapped_signal just to compute corpus quality fields).
        # `is_promoted` and `corpus_weight` are intentionally NOT set here:
        # is_promoted has window-aware threshold logic (`is_corpus_promoted`)
        # that's clearer in Python, and corpus_weight = N × conf × recency
        # depends on a window-specific recency multiplier.
        rows = await uow.fetch(f"""
            INSERT INTO agg_product_signal
                (target_product_id, canonical_edge_type, dst_node_type, dst_node_id,
                 window_type, review_cnt, pos_cnt, neg_cnt, neu_cnt, support_count, score,
                 last_seen_at, updated_at,
                 distinct_review_count, avg_confidence, synthetic_ratio)
            SELECT
                target_product_id,
                edge_type,
                dst_type,
                dst_id,
                $2::text,
                -- P3-7 (Wave 3.9): NULLIF mirrors the Python aggregator which
                -- excludes empty/None review_ids from distinct counts.
                COUNT(DISTINCT NULLIF(review_id, '')),
                COUNT(*) FILTER (WHERE polarity = 'POS'),
                COUNT(*) FILTER (WHERE polarity = 'NEG'),
                COUNT(*) FILTER (WHERE polarity IS NULL OR polarity NOT IN ('POS','NEG')),
                COUNT(*),
                CASE WHEN COUNT(*) > 0
                    THEN (COUNT(*) FILTER (WHERE polarity='POS') - COUNT(*) FILTER (WHERE polarity='NEG'))::real / COUNT(*)
                    ELSE 0 END,
                MAX(window_ts),
                now(),
                COUNT(DISTINCT NULLIF(review_id, '')),
                COALESCE(AVG(source_confidence)::real, 0.0),
                CASE WHEN COUNT(*) > 0
                    THEN (COUNT(*) FILTER (WHERE evidence_kind = 'BEE_SYNTHETIC'))::real / COUNT(*)
                    ELSE 0 END
            FROM wrapped_signal
            WHERE target_product_id = ANY($1)
              AND signal_family != 'CATALOG_VALIDATION'
              AND {window_clause}
            GROUP BY target_product_id, edge_type, dst_type, dst_id
            ON CONFLICT (target_product_id, canonical_edge_type, dst_node_id, window_type)
            DO UPDATE SET
                review_cnt = EXCLUDED.review_cnt,
                pos_cnt = EXCLUDED.pos_cnt,
                neg_cnt = EXCLUDED.neg_cnt,
                neu_cnt = EXCLUDED.neu_cnt,
                support_count = EXCLUDED.support_count,
                score = EXCLUDED.score,
                last_seen_at = EXCLUDED.last_seen_at,
                updated_at = EXCLUDED.updated_at,
                distinct_review_count = EXCLUDED.distinct_review_count,
                avg_confidence = EXCLUDED.avg_confidence,
                synthetic_ratio = EXCLUDED.synthetic_ratio,
                is_active = true
            RETURNING 1
        """, product_list, window_type)
        count += len(rows)

    # NOTE: `is_promoted` and `corpus_weight` are NOT computed by the SQL
    # path above. After `batch_aggregate_product_signals_sql` completes, a
    # post-hoc Python step must:
    #   - call `is_corpus_promoted()` from aggregate_product_signals.py to
    #     set `is_promoted` (window-aware distinct_review_count threshold).
    #   - compute `corpus_weight = distinct_review_count × avg_confidence ×
    #     recency_factor(window)` for each window.
    # P3-6/P3-7 corpus quality metadata (distinct_review_count, avg_confidence,
    # synthetic_ratio) are now populated by SQL, so the Python step no longer
    # needs to re-read wrapped_signal — it only reads back the agg row.
    return count


async def upsert_agg_user_preference(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO agg_user_preference (user_id, preference_edge_type,
            dst_node_type, dst_node_id, weight, confidence, source_mix, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (user_id, preference_edge_type, dst_node_id) DO UPDATE SET
            weight=EXCLUDED.weight,
            confidence=EXCLUDED.confidence,
            source_mix=EXCLUDED.source_mix,
            updated_at=EXCLUDED.updated_at,
            -- P3-8: re-upsert reactivates a previously soft-deleted row
            is_active=true
    """,
        row["user_id"], row["preference_edge_type"],
        row.get("dst_node_type", ""), row["dst_node_id"],
        row.get("weight", 1.0),
        row.get("confidence", 0.0),
        # source_mix is jsonb: asyncpg cannot bind a dict directly.
        json.dumps(row.get("source_mix")) if row.get("source_mix") is not None else None,
        uow.as_of_ts,
    )


async def upsert_serving_product_profile(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO serving_product_profile (product_id, source_product_id,
            source_channel, source_key_type, brand_id, brand_name, category_id,
            category_name, country_of_origin, price, price_band,
            variant_family_id, representative_product_name,
            main_benefit_ids, ingredient_ids,
            brand_concept_ids, category_concept_ids, ingredient_concept_ids,
            main_benefit_concept_ids,
            top_bee_attr_ids, top_keyword_ids, top_context_ids,
            top_concern_pos_ids, top_concern_neg_ids, top_tool_ids,
            top_comparison_product_ids, top_coused_product_ids,
            last_signal_at, review_count_30d, review_count_90d, review_count_all,
            signal_support_count_all,
            source_review_count_6m, source_review_score_count_6m,
            source_avg_rating_6m, source_review_min_date_6m,
            source_review_max_date_6m, source_review_count_all,
            source_review_score_count_all, source_avg_rating_all,
            source_review_min_date_all, source_review_max_date_all,
            source_review_stats_source, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43,$44)
        ON CONFLICT (product_id) DO UPDATE SET
            source_product_id=EXCLUDED.source_product_id,
            source_channel=EXCLUDED.source_channel,
            source_key_type=EXCLUDED.source_key_type,
            brand_id=EXCLUDED.brand_id,
            brand_name=EXCLUDED.brand_name,
            category_id=EXCLUDED.category_id,
            category_name=EXCLUDED.category_name,
            country_of_origin=EXCLUDED.country_of_origin,
            price=EXCLUDED.price,
            price_band=EXCLUDED.price_band,
            variant_family_id=EXCLUDED.variant_family_id,
            representative_product_name=EXCLUDED.representative_product_name,
            main_benefit_ids=EXCLUDED.main_benefit_ids,
            ingredient_ids=EXCLUDED.ingredient_ids,
            top_bee_attr_ids=EXCLUDED.top_bee_attr_ids,
            top_keyword_ids=EXCLUDED.top_keyword_ids,
            top_context_ids=EXCLUDED.top_context_ids,
            top_concern_pos_ids=EXCLUDED.top_concern_pos_ids,
            top_concern_neg_ids=EXCLUDED.top_concern_neg_ids,
            top_tool_ids=EXCLUDED.top_tool_ids,
            top_comparison_product_ids=EXCLUDED.top_comparison_product_ids,
            top_coused_product_ids=EXCLUDED.top_coused_product_ids,
            brand_concept_ids=EXCLUDED.brand_concept_ids,
            category_concept_ids=EXCLUDED.category_concept_ids,
            ingredient_concept_ids=EXCLUDED.ingredient_concept_ids,
            main_benefit_concept_ids=EXCLUDED.main_benefit_concept_ids,
            last_signal_at=EXCLUDED.last_signal_at,
            review_count_30d=EXCLUDED.review_count_30d,
            review_count_90d=EXCLUDED.review_count_90d,
            review_count_all=EXCLUDED.review_count_all,
            signal_support_count_all=EXCLUDED.signal_support_count_all,
            source_review_count_6m=EXCLUDED.source_review_count_6m,
            source_review_score_count_6m=EXCLUDED.source_review_score_count_6m,
            source_avg_rating_6m=EXCLUDED.source_avg_rating_6m,
            source_review_min_date_6m=EXCLUDED.source_review_min_date_6m,
            source_review_max_date_6m=EXCLUDED.source_review_max_date_6m,
            source_review_count_all=EXCLUDED.source_review_count_all,
            source_review_score_count_all=EXCLUDED.source_review_score_count_all,
            source_avg_rating_all=EXCLUDED.source_avg_rating_all,
            source_review_min_date_all=EXCLUDED.source_review_min_date_all,
            source_review_max_date_all=EXCLUDED.source_review_max_date_all,
            source_review_stats_source=EXCLUDED.source_review_stats_source,
            updated_at=EXCLUDED.updated_at
    """,
        row["product_id"], row.get("source_product_id") or row["product_id"],
        row.get("source_channel"), row.get("source_key_type"),
        row.get("brand_id"), row.get("brand_name"),
        row.get("category_id"), row.get("category_name"),
        row.get("country_of_origin"), row.get("price"), row.get("price_band"),
        row.get("variant_family_id"), row.get("representative_product_name"),
        row.get("main_benefit_ids", []), row.get("ingredient_ids", []),
        json.dumps(row.get("brand_concept_ids", [])),
        json.dumps(row.get("category_concept_ids", [])),
        json.dumps(row.get("ingredient_concept_ids", [])),
        json.dumps(row.get("main_benefit_concept_ids", [])),
        json.dumps(row.get("top_bee_attr_ids", [])),
        json.dumps(row.get("top_keyword_ids", [])),
        json.dumps(row.get("top_context_ids", [])),
        json.dumps(row.get("top_concern_pos_ids", [])),
        json.dumps(row.get("top_concern_neg_ids", [])),
        json.dumps(row.get("top_tool_ids", [])),
        json.dumps(row.get("top_comparison_product_ids", [])),
        json.dumps(row.get("top_coused_product_ids", [])),
        row.get("last_signal_at"), row.get("review_count_30d", 0),
        row.get("review_count_90d", 0), row.get("review_count_all", 0),
        row.get("signal_support_count_all", 0),
        row.get("source_review_count_6m"),
        row.get("source_review_score_count_6m"),
        row.get("source_avg_rating_6m"),
        row.get("source_review_min_date_6m"),
        row.get("source_review_max_date_6m"),
        row.get("source_review_count_all"),
        row.get("source_review_score_count_all"),
        row.get("source_avg_rating_all"),
        row.get("source_review_min_date_all"),
        row.get("source_review_max_date_all"),
        row.get("source_review_stats_source"),
        uow.as_of_ts,
    )


async def upsert_serving_user_profile(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO serving_user_profile (user_id, age_band, gender, skin_type, skin_tone,
            preferred_brand_ids, preferred_category_ids, preferred_ingredient_ids,
            avoided_ingredient_ids, concern_ids, goal_ids,
            preferred_bee_attr_ids, preferred_keyword_ids, preferred_context_ids,
            recent_purchase_brand_ids, repurchase_brand_ids, repurchase_category_ids,
            owned_product_ids, owned_family_ids, repurchased_family_ids,
            updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
        ON CONFLICT (user_id) DO UPDATE SET
            age_band=EXCLUDED.age_band,
            gender=EXCLUDED.gender,
            skin_type=EXCLUDED.skin_type,
            skin_tone=EXCLUDED.skin_tone,
            preferred_brand_ids=EXCLUDED.preferred_brand_ids,
            preferred_category_ids=EXCLUDED.preferred_category_ids,
            preferred_ingredient_ids=EXCLUDED.preferred_ingredient_ids,
            avoided_ingredient_ids=EXCLUDED.avoided_ingredient_ids,
            concern_ids=EXCLUDED.concern_ids, goal_ids=EXCLUDED.goal_ids,
            preferred_bee_attr_ids=EXCLUDED.preferred_bee_attr_ids,
            preferred_keyword_ids=EXCLUDED.preferred_keyword_ids,
            preferred_context_ids=EXCLUDED.preferred_context_ids,
            recent_purchase_brand_ids=EXCLUDED.recent_purchase_brand_ids,
            repurchase_brand_ids=EXCLUDED.repurchase_brand_ids,
            repurchase_category_ids=EXCLUDED.repurchase_category_ids,
            owned_product_ids=EXCLUDED.owned_product_ids,
            owned_family_ids=EXCLUDED.owned_family_ids,
            repurchased_family_ids=EXCLUDED.repurchased_family_ids,
            updated_at=EXCLUDED.updated_at
    """,
        row["user_id"], row.get("age_band"), row.get("gender"),
        row.get("skin_type"), row.get("skin_tone"),
        json.dumps(row.get("preferred_brand_ids", [])),
        json.dumps(row.get("preferred_category_ids", [])),
        json.dumps(row.get("preferred_ingredient_ids", [])),
        json.dumps(row.get("avoided_ingredient_ids", [])),
        json.dumps(row.get("concern_ids", [])),
        json.dumps(row.get("goal_ids", [])),
        json.dumps(row.get("preferred_bee_attr_ids", [])),
        json.dumps(row.get("preferred_keyword_ids", [])),
        json.dumps(row.get("preferred_context_ids", [])),
        json.dumps(row.get("recent_purchase_brand_ids", [])),
        json.dumps(row.get("repurchase_brand_ids", [])),
        json.dumps(row.get("repurchase_category_ids", [])),
        json.dumps(row.get("owned_product_ids", [])),
        json.dumps(row.get("owned_family_ids", [])),
        json.dumps(row.get("repurchased_family_ids", [])),
        uow.as_of_ts,
    )


async def mark_stale_agg_signals_inactive(
    uow: UnitOfWork,
    threshold_days: int = 90,
    include_ids: bool = False,
) -> dict[str, Any]:
    """P3-8: soft-delete aggregate rows whose `last_seen_at` is older than the
    freshness window.

    Sets `is_active=false` on `agg_product_signal` rows with
    `last_seen_at < now() - threshold_days`. Re-upserts later flip the flag
    back to true via the EXCLUDED clause, so this is reversible.

    `agg_user_preference` has no `last_seen_at` column, so cleanup is gated
    on `updated_at` instead (rows whose last refresh predates the threshold).

    Returns `{"product_signals": n, "user_preferences": m}` row counts.
    When `include_ids=True`, also returns unique `product_ids` and `user_ids`
    affected by the cleanup.
    """
    if threshold_days <= 0:
        raise ValueError(f"threshold_days must be > 0, got {threshold_days}")

    product_signals_q = """
        UPDATE agg_product_signal
        SET is_active = false
        WHERE is_active = true
          AND last_seen_at IS NOT NULL
          AND last_seen_at < now() - ($1::int * interval '1 day')
    """
    user_preferences_q = """
        UPDATE agg_user_preference
        SET is_active = false
        WHERE is_active = true
          AND updated_at < now() - ($1::int * interval '1 day')
    """
    # asyncpg returns "UPDATE <n>" status strings; parse the trailing count.
    def _count(status: str | None) -> int:
        if not status:
            return 0
        parts = status.strip().split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0

    def _unique(values: list[Any]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            if value is None:
                continue
            item = str(value)
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    if include_ids:
        ps_rows = await uow.fetch(
            product_signals_q + "\n        RETURNING target_product_id",
            threshold_days,
        )
        up_rows = await uow.fetch(
            user_preferences_q + "\n        RETURNING user_id",
            threshold_days,
        )
        return {
            "product_signals": len(ps_rows),
            "user_preferences": len(up_rows),
            "product_ids": _unique([row["target_product_id"] for row in ps_rows]),
            "user_ids": _unique([row["user_id"] for row in up_rows]),
        }

    ps = await uow.execute(product_signals_q, threshold_days)
    up = await uow.execute(user_preferences_q, threshold_days)
    return {
        "product_signals": _count(ps),
        "user_preferences": _count(up),
    }


async def sql_prefilter_candidates(
    uow: UnitOfWork,
    avoided_ingredient_ids: list[str],
    preferred_concept_ids: list[str],
    max_candidates: int = 200,
) -> list[str]:
    """SQL-first candidate prefilter.

    Hard filter (ALWAYS applied, regardless of preferred_concept_ids):
      Exclude products whose ingredients overlap with avoided_ingredient_ids.
      Checks BOTH raw `ingredient_ids` (TEXT[]) and `ingredient_concept_ids`
      (JSONB array of concept IRIs) so the avoided list can be in either domain.

    Positive overlap (applied only when preferred_concept_ids is non-empty):
      Require at least one match across brand/category/ingredient/main_benefit
      concept_ids.

    P0-6: previously the avoided filter was (a) skipped when preferred was empty,
    and (b) only checked raw ingredient_ids — both bugs caused SQL/Python
    divergence and "preferred-empty" users had no avoided protection.
    """
    avoided_list = avoided_ingredient_ids or []

    # base WHERE clause: avoided exclusion across both ID domains.
    # NULL or '[]' columns produce zero rows in unnest/jsonb_array_elements_text,
    # so NOT EXISTS is true and the product passes.
    base_where_avoid = """
        NOT EXISTS (
            SELECT 1 FROM unnest(spp.ingredient_ids) AS ing
            WHERE ing = ANY($1::text[])
        )
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(spp.ingredient_concept_ids::jsonb) AS ing_c
            WHERE ing_c = ANY($1::text[])
        )
    """

    if not preferred_concept_ids:
        rows = await uow.fetch(
            f"""
            SELECT DISTINCT spp.product_id
            FROM serving_product_profile spp
            WHERE {base_where_avoid}
            LIMIT $2
            """,
            avoided_list, max_candidates,
        )
        return [r["product_id"] for r in rows]

    rows = await uow.fetch(
        f"""
        SELECT DISTINCT spp.product_id
        FROM serving_product_profile spp
        WHERE {base_where_avoid}
        AND (
            EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.brand_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.category_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.ingredient_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.main_benefit_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
        )
        LIMIT $3
        """,
        avoided_list, preferred_concept_ids, max_candidates,
    )
    return [r["product_id"] for r in rows]
