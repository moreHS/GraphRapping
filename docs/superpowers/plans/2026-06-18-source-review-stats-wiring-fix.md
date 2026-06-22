# Source Review Stats Wiring Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure GraphRapping persists source-grounded 6-month review stats from Snowflake `f_prd_rv_hist` instead of fallback zeros.

**Architecture:** Keep graph support counts separate from source review volume/rating. Fetch or load source review stats as a dedicated snapshot keyed by `product_id + source_channel + source_key_type`, pass it through `FullLoadConfig.source_review_stats_by_product`, and only use catalog `REVIEW_COUNT/REVIEW_SCORE` as all-time fallback with unknown 6-month fields left null.

**Tech Stack:** Python, asyncpg/Postgres, Snowflake SQL builder, pytest, JSON source snapshots.

---

## Failure Evidence

- Local `product_review_stats`: 516 rows, `source_review_count_6m = 0` for all 516, positive for 0.
- Local `serving_product_profile`: 516 non-null `source_review_count_6m`, all 0.
- Product `61289`: DB has `source_review_count_6m=0`, `source_avg_rating_6m=NULL`, but the 2026-06-15 Snowflake handoff measured `review_count_6m=874`, `avg_prd_apal_scr_6m=4.939359`.
- Current source snapshots contain `REVIEW_COUNT/REVIEW_SCORE` only; no `review_count_6m`, `avg_rating_6m`, or 6-month min/max date fields.

## Files

- Modify: `src/loaders/source_review_stats_loader.py`
  - Add JSON snapshot load/save helpers.
  - Support `source_review_*` persistence keys as parser aliases.
- Modify: `src/jobs/run_full_load.py`
  - Preserve configured source stats.
  - Stop fabricating `source_review_count_6m=0` when only catalog all-time stats exist.
- Modify: `src/jobs/run_full_load_db.py`
  - Load optional source stats snapshot path and pass it into `FullLoadConfig`.
- Create: `scripts/fetch_source_review_stats_snapshot.py`
  - Read source identity snapshot.
  - Group product ids by channel.
  - Execute existing Snowflake SQL builder.
  - Write `product_review_stats_snowflake_<date>.json` and latest copy.
- Modify: `tests/test_source_review_stats_loader.py`
- Modify: `tests/test_source_product_id_contract.py`
- Modify: `tests/test_postgres_integration.py`
- Update docs after verification.

## Tasks

### Task 1: Regression Tests

- [x] Add loader tests proving source stats snapshot records with 6-month fields parse to persistence rows.
- [x] Add full-load merge test proving catalog fallback leaves 6-month fields absent/null instead of fabricating zeros.
- [x] Add DB/full-load smoke expectation for positive configured 6-month stats.

### Task 2: Loader And Snapshot Wiring

- [x] Implement snapshot helpers in `source_review_stats_loader.py`.
- [x] Implement Snowflake fetch CLI using the existing SQL builder.
- [x] Wire optional stats snapshot into `run_full_load_db.py`.

### Task 3: DB Refresh

- [x] Generate `data/source_snapshots/product_review_stats_snowflake_2026-06-18.json` from live Snowflake.
- [x] Refresh `data/source_snapshots/product_review_stats_snowflake_latest.json`.
- [x] Re-run full DB load using the stats snapshot.

### Task 4: Verification

- [x] Run targeted tests.
- [x] Run full `ruff check .`.
- [x] Run full `python -m pytest -q`.
- [x] Query local DB:
  - `product_review_stats.source_review_count_6m > 0` count must be positive.
  - `source_avg_rating_6m` count must be positive.
  - Product `61289` must have positive `source_review_count_6m`.
  - `serving_product_profile` must match `product_review_stats`.
- [x] Request code review and fix important findings.

## Final Verification Snapshot

- `ruff check .`: passed.
- `python -m pytest -q`: 676 passed, 36 skipped.
- `product_review_stats`: 516 rows, 516 positive `source_review_count_6m`, 0 zero 6-month counts.
- `serving_product_profile`: 516 positive `source_review_count_6m`, 516 non-null `source_avg_rating_6m`.
- Product `61289`: `source_review_count_6m=862`, `source_avg_rating_6m=4.941`, `source_review_count_all=4965`.
- `validate_all(..., expected_min_source_review_count_6m=516, expected_min_source_avg_rating_6m=516, enforce_source_grounding=True)`: `OK`.
