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
