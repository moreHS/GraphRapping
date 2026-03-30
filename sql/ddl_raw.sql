-- =============================================================================
-- Layer 0: Master Tables + Layer 1: Raw / Evidence Layer
-- =============================================================================

-- Layer 0: Product Master (source of truth)
create table if not exists product_master (
    product_id text primary key,
    product_name text not null,
    brand_id text,
    brand_name text,
    category_id text,
    category_name text,
    country_of_origin text,
    main_benefits text[],
    price numeric,
    ingredients text[],
    volume text,
    shade text,
    variant_family_id text,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Layer 0: User Master
create table if not exists user_master (
    user_id text primary key,
    age int,
    age_band text,
    gender text,
    skin_type text,
    skin_tone text,
    raw_payload jsonb,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Layer 0: Purchase Events
create table if not exists purchase_event_raw (
    purchase_event_id text primary key,
    user_id text not null,
    product_id text not null,
    purchased_at timestamptz,
    price numeric,
    quantity int default 1,
    channel text,
    raw_payload jsonb,
    created_at timestamptz not null default now()
);

-- Layer 0: User Summary (purchase/chat-based)
create table if not exists user_summary_raw (
    user_id text primary key,
    purchase_summary jsonb,
    repurchase_summary jsonb,
    seasonal_summary jsonb,
    chat_summary jsonb,
    updated_at timestamptz
);

-- =============================================================================
-- Layer 1: Review Raw
-- =============================================================================

create table if not exists review_raw (
    review_id text primary key,
    source text not null,
    source_review_key text,
    source_site text,
    brand_name_raw text,
    product_name_raw text,
    review_text text not null,
    reviewer_proxy_id text,
    identity_stability text not null default 'REVIEW_LOCAL',  -- STABLE|REVIEW_LOCAL
    event_time_utc timestamptz,
    event_time_raw_text text,
    event_tz text,
    event_time_source text not null default 'PROCESSING_TIME',  -- SOURCE_CREATED|COLLECTED_AT|PROCESSING_TIME
    raw_payload jsonb not null,
    review_version int not null default 1,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Review version history (immutable audit ledger)
create table if not exists review_raw_history (
    review_id text not null,
    review_version int not null check (review_version >= 1),
    source text not null,
    source_review_key text,
    source_site text,
    brand_name_raw text,
    product_name_raw text,
    review_text text not null,
    reviewer_proxy_id text,
    identity_stability text not null default 'REVIEW_LOCAL',
    event_time_utc timestamptz,
    event_time_raw_text text,
    event_tz text,
    event_time_source text not null default 'PROCESSING_TIME',
    raw_payload jsonb not null,
    is_active boolean not null default true,
    version_op text not null check (version_op in ('INSERT','UPDATE','TOMBSTONE','REACTIVATE')),
    review_created_at timestamptz not null,
    version_created_at timestamptz not null default now(),
    primary key (review_id, review_version)
);

-- Layer 1: Review → Product Link
create table if not exists review_catalog_link (
    review_id text primary key references review_raw(review_id),
    source_brand text,
    source_product_name text,
    matched_product_id text,           -- NULL if unresolved
    match_status text not null,        -- EXACT|NORM|ALIAS|FUZZY|QUARANTINE
    match_score real,
    match_method text,
    created_at timestamptz not null default now()
);

-- Layer 1: NER Raw
create table if not exists ner_raw (
    ner_row_id bigserial primary key,
    review_id text not null references review_raw(review_id),
    review_version int not null default 1,
    mention_text text not null,
    entity_group text not null,        -- PRD|PER|CAT|BRD|DATE|COL|AGE|VOL|EVN|ING
    start_offset int,
    end_offset int,
    raw_sentiment text,
    is_placeholder boolean not null default false,
    placeholder_type text,             -- REVIEW_TARGET|REVIEWER|PRONOUN
    created_at timestamptz not null default now()
);

-- Layer 1: BEE Raw
create table if not exists bee_raw (
    bee_row_id bigserial primary key,
    review_id text not null references review_raw(review_id),
    review_version int not null default 1,
    phrase_text text not null,
    bee_attr_raw text not null,        -- 39 BEE attribute types
    raw_sentiment text,
    start_offset int,
    end_offset int,
    created_at timestamptz not null default now()
);

-- Layer 1: REL Raw
create table if not exists rel_raw (
    rel_row_id bigserial primary key,
    review_id text not null references review_raw(review_id),
    review_version int not null default 1,
    subj_text text not null,
    subj_group text not null,
    subj_start int,
    subj_end int,
    obj_text text not null,
    obj_group text not null,
    obj_start int,
    obj_end int,
    relation_raw text not null,
    relation_canonical text,           -- 65 canonical or null
    source_type text,                  -- NER-NER|NER-BeE
    created_at timestamptz not null default now()
);

-- Layer 1: Dictionary Candidate Queue
create table if not exists dictionary_candidate_queue (
    candidate_id bigserial primary key,
    source_table text not null,
    source_row_id text not null,
    candidate_type text not null,      -- keyword|bee_attr|tool|concern|segment
    surface_text text not null,
    context_text text,
    status text not null default 'PENDING',  -- PENDING|APPROVED|REJECTED
    resolved_concept_id text,
    dictionary_version text,
    created_at timestamptz not null default now(),
    resolved_at timestamptz
);
