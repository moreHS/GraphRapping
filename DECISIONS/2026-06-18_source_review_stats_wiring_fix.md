# 2026-06-18 Source Review Stats Wiring Fix

## Background

The 2026-06-17 baseline incorrectly treated `source_review_count_6m` as wired
because 516 rows were non-null. A direct DB check showed every one of those
516 values was `0`, `source_avg_rating_6m` was entirely `NULL`, and 6-month
min/max dates were also `NULL`.

The original handoff required source-grounded 6-month stats from
`cdp.sf_cdpdw.f_prd_rv_hist`. Product `61289` had live Snowflake evidence on
2026-06-15 (`review_count_6m=874`, `avg_prd_apal_scr_6m=4.939359`), so the
all-zero DB state was a GraphRapping wiring bug, not an AmoreSimulation issue.

## Decision

1. `product_review_stats` is the source-stats table. It must be populated from
   a dedicated source review stats snapshot, not from product master
   `REVIEW_COUNT/REVIEW_SCORE` fallback.
2. Catalog `REVIEW_COUNT/REVIEW_SCORE` may populate all-time fallback fields
   only. It must not fabricate 6-month count/rating/date values.
3. `source_review_count_6m` and `source_review_score_count_6m` are nullable in
   raw and serving schemas. `NULL` means unknown; `0` is reserved for a real
   source query result of zero.
4. Source identity collisions remain excluded from the product-id compatibility
   stats map. `35119` has two real source identities and cannot be losslessly
   represented by one `product_id` row.

## Implementation

- Added `scripts/fetch_source_review_stats_snapshot.py`.
- Added `data/source_snapshots/product_review_stats_snowflake_2026-06-18.json`
  and `data/source_snapshots/product_review_stats_snowflake_latest.json`.
- Added `scripts/run_906_full_load_db.py`.
- Updated `src/loaders/source_review_stats_loader.py` to load stats snapshots
  and reject product master snapshots missing 6-month fields.
- Updated `src/jobs/run_full_load.py` so fallback all-time stats do not create
  `source_review_count_6m=0`.
- Updated `sql/ddl_raw.sql` and `src/db/repos/product_repo.py` so unknown
  6-month count fields persist as `NULL`.

## Verified Local DB Result

After full load on 2026-06-18:

| Check | Result |
| --- | ---: |
| `product_review_stats` rows | 516 |
| `product_review_stats.source_review_count_6m > 0` | 516 |
| `product_review_stats.source_review_count_6m = 0` | 0 |
| `product_review_stats.source_avg_rating_6m` non-null | 516 |
| `serving_product_profile.source_review_count_6m > 0` | 516 |
| `serving_product_profile.source_avg_rating_6m` non-null | 516 |
| `serving_product_profile` rows with collision/no source stats | 1 |

Representative products:

| product_id | source_channel | source_review_count_6m | source_avg_rating_6m | source_review_count_all |
| --- | --- | ---: | ---: | ---: |
| `61289` | `031` | 862 | 4.941 | 4965 |
| `35117` | `036` | 36329 | 4.868 | 162552 |
| `16855` | `031` | 24 | 4.958 | 69 |
| `35119` | `031,036` collision | NULL | NULL | NULL |
