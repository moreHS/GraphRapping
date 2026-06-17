-- =============================================================================
-- Operational State Tables
-- =============================================================================

create table if not exists schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now()
);

create table if not exists pipeline_run (
    run_id bigserial primary key,
    run_type text not null,                -- FULL|INCREMENTAL
    started_at timestamptz not null,
    completed_at timestamptz,
    status text not null default 'RUNNING', -- RUNNING|COMPLETED|FAILED
    watermark_ts timestamptz,              -- last processed updated_at
    watermark_rid text,                    -- last processed review_id (total order)
    review_count int default 0,
    signal_count int default 0,
    quarantine_count int default 0,
    error_message text
);

-- Wave 5.3: track which process holds the pipeline advisory lock for a given run.
-- nullable so existing rows from pre-5.3 runs remain valid; populated by the
-- entrypoint wrappers (run_full_load_to_db / run_incremental_to_db) inside
-- the lock-acquired critical section.
alter table pipeline_run add column if not exists lock_holder_pid integer;

create table if not exists reranker_contribution_log (
    log_id bigserial primary key,
    run_id bigint,
    user_id text,
    product_id text,
    original_rank int,
    final_rank int,
    diversity_bonus real,
    contribution_json jsonb,
    created_at timestamptz not null default now()
);
