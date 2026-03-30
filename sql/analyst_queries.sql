-- =============================================================================
-- Analyst Queries: Pre-built queries for common analysis patterns
-- =============================================================================

-- 1. Top BEE_ATTR signals by product category
-- Usage: Find which product attributes are most valued in each category
SELECT
    pm.category_name,
    aps.dst_node_id AS bee_attr_id,
    cr.canonical_name AS bee_attr_name,
    SUM(aps.review_cnt) AS total_reviews,
    ROUND(AVG(aps.score)::numeric, 3) AS avg_score
FROM agg_product_signal aps
JOIN product_master pm ON aps.target_product_id = pm.product_id
LEFT JOIN concept_registry cr ON aps.dst_node_id = cr.concept_id
WHERE aps.canonical_edge_type = 'HAS_BEE_ATTR_SIGNAL'
  AND aps.window_type = 'all'
GROUP BY pm.category_name, aps.dst_node_id, cr.canonical_name
ORDER BY pm.category_name, total_reviews DESC;


-- 2. User concern distribution
-- Usage: Understand what skin concerns users report most
SELECT
    aup.dst_node_id AS concern_id,
    cr.canonical_name AS concern_name,
    COUNT(DISTINCT aup.user_id) AS user_count,
    ROUND(AVG(aup.weight)::numeric, 2) AS avg_weight
FROM agg_user_preference aup
LEFT JOIN concept_registry cr ON aup.dst_node_id = cr.concept_id
WHERE aup.preference_edge_type = 'HAS_CONCERN'
GROUP BY aup.dst_node_id, cr.canonical_name
ORDER BY user_count DESC;


-- 3. Product comparison network
-- Usage: Find which products are most compared to each other
SELECT
    aps.target_product_id AS product_a,
    pm_a.product_name AS product_a_name,
    aps.dst_node_id AS product_b,
    pm_b.product_name AS product_b_name,
    aps.review_cnt AS comparison_count,
    aps.score AS sentiment_score
FROM agg_product_signal aps
JOIN product_master pm_a ON aps.target_product_id = pm_a.product_id
LEFT JOIN product_master pm_b ON aps.dst_node_id = pm_b.product_id
WHERE aps.canonical_edge_type = 'COMPARED_WITH_SIGNAL'
  AND aps.window_type = 'all'
ORDER BY aps.review_cnt DESC
LIMIT 50;


-- 4. Quarantine stats by type and status
-- Usage: Monitor pipeline health and identify common failure patterns
SELECT 'product_match' AS quarantine_type, status, COUNT(*) FROM quarantine_product_match GROUP BY status
UNION ALL
SELECT 'placeholder', status, COUNT(*) FROM quarantine_placeholder GROUP BY status
UNION ALL
SELECT 'unknown_keyword', status, COUNT(*) FROM quarantine_unknown_keyword GROUP BY status
UNION ALL
SELECT 'projection_miss', status, COUNT(*) FROM quarantine_projection_miss GROUP BY status
UNION ALL
SELECT 'untyped_entity', status, COUNT(*) FROM quarantine_untyped_entity GROUP BY status
ORDER BY quarantine_type, status;


-- 5. Projection registry coverage report
-- Usage: Find which predicate/type combos are actually observed vs registered
SELECT
    cf.predicate,
    cf.subject_type,
    cf.object_type,
    COUNT(*) AS fact_count,
    BOOL_OR(ws.signal_id IS NOT NULL) AS has_signal
FROM canonical_fact cf
LEFT JOIN wrapped_signal ws ON ws.review_id = cf.review_id
WHERE cf.valid_to IS NULL
GROUP BY cf.predicate, cf.subject_type, cf.object_type
ORDER BY fact_count DESC;


-- 6. Pipeline run history
-- Usage: Monitor incremental pipeline execution
SELECT
    run_id, run_type, started_at, completed_at, status,
    watermark_ts, review_count, signal_count, quarantine_count,
    error_message
FROM pipeline_run
ORDER BY started_at DESC
LIMIT 20;


-- 7. Product freshness: reviews per window
-- Usage: Identify products with recent vs stale signal data
SELECT
    spp.product_id,
    pm.product_name,
    pm.brand_name,
    spp.review_count_30d,
    spp.review_count_90d,
    spp.review_count_all,
    spp.last_signal_at
FROM serving_product_profile spp
JOIN product_master pm ON spp.product_id = pm.product_id
ORDER BY spp.review_count_30d DESC
LIMIT 50;


-- 8. Co-use product network (routine/bundling analysis)
-- Usage: Find products commonly used together
SELECT
    aps.target_product_id,
    pm_a.product_name AS source_product,
    aps.dst_node_id AS coused_product,
    pm_b.product_name AS coused_product_name,
    aps.review_cnt
FROM agg_product_signal aps
JOIN product_master pm_a ON aps.target_product_id = pm_a.product_id
LEFT JOIN product_master pm_b ON aps.dst_node_id = pm_b.product_id
WHERE aps.canonical_edge_type = 'USED_WITH_PRODUCT_SIGNAL'
  AND aps.window_type = 'all'
ORDER BY aps.review_cnt DESC
LIMIT 50;
