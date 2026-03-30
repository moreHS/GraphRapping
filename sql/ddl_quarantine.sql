-- =============================================================================
-- QA: Quarantine Tables (5 types)
-- All mapping failures go to explicit quarantine, never silent drop
-- =============================================================================

-- 1. Product match failures
create table if not exists quarantine_product_match (
    id bigserial primary key,
    review_id text not null,
    source_brand text,
    source_product_name text,
    attempted_match_score real,
    attempted_match_method text,
    reason text,
    raw_data jsonb,
    status text not null default 'PENDING',    -- PENDING|RESOLVED|REJECTED
    resolved_product_id text,
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);

-- 2. Placeholder resolution failures
create table if not exists quarantine_placeholder (
    id bigserial primary key,
    review_id text not null,
    mention_text text not null,
    entity_group text,
    placeholder_type text,
    reason text,
    raw_data jsonb,
    status text not null default 'PENDING',
    resolved_entity_iri text,
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);

-- 3. Unknown keyword (surface form not in dictionary)
create table if not exists quarantine_unknown_keyword (
    id bigserial primary key,
    review_id text,
    surface_text text not null,
    bee_attr_raw text,
    context_text text,
    reason text,
    raw_data jsonb,
    status text not null default 'PENDING',
    resolved_keyword_id text,
    resolved_concept_id text,
    dictionary_version text,
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);

-- 4. Projection registry miss (no mapping for predicate+type combo)
create table if not exists quarantine_projection_miss (
    id bigserial primary key,
    fact_id text,
    review_id text,
    predicate text not null,
    subject_type text,
    object_type text,
    polarity text,
    registry_version text,
    reason text,
    raw_data jsonb,
    status text not null default 'PENDING',
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);

-- 5. Untyped entity (e.g. used_with(X) where X is unknown Tool vs Product)
create table if not exists quarantine_untyped_entity (
    id bigserial primary key,
    review_id text,
    mention_text text not null,
    expected_types text[],                    -- [Tool, Product]
    context_predicate text,
    reason text,
    raw_data jsonb,
    status text not null default 'PENDING',
    resolved_entity_type text,
    resolved_entity_iri text,
    resolved_at timestamptz,
    created_at timestamptz not null default now()
);
