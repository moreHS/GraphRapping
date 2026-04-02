-- =============================================================================
-- Layer 2.5: Wrapped Signal + Signal Evidence
-- Projection registry output → aggregate input
-- =============================================================================

create table if not exists wrapped_signal (
    signal_id text primary key,
    review_id text,
    user_id text,
    target_product_id text,
    source_fact_ids text[] not null default '{}',  -- CACHE-ONLY: provenance SoT = signal_evidence table
    signal_family text not null,           -- BEE_ATTR|BEE_KEYWORD|CONTEXT|TOOL|
                                           -- CONCERN_POS|CONCERN_NEG|COMPARISON|COUSED_PRODUCT|
                                           -- SEGMENT|CATALOG_VALIDATION
    edge_type text not null,               -- HAS_BEE_ATTR_SIGNAL|HAS_BEE_KEYWORD_SIGNAL|
                                           -- USED_IN_CONTEXT_SIGNAL|USED_WITH_TOOL_SIGNAL|
                                           -- USED_WITH_PRODUCT_SIGNAL|ADDRESSES_CONCERN_SIGNAL|
                                           -- MAY_CAUSE_CONCERN_SIGNAL|COMPARED_WITH_SIGNAL|
                                           -- TARGETED_AT_SEGMENT_SIGNAL|RECOMMENDED_TO_SEGMENT_SIGNAL|
                                           -- CATALOG_VALIDATION_SIGNAL
    dst_type text not null,                -- BEEAttr|Keyword|TemporalContext|Tool|Concern|Product|UserSegment
    dst_id text not null,
    dst_ref_kind text not null,            -- ENTITY|CONCEPT|TEXT|NUMBER|JSON
    bee_attr_id text,                      -- BEE family: linked attr
    keyword_id text,                       -- BEE family: linked keyword
    polarity text,                         -- POS|NEG|NEU|MIXED|null
    negated boolean,
    intensity real,
    weight real not null default 1.0,
    registry_version text not null,
    window_ts timestamptz,                 -- event_time basis for windowed aggregation
    created_at timestamptz not null default now()
);

create index if not exists idx_ws_product on wrapped_signal(target_product_id);
create index if not exists idx_ws_family on wrapped_signal(signal_family);
create index if not exists idx_ws_edge_type on wrapped_signal(edge_type);
create index if not exists idx_ws_product_edge on wrapped_signal(target_product_id, edge_type);
create index if not exists idx_ws_review on wrapped_signal(review_id);

-- Signal Evidence (signal → contributing facts for explanation)
create table if not exists signal_evidence (
    signal_id text not null references wrapped_signal(signal_id),
    fact_id text not null references canonical_fact(fact_id),
    evidence_rank int not null,
    contribution real,
    primary key (signal_id, fact_id, evidence_rank)
);
