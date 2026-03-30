-- =============================================================================
-- Common Concept Layer: Shared concept registry for user/product join
-- =============================================================================

create table if not exists concept_registry (
    concept_id text primary key,
    concept_type text not null,            -- Brand|Category|Ingredient|BEEAttr|Keyword|TemporalContext|
                                           -- Frequency|Duration|AbsoluteDate|Concern|Goal|Tool|
                                           -- SkinType|SkinTone|Fragrance|UserSegment|PriceBand|Country|AgeBand
    canonical_name text not null,
    canonical_name_norm text not null,     -- lowercase, whitespace-stripped
    source_system text,                    -- product_db|review_extraction|user_chat|manual
    source_key text,
    lang text default 'ko',               -- ko|en|romanized
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_concept_type on concept_registry(concept_type);
create index if not exists idx_concept_norm on concept_registry(canonical_name_norm);

-- Concept Alias (multilingual / romanization)
create table if not exists concept_alias (
    alias_id bigserial primary key,
    concept_id text not null references concept_registry(concept_id),
    alias_text text not null,
    alias_norm text not null,
    lang text,                             -- ko|en|romanized
    source text,
    created_at timestamptz not null default now()
);

create index if not exists idx_alias_norm on concept_alias(alias_norm);
create index if not exists idx_alias_concept on concept_alias(concept_id);

-- Entity ↔ Concept Link
create table if not exists entity_concept_link (
    entity_iri text not null,
    concept_id text not null references concept_registry(concept_id),
    link_type text not null,               -- HAS_BRAND|IN_CATEGORY|HAS_INGREDIENT|HAS_BEE_ATTR|HAS_CONCERN|...
    confidence real,
    source text,                           -- product_db|review_extraction|user_chat
    primary key (entity_iri, concept_id, link_type)
);

create index if not exists idx_ecl_entity on entity_concept_link(entity_iri);
create index if not exists idx_ecl_concept on entity_concept_link(concept_id);
