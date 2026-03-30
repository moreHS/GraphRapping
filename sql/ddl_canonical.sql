-- =============================================================================
-- Layer 2: Canonical Fact Layer
-- Preserves all 65 canonical relations without compression
-- =============================================================================

-- Canonical Entity (normalized from raw mentions)
create table if not exists canonical_entity (
    entity_iri text primary key,
    entity_type text not null,             -- Product|ReviewerProxy|Brand|Category|Ingredient|BEEAttr|
                                           -- Keyword|TemporalContext|Frequency|Duration|AbsoluteDate|
                                           -- Concern|Goal|Tool|User|SkinType|SkinTone|Fragrance|
                                           -- UserSegment|OtherProduct
    canonical_name text not null,
    canonical_name_norm text not null,
    source_system text,                    -- product_db|review_extraction|user_chat|manual
    source_key text,
    match_confidence real,
    attrs jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_ce_type on canonical_entity(entity_type);
create index if not exists idx_ce_norm on canonical_entity(canonical_name_norm);

-- Canonical Fact (65 canonical predicates preserved)
create table if not exists canonical_fact (
    fact_id text primary key,
    review_id text,
    subject_iri text not null,
    predicate text not null,               -- 65 canonical relations
    object_iri text,
    object_value_text text,
    object_value_num double precision,
    object_value_json jsonb,
    object_ref_kind text not null,         -- ENTITY|CONCEPT|TEXT|NUMBER|JSON
    subject_type text not null,
    object_type text,
    polarity text,                         -- POS|NEG|NEU|MIXED|null
    confidence real,
    source_modalities text[] not null,     -- array: NER|BEE|REL|FUSED (union on multi-modality)
    extraction_version text,
    registry_version text,
    valid_from timestamptz,
    valid_to timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists idx_cf_subj on canonical_fact(subject_iri);
create index if not exists idx_cf_pred on canonical_fact(predicate);
create index if not exists idx_cf_obj on canonical_fact(object_iri);
create index if not exists idx_cf_review on canonical_fact(review_id);
create index if not exists idx_cf_subj_pred on canonical_fact(subject_iri, predicate);

-- Fact Provenance (raw row → canonical fact link for audit/explanation)
create table if not exists fact_provenance (
    fact_id text not null references canonical_fact(fact_id),
    raw_table text not null,               -- ner_raw|bee_raw|rel_raw
    raw_row_id text not null,
    review_id text,
    snippet text,
    start_offset int,
    end_offset int,
    source_modality text not null,         -- NER|BEE|REL
    evidence_rank int,
    primary key (fact_id, raw_table, raw_row_id)
);

-- Fact Qualifier (structured qualifiers for projection registry)
create table if not exists fact_qualifier (
    qualifier_id bigserial primary key,
    fact_id text not null references canonical_fact(fact_id),
    qualifier_key text not null,
    qualifier_type text not null,           -- context|time|duration|frequency|segment|tool|reason
    qualifier_iri text,
    qualifier_value_text text,
    qualifier_value_num double precision,
    qualifier_value_json jsonb
);

create unique index if not exists uq_fact_qualifier
    on fact_qualifier (fact_id, qualifier_key, coalesce(qualifier_iri, ''), coalesce(qualifier_value_text, ''));
