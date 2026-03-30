-- =============================================================================
-- Composite Indexes for hot query paths
-- =============================================================================

-- Raw layer
create index if not exists idx_ner_review on ner_raw(review_id);
create index if not exists idx_bee_review on bee_raw(review_id);
create index if not exists idx_rel_review on rel_raw(review_id);
create index if not exists idx_rcl_status on review_catalog_link(match_status);
create index if not exists idx_rcl_product on review_catalog_link(matched_product_id);
create index if not exists idx_purchase_user on purchase_event_raw(user_id);
create index if not exists idx_purchase_product on purchase_event_raw(product_id);

-- Canonical layer
create index if not exists idx_cf_subj_type on canonical_fact(subject_type);
create index if not exists idx_cf_obj_type on canonical_fact(object_type);
create index if not exists idx_cf_pred_subj on canonical_fact(predicate, subject_iri);
create index if not exists idx_cf_pred_obj on canonical_fact(predicate, object_iri);
create index if not exists idx_fp_review on fact_provenance(review_id);
create index if not exists idx_fq_fact on fact_qualifier(fact_id);

-- Signal layer
create index if not exists idx_ws_dst on wrapped_signal(dst_id);
create index if not exists idx_ws_window on wrapped_signal(window_ts);

-- Mart layer
create index if not exists idx_aps_product_window on agg_product_signal(target_product_id, window_type);
create index if not exists idx_aup_edge on agg_user_preference(preference_edge_type);
create index if not exists idx_aup_dst on agg_user_preference(dst_node_id);

-- Quarantine
create index if not exists idx_qpm_status on quarantine_product_match(status);
create index if not exists idx_quk_status on quarantine_unknown_keyword(status);
create index if not exists idx_qprm_status on quarantine_projection_miss(status);
