-- =============================================================================
-- GraphRapping — Consumer Contract Queries (Wave 5.5)
--
-- Standard read patterns for downstream consumers (e.g. AmoreSimulation).
-- All queries assume the consumer holds a read-only role per
-- docs/architecture/db_consumer_contract.md §1.2.
--
-- Conventions:
--   - $1, $2, ... are positional placeholders (asyncpg-compatible).
--   - Standard reads filter on master is_active=true AND mart is_active=true
--     AND (when applicable) is_promoted=true per contract §2, §7.
--   - "Expected row count" notes are based on the 2026-06-17 local
--     source-grounded snapshot (517 active products, 50 active users,
--     2801 signals, kg_off).
--   - Column names follow the canonical DDL (sql/ddl_signal.sql,
--     sql/ddl_mart.sql) — NOT the in-memory dataclass field names. The two
--     diverge in places (e.g. wrapped_signal.edge_type ↔ agg_product_signal.
--     canonical_edge_type; agg_product_signal.pos_cnt vs in-memory positive_count).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Section 1: Readiness checks
--   Run these BEFORE issuing reads. If any returns 0 or NULL, the DB is not
--   ready for consumer reads and downstream behavior is undefined.
-- -----------------------------------------------------------------------------

-- 1.1 — Schema versions present. Consumer should fail-fast if any of the 8
--       Wave 4 minimum versions is missing.
--       Params: none. Expected: 8+ rows when DB is fully migrated.
SELECT version, applied_at
FROM schema_migrations
ORDER BY version;


-- 1.2 — Active master counts. Both must be > 0 for any useful read.
--       Params: none. Expected (2026-06-17 local): active_products=517, active_users=50.
SELECT
  (SELECT COUNT(*) FROM product_master WHERE is_active = true) AS active_products,
  (SELECT COUNT(*) FROM user_master    WHERE is_active = true) AS active_users;


-- 1.3 — Promoted-signal count per window. Use to confirm the window the
--       consumer plans to read has enough volume.
--       Params: none. Expected (v260605, 'all'): non-zero across edge types.
SELECT window_type, COUNT(*) AS promoted_count
FROM agg_product_signal
WHERE is_active = true AND is_promoted = true
GROUP BY window_type
ORDER BY window_type;


-- 1.4 — Provenance coverage sanity. Every wrapped_signal MUST have at least
--       one signal_evidence row. Consumers using signal_evidence as the
--       provenance source of truth (per contract §5) should see equality.
--       Params: none. Expected: same count both sides.
SELECT
  (SELECT COUNT(*) FROM wrapped_signal)  AS total_signals,
  (SELECT COUNT(*) FROM wrapped_signal s
     WHERE EXISTS (SELECT 1 FROM signal_evidence e
                   WHERE e.signal_id = s.signal_id)) AS signals_with_evidence;


-- 1.5 — Review-summary sidecar freshness. This is optional for graph-only
--       consumers, but required for final product-profile consumers that need
--       ES review-summary text. Expected (2026-06-17 local after sidecar load):
--       one latest manifest row, clean_lookup_product_count near active_products
--       minus SOURCE_KEY_COLLISION rows.
SELECT
  manifest_id,
  created_at,
  long_alias,
  short_alias,
  product_count,
  clean_lookup_product_count,
  fetched_long_docs,
  fetched_short_docs,
  matched,
  exact_category,
  source_unique,
  product_id_unique,
  ambiguous_skipped,
  not_found,
  collision_excluded
FROM review_summary_manifest
ORDER BY manifest_id DESC
LIMIT 1;


-- -----------------------------------------------------------------------------
-- Section 2: Standard reads — products
-- -----------------------------------------------------------------------------

-- 2.1 — Active products with their serving profile.
--       serving_product_profile is window-agnostic (carries the full
--       top-K per edge type pre-aggregated by `build_serving_views`);
--       no window param needed.
--       Filters both product_master.is_active AND serving_product_profile.is_active.
--       Params: none. Expected (2026-06-17 local): up to 517 rows.
SELECT
  pm.product_id,
  spp.source_product_id,
  spp.source_channel,
  spp.source_key_type,
  pm.product_name,
  spp.representative_product_name,
  pm.brand_id,
  pm.brand_name,
  pm.category_id,
  pm.category_name,
  pm.price,
  pm.source_truth_source,
  pm.source_truth_quality,
  pm.source_truth_updated_at,
  spp.source_review_count_6m,
  spp.source_review_score_count_6m,
  spp.source_avg_rating_6m,
  spp.source_review_count_all,
  spp.source_review_score_count_all,
  spp.source_avg_rating_all,
  spp.source_review_stats_source,
  rss.match_status AS review_summary_match_status,
  rss.review_summary_category,
  rss.normalized_summary AS review_summary,
  rss.candidate_metadata AS review_summary_match_metadata,
  spp.review_count_30d AS graph_review_support_30d,
  spp.review_count_90d AS graph_review_support_90d,
  spp.review_count_all AS graph_review_support_all,
  spp.signal_support_count_all,
  spp.top_bee_attr_ids,
  spp.top_keyword_ids,
  spp.top_concern_pos_ids,
  spp.top_comparison_product_ids,
  spp.last_signal_at
FROM product_master pm
JOIN serving_product_profile spp ON spp.product_id = pm.product_id
LEFT JOIN review_summary_sidecar rss ON rss.product_id = pm.product_id
WHERE pm.is_active = true
  AND spp.is_active = true
ORDER BY spp.source_review_count_6m DESC NULLS LAST,
         spp.review_count_all DESC NULLS LAST,
         pm.product_id;


-- 2.2 — Promoted signals for a specific product + window.
--       Params: $1 = product_id, $2 = window_type ('all' | '90d' | '30d')
--       Expected: one row per (canonical_edge_type, dst_node_id) that
--       cleared the Wave 2.8 promotion gate.
SELECT
  canonical_edge_type,
  dst_node_id,
  distinct_review_count,
  avg_confidence,
  synthetic_ratio,
  pos_cnt AS positive_count,
  neg_cnt AS negative_count,
  neu_cnt AS neutral_count,
  last_seen_at
FROM agg_product_signal
WHERE target_product_id = $1
  AND window_type = $2
  AND is_active = true
  AND is_promoted = true
ORDER BY distinct_review_count DESC, avg_confidence DESC;


-- 2.3 — Promoted signal with provenance facts (joined to canonical_fact).
--       Joins wrapped_signal.edge_type ↔ agg_product_signal.canonical_edge_type
--       and constrains wrapped_signal.window_ts to the same window so 30d/90d
--       reads don't surface evidence from outside the window.
--       Params: $1 = product_id, $2 = window_type
SELECT
  ag.canonical_edge_type,
  ag.dst_node_id,
  ws.signal_id,
  cf.fact_id,
  cf.predicate,
  cf.subject_iri,
  cf.object_iri,
  cf.confidence AS fact_confidence,
  cf.source_modalities
FROM agg_product_signal ag
JOIN wrapped_signal ws
  ON  ws.target_product_id = ag.target_product_id
  AND ws.edge_type         = ag.canonical_edge_type
  AND ws.dst_id            = ag.dst_node_id
  -- Restrict evidence to the same window as the aggregate row. For 'all'
  -- (window_start/end NULL or = epoch), the predicate becomes TRUE.
  AND (ag.window_start IS NULL OR ws.window_ts::date >= ag.window_start)
  AND (ag.window_end   IS NULL OR ws.window_ts::date <= ag.window_end)
JOIN signal_evidence se ON se.signal_id = ws.signal_id
JOIN canonical_fact cf  ON cf.fact_id    = se.fact_id
WHERE ag.target_product_id = $1
  AND ag.window_type = $2
  AND ag.is_active = true
  AND ag.is_promoted = true
ORDER BY ag.distinct_review_count DESC, se.evidence_rank;


-- -----------------------------------------------------------------------------
-- Section 3: Standard reads — users
-- -----------------------------------------------------------------------------

-- 3.1 — Active users with their serving profile.
--       Filters both user_master.is_active AND serving_user_profile.is_active.
--       Params: none. Expected (2026-06-17 local): up to 50 rows.
SELECT
  um.user_id,
  um.age_band,
  um.gender,
  um.skin_type,
  um.skin_tone,
  sup.preferred_brand_ids,
  sup.preferred_category_ids,
  sup.preferred_ingredient_ids,
  sup.avoided_ingredient_ids,
  sup.concern_ids,
  sup.goal_ids,
  sup.recent_purchase_brand_ids,
  sup.owned_product_ids,
  sup.repurchase_brand_ids
FROM user_master um
JOIN serving_user_profile sup ON sup.user_id = um.user_id
WHERE um.is_active = true
  AND sup.is_active = true
ORDER BY um.user_id;


-- 3.2 — Active user preferences with optional confidence threshold.
--       Note: agg_user_preference exposes weight, confidence, source_mix,
--       updated_at. Older "support_count / source_types" fields exist only
--       on the in-memory aggregation rows, not the persisted table.
--       Params: $1 = user_id, $2 = min_confidence (e.g. 0.6); pass 0 to disable.
SELECT
  preference_edge_type,
  dst_node_type,
  dst_node_id,
  weight,
  confidence,
  source_mix,
  updated_at
FROM agg_user_preference
WHERE user_id = $1
  AND is_active = true
  AND COALESCE(confidence, 0) >= COALESCE($2, 0)
ORDER BY weight DESC, dst_node_id;


-- -----------------------------------------------------------------------------
-- Section 4: Window variants
--   Use these by passing window_type. The contract recommends 'all' for
--   stable offline simulation, '90d' for trend surfaces, '30d' for
--   freshness experiments.
-- -----------------------------------------------------------------------------

-- 4.1 — Top-K products by promoted-signal volume in a given window.
--       Promoted-only filter is in WHERE (not HAVING) so latest_signal_at
--       cannot be sourced from an unpromoted row.
--       Params: $1 = window_type, $2 = K (e.g. 50)
SELECT
  ag.target_product_id,
  pm.brand_name,
  pm.category_name,
  COUNT(*)             AS promoted_signal_count,
  MAX(ag.last_seen_at) AS latest_signal_at
FROM agg_product_signal ag
JOIN product_master pm ON pm.product_id = ag.target_product_id
WHERE ag.window_type = $1
  AND ag.is_active   = true
  AND ag.is_promoted = true
  AND pm.is_active   = true
GROUP BY ag.target_product_id, pm.brand_name, pm.category_name
ORDER BY promoted_signal_count DESC, latest_signal_at DESC NULLS LAST
LIMIT $2;


-- 4.2 — Co-mentioned product pairs in a window (COMPARED_WITH / USED_WITH).
--       Use for "users also viewed / used together" surfaces.
--       Params: $1 = window_type, $2 = K
SELECT
  ag.target_product_id AS source_product_id,
  ag.dst_node_id       AS co_product_id,
  ag.canonical_edge_type,
  ag.distinct_review_count,
  ag.avg_confidence
FROM agg_product_signal ag
WHERE ag.window_type = $1
  AND ag.is_active = true
  AND ag.is_promoted = true
  AND ag.canonical_edge_type IN ('COMPARED_WITH_SIGNAL', 'USED_WITH_PRODUCT_SIGNAL')
ORDER BY ag.distinct_review_count DESC
LIMIT $2;


-- -----------------------------------------------------------------------------
-- Section 5: Operational helpers
-- -----------------------------------------------------------------------------

-- 5.1 — Latest completed pipeline_run (for staleness checks).
--       Params: none.
SELECT run_id, run_type, started_at, completed_at, status,
       review_count, signal_count, quarantine_count,
       watermark_ts, watermark_rid, lock_holder_pid
FROM pipeline_run
WHERE status = 'COMPLETED'
ORDER BY completed_at DESC NULLS LAST, run_id DESC
LIMIT 1;


-- 5.2 — Pipeline_run history (last N).
--       Params: $1 = N (e.g. 10)
SELECT run_id, run_type, started_at, completed_at, status,
       review_count, signal_count, quarantine_count
FROM pipeline_run
ORDER BY run_id DESC
LIMIT $1;
