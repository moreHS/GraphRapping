-- =============================================================================
-- Layer 3: Serving / Aggregate Layer
-- Windowed aggregation + table-based mart profiles
-- =============================================================================

-- Product-side aggregate (windowed)
create table if not exists agg_product_signal (
    target_product_id text not null,
    canonical_edge_type text not null,
    dst_node_type text not null,
    dst_node_id text not null,
    window_type text not null,             -- 30d|90d|all
    review_cnt int not null,
    pos_cnt int not null default 0,
    neg_cnt int not null default 0,
    neu_cnt int not null default 0,
    support_count int not null,
    score real not null,
    recent_score real,
    recent_support_count int,
    last_seen_at timestamptz,
    window_start date,
    window_end date,
    evidence_sample jsonb,                 -- top-k [{review_id, fact_id, snippet, score}]
    updated_at timestamptz not null default now(),
    primary key (target_product_id, canonical_edge_type, dst_node_id, window_type)
);

create index if not exists idx_aps_product on agg_product_signal(target_product_id);
create index if not exists idx_aps_edge on agg_product_signal(canonical_edge_type);

-- User-side aggregate
create table if not exists agg_user_preference (
    user_id text not null,
    preference_edge_type text not null,    -- HAS_SKIN_TYPE|PREFERS_BRAND|PREFERS_CATEGORY|
                                           -- PREFERS_INGREDIENT|AVOIDS_INGREDIENT|HAS_CONCERN|
                                           -- WANTS_GOAL|WANTS_EFFECT|PREFERS_CONTEXT|
                                           -- PREFERS_BEE_ATTR|AVOIDS_BEE_ATTR|
                                           -- PREFERS_KEYWORD|AVOIDS_KEYWORD|
                                           -- SEASONAL_PREFERS_BRAND|SEASONAL_PREFERS_CATEGORY|
                                           -- REPURCHASES_PRODUCT_OR_FAMILY
    dst_node_type text not null,
    dst_node_id text not null,
    weight real not null default 1.0,
    confidence real,
    source_mix jsonb,                      -- {purchase: 0.6, chat: 0.4}
    updated_at timestamptz not null default now(),
    primary key (user_id, preference_edge_type, dst_node_id)
);

create index if not exists idx_aup_user on agg_user_preference(user_id);

-- Serving Product Profile (table-based mart — NOT just a view)
create table if not exists serving_product_profile (
    product_id text primary key,
    -- source identity fields (explicit source contract)
    source_product_id text,
    source_channel text,
    source_key_type text,
    -- truth columns (from product_master)
    brand_id text,
    brand_name text,
    category_id text,
    category_name text,
    country_of_origin text,
    price numeric,
    price_band text,
    variant_family_id text,
    representative_product_name text,
    main_benefit_ids text[],
    ingredient_ids text[],
    -- concept IRI fields (for shared concept join)
    brand_concept_ids jsonb,
    category_concept_ids jsonb,
    ingredient_concept_ids jsonb,
    main_benefit_concept_ids jsonb,
    -- signal columns (from agg_product_signal)
    top_bee_attr_ids jsonb,                -- [{id, score, review_cnt}]
    top_keyword_ids jsonb,
    top_context_ids jsonb,
    top_concern_pos_ids jsonb,
    top_concern_neg_ids jsonb,
    top_tool_ids jsonb,
    top_comparison_product_ids jsonb,
    top_coused_product_ids jsonb,
    -- freshness columns
    last_signal_at timestamptz,
    review_count_30d int default 0,
    review_count_90d int default 0,
    review_count_all int default 0,
    -- source review volume/rating fields (raw source stats, not graph support)
    source_review_count_6m int,
    source_review_score_count_6m int,
    source_avg_rating_6m numeric(5, 3),
    source_review_min_date_6m date,
    source_review_max_date_6m date,
    source_review_count_all int,
    source_review_score_count_all int,
    source_avg_rating_all numeric(5, 3),
    source_review_min_date_all date,
    source_review_max_date_all date,
    source_review_stats_source text,
    -- meta
    is_active boolean not null default true,
    updated_at timestamptz not null default now()
);

-- Serving User Profile (table-based mart)
create table if not exists serving_user_profile (
    user_id text primary key,
    -- demographics
    age_band text,
    gender text,
    skin_type text,
    skin_tone text,
    -- preference summaries (from agg_user_preference)
    preferred_brand_ids jsonb,
    preferred_category_ids jsonb,
    preferred_ingredient_ids jsonb,
    avoided_ingredient_ids jsonb,
    concern_ids jsonb,
    goal_ids jsonb,
    preferred_bee_attr_ids jsonb,
    preferred_keyword_ids jsonb,
    preferred_context_ids jsonb,
    recent_purchase_brand_ids jsonb,
    repurchase_brand_ids jsonb,
    repurchase_category_ids jsonb,
    owned_product_ids jsonb,
    owned_family_ids jsonb,
    repurchased_family_ids jsonb,
    -- meta
    is_active boolean not null default true,
    updated_at timestamptz not null default now()
);

-- Review summary sidecar (source ES review-summary text, not graph evidence).
-- This table is keyed by GraphRapping product_id, but retains source identity
-- and raw ES docs so downstream consumers do not lose high-quality source fields.
create table if not exists review_summary_sidecar (
    product_id text primary key references product_master(product_id),
    source_product_id text not null,
    source_channel text,
    source_key_type text,
    review_source text,
    review_channel text,
    review_summary_category text,
    match_status text not null,
    long_doc_id text,
    short_doc_id text,
    long_doc jsonb,
    short_doc jsonb,
    candidate_metadata jsonb,
    normalized_summary jsonb,
    an_date text,
    source text not null default 'es8_summary_review',
    updated_at timestamptz not null default now()
);

create index if not exists idx_review_summary_sidecar_source_identity
    on review_summary_sidecar(source_channel, source_key_type, source_product_id);
create index if not exists idx_review_summary_sidecar_status
    on review_summary_sidecar(match_status);

create table if not exists review_summary_manifest (
    manifest_id bigserial primary key,
    source text not null default 'es8_summary_review',
    long_alias text,
    short_alias text,
    an_date text,
    product_count int not null default 0,
    clean_lookup_product_count int not null default 0,
    fetched_long_docs int not null default 0,
    fetched_short_docs int not null default 0,
    matched int not null default 0,
    exact_category int not null default 0,
    source_unique int not null default 0,
    product_id_unique int not null default 0,
    ambiguous_skipped int not null default 0,
    not_found int not null default 0,
    collision_excluded int not null default 0,
    errors int not null default 0,
    payload jsonb,
    created_at timestamptz not null default now()
);

ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS variant_family_id text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS representative_product_name text;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS recent_purchase_brand_ids jsonb;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS repurchase_brand_ids jsonb;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS repurchase_category_ids jsonb;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS owned_product_ids jsonb;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS owned_family_ids jsonb;
ALTER TABLE serving_user_profile ADD COLUMN IF NOT EXISTS repurchased_family_ids jsonb;

-- Corpus promotion columns (vNext)
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS distinct_review_count int NOT NULL DEFAULT 0;
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS avg_confidence real NOT NULL DEFAULT 0.0;
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS synthetic_ratio real NOT NULL DEFAULT 0.0;
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS corpus_weight real NOT NULL DEFAULT 0.0;
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS is_promoted boolean NOT NULL DEFAULT false;

-- P3-7 (Wave 2.9): signal_support_count_all is the sum-of-review_cnt across
-- (edge, dst) signal rows for the product. Distinct from `review_count_all`
-- which now stores product-level distinct review_id count.
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS signal_support_count_all int NOT NULL DEFAULT 0;

-- Source-grounded serving contract (2026-06-15): keep source stats explicit
-- and separate from graph support review_count_* fields.
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_product_id text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_channel text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_key_type text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_count_6m int;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_score_count_6m int;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_avg_rating_6m numeric(5, 3);
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_min_date_6m date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_max_date_6m date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_count_all int;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_score_count_all int;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_avg_rating_all numeric(5, 3);
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_min_date_all date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_max_date_all date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_stats_source text;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_count_6m DROP NOT NULL;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_count_6m DROP DEFAULT;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_score_count_6m DROP NOT NULL;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_score_count_6m DROP DEFAULT;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_count_all DROP NOT NULL;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_count_all DROP DEFAULT;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_score_count_all DROP NOT NULL;
ALTER TABLE serving_product_profile ALTER COLUMN source_review_score_count_all DROP DEFAULT;

-- P3-8 (Wave 2.10): soft-delete marker for aggregate rows whose last_seen_at
-- has fallen outside the freshness window. Re-upsert (EXCLUDED) reactivates.
ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE agg_user_preference ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

-- P3-8 (Wave 3.8): partial indexes targeted at the cleanup query shape —
-- `WHERE is_active = true AND <ts> < cutoff`. Skip dead rows entirely.
CREATE INDEX IF NOT EXISTS idx_aps_active_lastseen
    ON agg_product_signal (last_seen_at) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_aup_active_updated
    ON agg_user_preference (updated_at) WHERE is_active = true;
