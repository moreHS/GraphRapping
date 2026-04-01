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
            is_promoted=EXCLUDED.is_promoted
    """,
        row["target_product_id"], row["canonical_edge_type"],
        row["dst_node_type"], row["dst_node_id"], row["window_type"],
        row["review_cnt"], row["pos_cnt"], row["neg_cnt"],
        row.get("neu_cnt", 0), row["support_count"], row["score"],
        row.get("recent_score"), row.get("recent_support_count"),
        row.get("last_seen_at"), row.get("window_start"), row.get("window_end"),
        row.get("evidence_sample"), uow.as_of_ts,
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

        rows = await uow.fetch(f"""
            INSERT INTO agg_product_signal
                (target_product_id, canonical_edge_type, dst_node_type, dst_node_id,
                 window_type, review_cnt, pos_cnt, neg_cnt, neu_cnt, support_count, score,
                 updated_at)
            SELECT
                target_product_id,
                edge_type,
                dst_type,
                dst_id,
                $2::text,
                COUNT(DISTINCT review_id),
                COUNT(*) FILTER (WHERE polarity = 'POS'),
                COUNT(*) FILTER (WHERE polarity = 'NEG'),
                COUNT(*) FILTER (WHERE polarity IS NULL OR polarity NOT IN ('POS','NEG')),
                COUNT(*),
                CASE WHEN COUNT(*) > 0
                    THEN (COUNT(*) FILTER (WHERE polarity='POS') - COUNT(*) FILTER (WHERE polarity='NEG'))::real / COUNT(*)
                    ELSE 0 END,
                now()
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
                updated_at = EXCLUDED.updated_at
            RETURNING 1
        """, product_list, window_type)
        count += len(rows)

    # NOTE: is_promoted is NOT computed by the SQL path above.
    # After batch_aggregate_product_signals_sql completes, a post-hoc Python
    # step must call is_corpus_promoted() from aggregate_product_signals.py
    # to set distinct_review_count, avg_confidence, synthetic_ratio,
    # corpus_weight, and is_promoted on each aggregated row.
    return count


async def upsert_agg_user_preference(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO agg_user_preference (user_id, preference_edge_type,
            dst_node_type, dst_node_id, weight, source_mix, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (user_id, preference_edge_type, dst_node_id) DO UPDATE SET
            weight=EXCLUDED.weight, source_mix=EXCLUDED.source_mix,
            updated_at=EXCLUDED.updated_at
    """,
        row["user_id"], row["preference_edge_type"],
        row.get("dst_node_type", ""), row["dst_node_id"],
        row.get("weight", 1.0), row.get("source_mix"), uow.as_of_ts,
    )


async def upsert_serving_product_profile(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO serving_product_profile (product_id, brand_id, brand_name,
            category_id, category_name, country_of_origin, price, price_band,
            main_benefit_ids, ingredient_ids,
            brand_concept_ids, category_concept_ids, ingredient_concept_ids,
            main_benefit_concept_ids,
            top_bee_attr_ids, top_keyword_ids, top_context_ids,
            top_concern_pos_ids, top_concern_neg_ids, top_tool_ids,
            top_comparison_product_ids, top_coused_product_ids,
            last_signal_at, review_count_30d, review_count_90d, review_count_all,
            updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27)
        ON CONFLICT (product_id) DO UPDATE SET
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
            updated_at=EXCLUDED.updated_at
    """,
        row["product_id"], row.get("brand_id"), row.get("brand_name"),
        row.get("category_id"), row.get("category_name"),
        row.get("country_of_origin"), row.get("price"), row.get("price_band"),
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
        uow.as_of_ts,
    )


async def upsert_serving_user_profile(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO serving_user_profile (user_id, age_band, gender, skin_type, skin_tone,
            preferred_brand_ids, preferred_category_ids, preferred_ingredient_ids,
            avoided_ingredient_ids, concern_ids, goal_ids,
            preferred_bee_attr_ids, preferred_keyword_ids, preferred_context_ids,
            updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (user_id) DO UPDATE SET
            preferred_brand_ids=EXCLUDED.preferred_brand_ids,
            preferred_category_ids=EXCLUDED.preferred_category_ids,
            preferred_ingredient_ids=EXCLUDED.preferred_ingredient_ids,
            avoided_ingredient_ids=EXCLUDED.avoided_ingredient_ids,
            concern_ids=EXCLUDED.concern_ids, goal_ids=EXCLUDED.goal_ids,
            preferred_bee_attr_ids=EXCLUDED.preferred_bee_attr_ids,
            preferred_keyword_ids=EXCLUDED.preferred_keyword_ids,
            preferred_context_ids=EXCLUDED.preferred_context_ids,
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
        uow.as_of_ts,
    )


async def sql_prefilter_candidates(
    uow: UnitOfWork,
    avoided_ingredient_ids: list[str],
    preferred_concept_ids: list[str],
    max_candidates: int = 200,
) -> list[str]:
    """SQL-first candidate prefilter: exclude avoided ingredients, require concept overlap.

    Returns product_ids that pass hard filters and have at least one concept overlap.
    Downstream Python overlap scoring runs only on this reduced set.
    """
    if not preferred_concept_ids:
        # No preferences → return all non-conflicting products
        rows = await uow.fetch("""
            SELECT product_id FROM serving_product_profile
            LIMIT $1
        """, max_candidates)
        return [r["product_id"] for r in rows]

    rows = await uow.fetch("""
        SELECT DISTINCT spp.product_id
        FROM serving_product_profile spp
        WHERE NOT EXISTS (
            SELECT 1 FROM unnest(spp.ingredient_ids) AS ing
            WHERE ing = ANY($1::text[])
        )
        AND (
            EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.brand_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.category_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.ingredient_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
            OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(spp.main_benefit_concept_ids::jsonb) b WHERE b = ANY($2::text[]))
        )
        LIMIT $3
    """, avoided_ingredient_ids or [], preferred_concept_ids, max_candidates)
    return [r["product_id"] for r in rows]
