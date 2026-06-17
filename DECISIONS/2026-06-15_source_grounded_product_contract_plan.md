# Source-Grounded Product Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GraphRapping preserve source-grounded product truth, raw review identity/rating, and graph outputs by the same string-preserved `prd_id`/`product_id`, so downstream consumers never receive mock/heuristic product truth as production truth.

**Architecture:** Treat `product_id` as the source product key, not a generated GraphRapping key. Preserve source identity on `review_raw`, enrich `product_master` from trusted catalog/Snowflake fields, persist source review stats in a dedicated table, and denormalize only explicit `source_*` stats into `serving_product_profile`. Keep existing graph support fields backward compatible; do not silently redefine `review_count_*`.

**Tech Stack:** Python 3.11, asyncpg/Postgres, pure SQL builders for Snowflake-compatible queries, existing GraphRapping full/incremental DB pipelines, pytest/ruff/mypy.

---

## Current Root Cause

This is not a missing display field. It is a broken source-truth contract.

1. `product_loader` reads `REVIEW_COUNT` and `REVIEW_SCORE`, but stores them only in transient `_es_meta`; DB DDL/repo/serving schema cannot persist them.
2. `serving_product_profile.review_count_*` intentionally means graph evidence support, not source product review volume.
3. `RawReviewRecord` currently drops `source_product_id`, `channel`, and review score, so review raw rows cannot prove source product identity after ingest.
4. `process_review()` uses brand/product-name matching first, even when a source product id exists.
5. `scripts/synthesize_mock_from_v260605.py` invented `BRAND_NAME` from `prd_nm` first token and wrote `REVIEW_COUNT=0`, `REVIEW_SCORE=0.0`; this converted missing mock metadata into false product truth.
6. Local DB verification on 2026-06-15 showed product `61289` persisted as:

```text
brand_name = 【LIVE/2종
price = 0
review_count_all = 0
signal_support_count_all = 0
```

This must never pass a production-readiness contract.

## Non-Negotiable Contract

- `product_id` is a string-preserved source product id.
- `source_product_id` equals `product_id` unless a future multi-source mapping table explicitly says otherwise.
- Do not assume numeric-only product ids; real `P`-prefixed ids are valid source ids.
- Review raw facts preserve source identity: `source_product_id`, `source_channel`, `source_key_type`, and source rating when available.
- Review-to-product matching must prefer exact `source_product_id` over brand/name fuzzy matching.
- Product master truth must come from trusted source columns, not product-name token heuristics.
- Existing `review_count_30d/90d/all` remain graph support counts for backward compatibility.
- Source product review volume/rating must use explicit `source_review_*` fields.
- `avg_rating=0.0` is invalid when there are no score rows; use NULL.
- Mock data may be incomplete, but it must be marked incomplete. Mock code must not invent production-looking product truth.

## Source Priority

### Product Identity

1. `rs_own.product_id` / `source_product_id` from review source.
2. Product catalog `ONLINE_PROD_SERIAL_NUMBER`.
3. Snowflake own-source key:
   - channel `031`: `TO_VARCHAR(dcpm.ecp_onln_prd_srno)` / `t4.ecp_onln_prd_srno`
   - channels `036/039/048`: `TO_VARCHAR(fprh.chn_prd_cd)` unless a verified ECP srno mapping is introduced.

### Brand

1. Product catalog `BRAND_NAME` when non-empty and not placeholder/promo text.
2. Snowflake `dpam.brnd_nm`.
3. `rs.jsonl` `brnd_nm`.
4. External-source `rspn_sal_lcns_nm`.
5. NULL/`Unknown` with explicit provenance, not product-name token extraction.

### Product Name

1. Product catalog `REPRESENTATIVE_PROD_NAME`.
2. Snowflake `dpam.rprs_prd_nm`.
3. Snowflake `t4.ecp_onln_prd_nm`.
4. Product catalog `prd_nm`.
5. `rs.jsonl` `prd_nm`.

Raw promo-heavy names may be preserved as source facts, but display/representative name must prefer representative source fields.

### Review Volume And Rating

1. Snowflake `f_prd_rv_hist` aggregated by source product id, using `COUNT(*)`, `COUNT(fprh.prd_apal_scr)`, `AVG(fprh.prd_apal_scr)`.
2. Product catalog `REVIEW_COUNT` / `REVIEW_SCORE` only as fallback if Snowflake stats are not available.
3. Graph signal support only as a final fallback for features that can tolerate graph-derived proxies.

## File Map

### New Files

- `src/loaders/source_review_stats_loader.py`
  - Dataclass `SourceReviewStats`.
  - Pure SQL builders for own-source review stats.
  - Row parser from Snowflake-like dict rows to GraphRapping dict rows.

- `src/loaders/product_truth_merge.py`
  - Pure functions for applying product truth priority.
  - Placeholder/promo brand rejection.
  - No product-name token extraction.

- `tests/test_source_review_stats_loader.py`
  - SQL shape, escaping, row parsing, NULL average handling.

- `tests/test_product_truth_merge.py`
  - Brand priority and no heuristic fallback.
  - `61289` regression: promo prefix brand is rejected when Snowflake brand exists.

- `tests/test_source_product_id_contract.py`
  - Raw review source id persistence.
  - Exact source product id matching beats fuzzy/name matching.

- `tests/test_product_review_stats_repo.py`
  - Postgres upsert/idempotency for `product_review_stats`.

- `tests/test_serving_source_review_stats.py`
  - Serving builder preserves graph counts and adds source stats.

### Modified Files

- `sql/ddl_raw.sql`
  - Add review source identity/rating columns to `review_raw` and `review_raw_history`.
  - Add product master source truth columns.
  - Add `product_review_stats`.

- `sql/ddl_mart.sql`
  - Add `source_review_*` columns to `serving_product_profile`.

- `src/ingest/review_ingest.py`
  - Extend `RawReviewRecord` with source product identity and rating.
  - Persist those fields in `review_raw` dict and raw_payload.

- `src/loaders/relation_loader.py`
  - Map `source_product_id`, `channel`, `source_key_type`, `prd_apal_scr` when present.

- `src/loaders/rs_jsonl_loader.py`
  - Map `product_id` to `source_product_id`.
  - Map `channel` to `source_channel`.
  - Map `prd_apal_scr` if present.

- `src/loaders/product_loader.py`
  - Stop hiding source stats only in `_es_meta`.
  - Produce product master source fields and fallback review stats rows.

- `src/link/product_matcher.py`
  - Add exact source-id matching helper, or expose index membership check.

- `src/jobs/run_daily_pipeline.py`
  - Prefer source-id exact match before brand/product-name matching.
  - Pass source review stats into `build_serving_product_profile`.

- `src/jobs/run_full_load.py`
  - Extend `FullLoadConfig` with `source_review_stats_by_product`.
  - Apply `product_truth_merge` before `run_batch`.

- `src/jobs/run_full_load_db.py`
  - Persist `product_review_stats` in Layer 0.

- `src/jobs/run_incremental_pipeline.py`
  - Rebuild dirty product serving profiles with latest product review stats.

- `src/jobs/run_incremental_pipeline_db.py`
  - Optional provider hook for refreshing stats of dirty product ids.

- `src/db/repos/product_repo.py`
  - Upsert expanded `product_master`.
  - Add `upsert_product_review_stats`.

- `src/db/repos/review_repo.py`
  - Insert/update expanded `review_raw` and history columns.

- `src/db/repos/mart_repo.py`
  - Upsert expanded `serving_product_profile`.

- `src/mart/build_serving_views.py`
  - Add `source_review_stats` argument.
  - Keep graph support fields unchanged.

- `src/mart/serving_profile_schema.py`
  - Add `source_review_*` columns to single source of truth.

- `src/db/contract_validator.py`
  - Require new columns.
  - Add production-readiness checks for source identity/stats/truth quality.

- `sql/consumer_contract_queries.sql`
  - Select source stats and source identity.

- `docs/architecture/db_consumer_contract.md`
  - Document source-vs-graph review semantics.

- `scripts/synthesize_mock_from_v260605.py`
  - Remove brand token heuristic as production-looking truth.
  - Mark missing catalog truth explicitly.

## Task 1: Lock The Failing Contract Before Fixing

**Files:**
- Create: `tests/test_source_product_id_contract.py`
- Create: `tests/test_product_truth_merge.py`
- Modify later: implementation files listed above.

- [ ] **Step 1: Add regression test for product `61289` truth merge**

Test intent:

```python
def test_source_brand_overrides_promo_prefix_brand_for_61289():
    catalog_master = {
        "product_id": "61289",
        "product_name": "【LIVE/2종 증정+6,000P】블랙쿠션 듀오 SPF34/PA++ (모든컬러)",
        "brand_name": "【LIVE/2종",
        "brand_id": "live2",
    }
    stats = {
        "product_id": "61289",
        "brand_id": "11107",
        "brand_name": "헤라",
        "representative_product_name": "블랙쿠션 듀오 SPF34/PA++",
    }

    merged = merge_product_truth(catalog_master, source_review_stats=stats)

    assert merged["product_id"] == "61289"
    assert merged["brand_name"] == "헤라"
    assert merged["brand_id"] == "11107"
    assert merged["representative_product_name"] == "블랙쿠션 듀오 SPF34/PA++"
```

Expected initial failure: `ImportError` or wrong brand remains `【LIVE/2종`.

- [ ] **Step 2: Add regression test forbidding product-name brand invention**

```python
def test_missing_brand_stays_unknown_not_first_product_token():
    catalog_master = {
        "product_id": "P1",
        "product_name": "[기획] 그린티 히알루론산 세럼",
        "brand_name": None,
    }

    merged = merge_product_truth(catalog_master, source_review_stats=None)

    assert merged["brand_name"] is None
    assert merged["source_truth_quality"] == "MISSING_SOURCE_BRAND"
```

- [ ] **Step 3: Add source product id match test**

```python
def test_source_product_id_exact_match_beats_name_matching():
    idx = ProductIndex.build([
        {"product_id": "61289", "product_name": "블랙쿠션 듀오", "brand_name": "헤라"},
        {"product_id": "P045", "product_name": "블랙 쿠션", "brand_name": "Fixture"},
    ])
    match = match_product_by_source_id("61289", idx)

    assert match is not None
    assert match.matched_product_id == "61289"
    assert match.match_method == "source_product_id"
```

Add a second integration-style test using existing helpers from
`tests/test_kg_mode_wiring.py` or `tests/test_end_to_end.py` only after the
helper-level test passes. The integration test must assert the persisted
`review_catalog_link.match_method == "source_product_id"`.

- [ ] **Step 4: Run failing tests**

Run:

```bash
python -m pytest tests/test_product_truth_merge.py tests/test_source_product_id_contract.py -q
```

Expected: fails before implementation.

## Task 2: Preserve Source Product Identity And Rating In Review Raw

**Files:**
- Modify: `sql/ddl_raw.sql`
- Modify: `src/ingest/review_ingest.py`
- Modify: `src/loaders/relation_loader.py`
- Modify: `src/loaders/rs_jsonl_loader.py`
- Modify: `src/db/repos/review_repo.py`
- Test: `tests/test_source_product_id_contract.py`
- Test: `tests/test_postgres_integration.py`

- [ ] **Step 1: Extend DDL**

Add idempotent columns:

```sql
ALTER TABLE review_raw ADD COLUMN IF NOT EXISTS source_product_id text;
ALTER TABLE review_raw ADD COLUMN IF NOT EXISTS source_channel text;
ALTER TABLE review_raw ADD COLUMN IF NOT EXISTS source_key_type text;
ALTER TABLE review_raw ADD COLUMN IF NOT EXISTS source_rating numeric(5, 3);

ALTER TABLE review_raw_history ADD COLUMN IF NOT EXISTS source_product_id text;
ALTER TABLE review_raw_history ADD COLUMN IF NOT EXISTS source_channel text;
ALTER TABLE review_raw_history ADD COLUMN IF NOT EXISTS source_key_type text;
ALTER TABLE review_raw_history ADD COLUMN IF NOT EXISTS source_rating numeric(5, 3);
```

Do not make these `NOT NULL`; external/global sources can be incomplete.

- [ ] **Step 2: Extend `RawReviewRecord`**

Add fields:

```python
source_product_id: str | None = None
source_channel: str | None = None
source_key_type: str | None = None
source_rating: float | None = None
```

In `ingest_review`, write them both as top-level `review_raw` fields and inside `raw_payload`.

- [ ] **Step 3: Update loaders**

`relation_loader`:

```python
source_product_id=record.get("source_product_id") or record.get("product_id"),
source_channel=record.get("channel"),
source_key_type=record.get("source_key_type"),
source_rating=_parse_optional_float(record.get("prd_apal_scr") or record.get("source_rating")),
```

`rs_jsonl_loader`:

```python
source_product_id=str(record.get("product_id")) if record.get("product_id") is not None else None,
source_channel=record.get("channel"),
source_key_type=_source_key_type(record.get("channel")),
source_rating=_parse_optional_float(record.get("prd_apal_scr")),
```

Implement `_source_key_type("031") -> "ecp_onln_prd_srno"`, `_source_key_type("036"/"039"/"048") -> "chn_prd_cd"`.

- [ ] **Step 4: Update repo insert/history SQL**

`upsert_review_raw` and `_append_history` must insert/update/copy the new columns.

- [ ] **Step 5: Verify**

Run:

```bash
python -m pytest tests/test_loaders.py tests/test_rs_jsonl_transform.py tests/test_postgres_integration.py -q
```

Expected: all pass after repo/test updates.

## Task 3: Source Product Id Exact Match Before Fuzzy Matching

**Files:**
- Modify: `src/link/product_matcher.py`
- Modify: `src/jobs/run_daily_pipeline.py`
- Test: `tests/test_source_product_id_contract.py`
- Test: `tests/test_product_matcher.py`

- [ ] **Step 1: Add exact id lookup**

Add:

```python
def match_product_by_source_id(source_product_id: str | None, index: ProductIndex) -> MatchResult | None:
    if not source_product_id:
        return None
    pid = str(source_product_id)
    if pid in index.exact:
        return MatchResult(
            matched_product_id=pid,
            match_status=MatchStatus.EXACT,
            match_score=1.0,
            match_method="source_product_id",
        )
    return None
```

`MatchStatus.EXACT` already exists in `src/common/enums.py`; use it for this match path.

- [ ] **Step 2: Use exact source id first in `process_review`**

Before:

```python
match = match_product(record.brnd_nm, record.prod_nm, product_index)
```

After:

```python
match = match_product_by_source_id(record.source_product_id, product_index)
if match is None:
    match = match_product(record.brnd_nm, record.prod_nm, product_index)
```

- [ ] **Step 3: Preserve link provenance**

`review_catalog_link` should keep:

```python
"source_product_id": record.source_product_id,
"source_channel": record.source_channel,
"source_key_type": record.source_key_type,
```

Only add DB columns if needed by downstream contract; otherwise preserve in `review_raw` and use `match_method`.

- [ ] **Step 4: Verify no fallback regression**

Run:

```bash
python -m pytest tests/test_product_matcher.py tests/test_end_to_end.py tests/test_source_product_id_contract.py -q
```

Expected: exact source-id tests pass; existing name-match tests still pass.

## Task 4: Product Master Source Truth Merge

**Files:**
- Create: `src/loaders/product_truth_merge.py`
- Modify: `src/loaders/product_loader.py`
- Modify: `src/ingest/product_ingest.py`
- Modify: `sql/ddl_raw.sql`
- Modify: `src/db/repos/product_repo.py`
- Test: `tests/test_product_truth_merge.py`
- Test: `tests/test_product_loader_mock_schema.py`
- Test: `tests/test_master_upsert_completeness.py`

- [ ] **Step 1: Add product master DDL columns**

```sql
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_product_id text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_channel text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_key_type text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS representative_product_name text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_truth_source text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_truth_quality text;
ALTER TABLE product_master ADD COLUMN IF NOT EXISTS source_truth_updated_at timestamptz;
```

- [ ] **Step 2: Extend `ProductRecord`**

Add:

```python
source_product_id: str | None = None
source_channel: str | None = None
source_key_type: str | None = None
representative_product_name: str | None = None
source_truth_source: str | None = None
source_truth_quality: str | None = None
```

- [ ] **Step 3: Implement `product_truth_merge`**

Required helpers:

```python
PROMO_PREFIX_CHARS = ("【", "[", "(")

def is_placeholder_brand(value: str | None) -> bool:
    if not value:
        return True
    stripped = value.strip()
    return stripped.startswith(PROMO_PREFIX_CHARS) or stripped in {"Unknown", "UNKNOWN", "기타"}
```

`merge_product_truth(product_master, source_review_stats=None)` must:

- preserve `product_id`
- set `source_product_id = product_id` if absent
- prefer non-placeholder catalog brand
- override placeholder/promo brand from source stats brand
- never derive brand from product name
- set `source_truth_quality` to:
  - `SOURCE_GROUNDED` when brand/name came from trusted source
  - `PARTIAL_SOURCE` when product id exists but brand/name missing
  - `MISSING_SOURCE_BRAND` when no trusted brand exists

- [ ] **Step 4: Stop product-loader stats loss**

`load_products_from_es_records` must no longer rely on `_es_meta` as the only carrier. It should map:

```python
representative_product_name=record.get("REPRESENTATIVE_PROD_NAME")
source_product_id=record.get("ONLINE_PROD_SERIAL_NUMBER")
source_key_type="ecp_onln_prd_srno" when channel is own 031 or unknown ES catalog source
source_truth_source="product_catalog_es"
```

Keep `_es_meta` temporarily for backward compatibility, but do not make serving depend only on it.

- [ ] **Step 5: Update repo completeness tests**

`tests/test_master_upsert_completeness.py` must include the new product columns in `_PRODUCT_REFRESHABLE_COLUMNS`.

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest tests/test_product_truth_merge.py tests/test_product_loader_mock_schema.py tests/test_master_upsert_completeness.py -q
```

Expected: all pass.

## Task 5: Source Review Stats Loader

**Files:**
- Create: `src/loaders/source_review_stats_loader.py`
- Test: `tests/test_source_review_stats_loader.py`
- Docs reference: `/Users/amore/workplace/ap-data-utils/src/nlp_data_utils/snowflake/nlp/load.py`
- Docs reference: `/Users/amore/workplace/rs_origin/service-rs/sm_batch_pipeline/src/utils/sku_enrichment/snowflake_query.py`

- [ ] **Step 1: Define dataclass**

```python
@dataclass(frozen=True)
class SourceReviewStats:
    product_id: str
    source_channel: str | None
    source_key_type: str | None
    product_name: str | None
    representative_product_name: str | None
    brand_id: str | None
    brand_name: str | None
    review_count_6m: int
    score_count_6m: int
    avg_rating_6m: float | None
    review_min_date_6m: date | None
    review_max_date_6m: date | None
    review_count_all: int
    score_count_all: int
    avg_rating_all: float | None
    review_min_date_all: date | None
    review_max_date_all: date | None
    source: str = "snowflake:f_prd_rv_hist"
```

- [ ] **Step 2: Implement SQL literal escaping**

```python
def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
```

Use this only in pure builders. If a future Snowflake helper supports binds, add a separate bind-based executor; do not mix bind assumptions into raw SQL tests.

- [ ] **Step 3: Implement own `031` SQL builder**

Required SQL shape:

```sql
WITH base AS (
    SELECT
        fprh.chn_cd,
        TO_VARCHAR(dcpm.ecp_onln_prd_srno) AS product_id,
        MAX(t4.ecp_onln_prd_nm) AS product_name,
        MAX(dpam.rprs_prd_nm) AS representative_product_name,
        MAX(dpam.brnd_cd) AS brand_id,
        MAX(dpam.brnd_nm) AS brand_name,
        COUNT(*) AS review_count_all,
        COUNT(fprh.prd_apal_scr) AS score_count_all,
        AVG(fprh.prd_apal_scr) AS avg_rating_all,
        MIN(fprh.stnd_ymd) AS review_min_date_all,
        MAX(fprh.stnd_ymd) AS review_max_date_all,
        COUNT(CASE
            WHEN fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()
            THEN 1
        END) AS review_count_6m,
        COUNT(CASE
            WHEN fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()
             AND fprh.prd_apal_scr IS NOT NULL
            THEN 1
        END) AS score_count_6m,
        AVG(CASE
            WHEN fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()
            THEN fprh.prd_apal_scr
        END) AS avg_rating_6m,
        MIN(CASE
            WHEN fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()
            THEN fprh.stnd_ymd
        END) AS review_min_date_6m,
        MAX(CASE
            WHEN fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()
            THEN fprh.stnd_ymd
        END) AS review_max_date_6m
    FROM cdp.sf_cdpdw.f_prd_rv_hist fprh
    LEFT JOIN cdp.sf_cdpdw.d_prd_anl_mstr dpam
      ON fprh.prd_cd = dpam.prd_cd
    LEFT JOIN cdp.sf_cdpdw.d_chn_prd_mstr dcpm
      ON fprh.chn_cd = dcpm.chn_cd
     AND fprh.chn_prd_cd = dcpm.chn_prd_cd
    LEFT JOIN cdp.sf_cdpdw.d_ecp_onln_prd_mstr t4
      ON dcpm.chn_cd = t4.chn_cd
     AND dcpm.ecp_onln_prd_srno = t4.ecp_onln_prd_srno
    WHERE fprh.chn_cd = '031'
      AND TO_VARCHAR(dcpm.ecp_onln_prd_srno) IN ('61289', '12345')
    GROUP BY fprh.chn_cd, TO_VARCHAR(dcpm.ecp_onln_prd_srno)
)
SELECT * FROM base
```

The canonical query shape uses `CASE WHEN` aggregate expressions, not PostgreSQL `FILTER`, so the implementation does not fork by warehouse dialect.

- [ ] **Step 4: Implement own non-031 builder**

For `036/039/048`, use:

```sql
TO_VARCHAR(fprh.chn_prd_cd) AS product_id
```

and `source_key_type = 'chn_prd_cd'`.

- [ ] **Step 5: Unit tests**

Assert:

- product ids are escaped: `"12'34"` becomes `'12''34'`
- query includes `AVG(fprh.prd_apal_scr)`
- query includes `COUNT(CASE` and does not include `FILTER (`
- query includes `MAX(dpam.brnd_nm)` and `MAX(dpam.brnd_cd)`
- query includes `MAX(t4.ecp_onln_prd_nm)` and `MAX(dpam.rprs_prd_nm)`
- empty score count parses `avg_rating_*` as `None`

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest tests/test_source_review_stats_loader.py -q
```

Expected: pass.

## Task 6: Persist `product_review_stats`

**Files:**
- Modify: `sql/ddl_raw.sql`
- Modify: `src/db/repos/product_repo.py`
- Test: `tests/test_product_review_stats_repo.py`

- [ ] **Step 1: Add table**

```sql
CREATE TABLE IF NOT EXISTS product_review_stats (
    product_id text PRIMARY KEY REFERENCES product_master(product_id),
    source_channel text,
    source_key_type text,
    source_review_count_6m int NOT NULL DEFAULT 0,
    source_review_score_count_6m int NOT NULL DEFAULT 0,
    source_avg_rating_6m numeric(5, 3),
    source_review_min_date_6m date,
    source_review_max_date_6m date,
    source_review_count_all int NOT NULL DEFAULT 0,
    source_review_score_count_all int NOT NULL DEFAULT 0,
    source_avg_rating_all numeric(5, 3),
    source_review_min_date_all date,
    source_review_max_date_all date,
    source text NOT NULL DEFAULT 'snowflake:f_prd_rv_hist',
    updated_at timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Add repo function**

```python
async def upsert_product_review_stats(uow: UnitOfWork, row: dict[str, Any]) -> None:
    await uow.execute(
        """
        INSERT INTO product_review_stats (
            product_id, source_channel, source_key_type,
            source_review_count_6m, source_review_score_count_6m,
            source_avg_rating_6m, source_review_min_date_6m, source_review_max_date_6m,
            source_review_count_all, source_review_score_count_all,
            source_avg_rating_all, source_review_min_date_all, source_review_max_date_all,
            source, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (product_id) DO UPDATE SET
            source_channel = EXCLUDED.source_channel,
            source_key_type = EXCLUDED.source_key_type,
            source_review_count_6m = EXCLUDED.source_review_count_6m,
            source_review_score_count_6m = EXCLUDED.source_review_score_count_6m,
            source_avg_rating_6m = EXCLUDED.source_avg_rating_6m,
            source_review_min_date_6m = EXCLUDED.source_review_min_date_6m,
            source_review_max_date_6m = EXCLUDED.source_review_max_date_6m,
            source_review_count_all = EXCLUDED.source_review_count_all,
            source_review_score_count_all = EXCLUDED.source_review_score_count_all,
            source_avg_rating_all = EXCLUDED.source_avg_rating_all,
            source_review_min_date_all = EXCLUDED.source_review_min_date_all,
            source_review_max_date_all = EXCLUDED.source_review_max_date_all,
            source = EXCLUDED.source,
            updated_at = EXCLUDED.updated_at
        """,
        row["product_id"],
        row.get("source_channel"),
        row.get("source_key_type"),
        row.get("source_review_count_6m", 0),
        row.get("source_review_score_count_6m", 0),
        row.get("source_avg_rating_6m"),
        row.get("source_review_min_date_6m"),
        row.get("source_review_max_date_6m"),
        row.get("source_review_count_all", 0),
        row.get("source_review_score_count_all", 0),
        row.get("source_avg_rating_all"),
        row.get("source_review_min_date_all"),
        row.get("source_review_max_date_all"),
        row.get("source", "snowflake:f_prd_rv_hist"),
        uow.as_of_ts,
    )
```

It must update every non-PK field on conflict and set `updated_at = uow.as_of_ts`.

- [ ] **Step 3: PG idempotency test**

Insert stats for `61289`, update with new counts, assert one row and updated values.

- [ ] **Step 4: Verify**

Run:

```bash
GRAPHRAPPING_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/graphrapping_wave4 \
python -m pytest tests/test_product_review_stats_repo.py -q
```

If local Postgres is unavailable, run unit tests and mark PG test as existing skip-pattern compatible.

## Task 7: Denormalize Source Stats Into Serving Profile

**Files:**
- Modify: `sql/ddl_mart.sql`
- Modify: `src/mart/serving_profile_schema.py`
- Modify: `src/mart/build_serving_views.py`
- Modify: `src/db/repos/mart_repo.py`
- Modify: `src/db/contract_validator.py`
- Test: `tests/test_serving_source_review_stats.py`
- Test: `tests/test_serving_profile_columns_align.py`
- Test: `tests/test_db_contract_validator.py`

- [ ] **Step 1: Add serving DDL columns**

```sql
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_product_id text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_channel text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_key_type text;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_count_6m int NOT NULL DEFAULT 0;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_score_count_6m int NOT NULL DEFAULT 0;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_avg_rating_6m numeric(5, 3);
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_min_date_6m date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_max_date_6m date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_count_all int NOT NULL DEFAULT 0;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_score_count_all int NOT NULL DEFAULT 0;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_avg_rating_all numeric(5, 3);
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_min_date_all date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_max_date_all date;
ALTER TABLE serving_product_profile ADD COLUMN IF NOT EXISTS source_review_stats_source text;
```

- [ ] **Step 2: Update schema single source of truth**

Append the above names to `SERVING_PRODUCT_PROFILE_COLUMNS` near product truth/support fields.

- [ ] **Step 3: Update builder signature**

```python
def build_serving_product_profile(
    product_master: dict[str, Any],
    agg_signals: list[dict[str, Any]],
    window_type: str = "all",
    concept_links: list[dict] | None = None,
    promoted_only: bool = True,
    source_review_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Builder output must keep:

```python
"review_count_all": review_count_all,
"signal_support_count_all": signal_support_count_all,
```

and add:

```python
"source_review_count_6m": stats.get("source_review_count_6m", 0),
"source_avg_rating_6m": stats.get("source_avg_rating_6m"),
"source_review_count_all": stats.get("source_review_count_all", 0),
"source_avg_rating_all": stats.get("source_avg_rating_all"),
```

- [ ] **Step 4: Update mart repo**

`upsert_serving_product_profile` insert/update/args must match `SERVING_PRODUCT_PROFILE_COLUMNS + ["updated_at"]`.

- [ ] **Step 5: Update validator**

`_REQUIRED_TABLES["serving_product_profile"]` already derives from schema module; add production readiness query:

```sql
SELECT COUNT(*)
FROM serving_product_profile spp
JOIN product_master pm USING(product_id)
WHERE pm.is_active = true
  AND spp.is_active = true
  AND (
      COALESCE(spp.source_product_id, '') <> pm.product_id
      OR pm.brand_name LIKE '【%'
      OR pm.brand_name LIKE '[%'
  );
```

This should be reported as INVALID only in strict production-readiness mode, not for generic unit fixture DBs.

- [ ] **Step 6: Verify schema alignment**

Run:

```bash
python -m pytest tests/test_serving_profile_columns_align.py tests/test_serving_source_review_stats.py tests/test_db_contract_validator.py -q
```

Expected: all pass.

## Task 8: Wire Full Load And Incremental Pipelines

**Files:**
- Modify: `src/jobs/run_full_load.py`
- Modify: `src/jobs/run_daily_pipeline.py`
- Modify: `src/jobs/run_full_load_db.py`
- Modify: `src/jobs/run_incremental_pipeline.py`
- Modify: `src/jobs/run_incremental_pipeline_db.py`
- Test: `tests/test_full_load_db.py`
- Test: `tests/test_incremental_pipeline_db.py`
- Test: `tests/test_incremental_cleanup_wiring.py`

- [ ] **Step 1: Extend config**

```python
source_review_stats_by_product: dict[str, dict[str, Any]] | None = None
```

Add to `FullLoadConfig`.

- [ ] **Step 2: Merge product truth before batch**

After loading products and before `build_product_lookups_from_masters`, apply:

```python
product_result.product_masters = {
    pid: merge_product_truth(
        master,
        source_review_stats=(config.source_review_stats_by_product or {}).get(pid),
    )
    for pid, master in product_result.product_masters.items()
}
```

- [ ] **Step 3: Pass stats to serving builder**

In `run_batch`, for each product:

```python
stats = source_review_stats_by_product.get(pid) if source_review_stats_by_product else None
profile = build_serving_product_profile(
    master,
    pid_signals_dicts,
    concept_links=links,
    source_review_stats=stats,
)
```

- [ ] **Step 4: Persist stats in DB full load**

In `_persist_layer0`, after product master upsert:

```python
if pid in source_review_stats_by_product:
    await product_repo.upsert_product_review_stats(uow, source_review_stats_by_product[pid])
```

Make stats source available from `in_memory.batch_result` or `FullLoadResult`.

- [ ] **Step 5: Incremental**

Dirty products must rebuild serving profiles with stats read from `product_review_stats`:

```sql
SELECT * FROM product_review_stats WHERE product_id = $1
```

If no stats row exists, builder defaults source counts to zero/null.

- [ ] **Step 6: Verify**

Run:

```bash
python -m pytest tests/test_full_load_db.py tests/test_incremental_pipeline_db.py tests/test_incremental_cleanup_wiring.py -q
```

Expected: pass.

## Task 9: Fix Mock Synthesis Without Inventing Product Truth

**Files:**
- Modify: `scripts/synthesize_mock_from_v260605.py`
- Modify: `mockdata/README.md`
- Modify: `mockdata/SCHEMA_RS_JSONL.md`
- Test: `tests/test_mock_integrity.py`
- Test: `tests/test_mock_pipeline_smoke.py`
- Test: `tests/test_corpus_promotion_baseline.py`

- [ ] **Step 1: Remove brand token extraction from catalog truth**

Delete or quarantine this behavior:

```python
brand_name = extract_brand_token(prd_nm)
```

Replacement:

```python
brand_name = meta.get("brnd_nm") or meta.get("rspn_sal_lcns_nm") or None
source_truth_quality = "PARTIAL_SOURCE" if brand_name else "MISSING_SOURCE_BRAND"
```

If the v260605 source sample lacks brand, the mock catalog must say it lacks brand. It must not pretend promo text is a brand.

- [ ] **Step 2: Keep category heuristic only as mock metadata**

Category keyword extraction can remain only if marked:

```json
"SOURCE_TRUTH_QUALITY": "MOCK_CATEGORY_HEURISTIC"
```

Do not let category heuristic be used as proof of source category in production-readiness mode.

- [ ] **Step 3: Stop writing `REVIEW_SCORE=0.0` as a real rating**

Use:

```python
"REVIEW_COUNT": None,
"REVIEW_SCORE": None,
```

or omit the fields. Product loader must parse missing values as NULL/zero count with `source='mock:missing'`, not source rating.

- [ ] **Step 4: Add mock integrity tests**

Assert:

```python
assert not rec["BRAND_NAME"].startswith("【")
assert rec.get("REVIEW_SCORE") is None
```

For records without source brand, assert `SOURCE_TRUTH_QUALITY == "MISSING_SOURCE_BRAND"`.

- [ ] **Step 5: Recompute baseline**

After mock regeneration, promoted product counts may change. Re-run:

```bash
python scripts/synthesize_mock_from_v260605.py
python -m pytest tests/test_mock_pipeline_smoke.py tests/test_corpus_promotion_baseline.py -q
```

Update baseline only after confirming signal/quarantine changes are explained by truth contract changes, not accidental data loss.

## Task 10: Consumer Contract And AmoreSimulation Handoff

**Files:**
- Modify: `sql/consumer_contract_queries.sql`
- Modify: `docs/architecture/db_consumer_contract.md`
- Create: `docs/architecture/amoresim_source_grounded_handoff_2026_06_15.md`

- [ ] **Step 1: Update standard product query**

Select:

```sql
pm.product_id,
spp.source_product_id,
spp.source_channel,
spp.source_key_type,
pm.product_name,
pm.representative_product_name,
pm.brand_id,
pm.brand_name,
pm.category_id,
pm.category_name,
pm.price,
spp.source_review_count_6m,
spp.source_avg_rating_6m,
spp.source_review_count_all,
spp.source_avg_rating_all,
spp.review_count_all AS graph_review_support_all,
spp.signal_support_count_all
```

Keep:

```sql
WHERE pm.is_active = true
  AND spp.is_active = true
```

- [ ] **Step 2: Document semantics**

Add a table:

| Field | Meaning | Consumer Use |
|---|---|---|
| `source_review_count_6m` | raw source review volume | social proof / ranking |
| `source_avg_rating_6m` | raw source avg `prd_apal_scr` | product trust |
| `review_count_all` | graph distinct evidence count | graph confidence/shrinkage |
| `signal_support_count_all` | graph signal occurrence sum | diagnostics/badges only |

- [ ] **Step 3: Handoff guidance for AmoreSimulation**

Required consumer mapping:

```python
review_volume = (
    profile.source_review_count_6m
    or profile.source_review_count_all
    or profile.review_count_all
)
avg_rating = (
    profile.source_avg_rating_6m
    or profile.source_avg_rating_all
    or compute_avg_rating_gr(product_signals)
)
```

Do not implement AmoreSimulation changes from GraphRapping unless the user explicitly asks; this handoff defines the required follow-up.

## Task 11: Verification Matrix

Run in this order:

- [ ] **Unit contract tests**

```bash
python -m pytest \
  tests/test_product_truth_merge.py \
  tests/test_source_review_stats_loader.py \
  tests/test_source_product_id_contract.py \
  tests/test_serving_source_review_stats.py \
  -q
```

- [ ] **Schema/repo alignment**

```bash
python -m pytest \
  tests/test_serving_profile_columns_align.py \
  tests/test_master_upsert_completeness.py \
  tests/test_db_contract_validator.py \
  -q
```

- [ ] **Full existing non-PG regression**

```bash
python -m pytest tests -q
```

- [ ] **Postgres integration**

```bash
bash scripts/run_postgres_integration.sh
```

- [ ] **Local DB smoke for `61289`**

After full load with source stats:

```sql
SELECT
  pm.product_id,
  pm.product_name,
  pm.representative_product_name,
  pm.brand_name,
  spp.source_review_count_6m,
  spp.source_avg_rating_6m,
  spp.source_review_count_all,
  spp.source_avg_rating_all,
  spp.review_count_all AS graph_review_support_all,
  spp.signal_support_count_all
FROM product_master pm
JOIN serving_product_profile spp USING(product_id)
WHERE pm.product_id = '61289'
  AND pm.is_active = true
  AND spp.is_active = true;
```

Expected shape:

```text
brand_name = 헤라
source_review_count_6m > 0
source_avg_rating_6m between 1 and 5
source_review_count_all >= source_review_count_6m
graph_review_support_all remains graph-derived and may be lower
```

Do not assert exact live Snowflake counts unless the source snapshot is fixed.

## Implementation Delegation Plan

Per `AGENTS.md`, implementation should be done by subagents after user approval. Main session owns review and integration.

Recommended parallel split:

1. **Agent A: Source Identity And Review Raw**
   - Owns Task 2 and Task 3.
   - Write scope: `review_ingest`, review loaders, `run_daily_pipeline`, matcher tests.

2. **Agent B: Product Truth And Mock Guardrails**
   - Owns Task 4 and Task 9.
   - Write scope: product loader/merge, mock synthesis, mock tests.

3. **Agent C: Source Review Stats And DB Persistence**
   - Owns Task 5 and Task 6.
   - Write scope: stats loader, DDL raw table, product repo, PG tests.

4. **Agent D: Serving Contract And Validator**
   - Owns Task 7 and Task 10.
   - Write scope: mart DDL/repo/schema/builder, validator, docs/query.

5. **Main Session**
   - Owns Task 8 integration across full/incremental.
   - Runs verification matrix.
   - Reviews subagent diffs for source contract drift.

No agent may edit another agent's write scope without reporting back first. No agent may reintroduce product-name brand heuristics.

## Stop Conditions

Stop and ask the user before proceeding if any of these happen:

- Source key mapping for a channel conflicts with `mockdata/SCHEMA_RS_JSONL.md`.
- Snowflake query cannot provide `prd_apal_scr` or brand for own `031`.
- Product truth merge would require inventing brand/category from names.
- A change would redefine existing `review_count_*` semantics.
- Full-load DB contract validator passes while `61289` still has promo-prefix brand.
- The same test failure class occurs twice; record it in `ERR_HIST/`.

## Self-Review

- **Spec coverage:** Covers product master, raw review identity/rating, graph serving output, source review volume/rating, mock contamination, and AmoreSimulation handoff.
- **Placeholder scan:** No open implementation placeholders. Unknown source data is represented as explicit NULL/quality state, not inferred.
- **Type consistency:** `source_review_count_*` and `source_avg_rating_*` names are consistent across loader, stats table, serving profile, and consumer query.
- **Backward compatibility:** Existing `review_count_*` and `signal_support_count_all` semantics are preserved.
- **Risk boundary:** Snowflake connector is not added to GraphRapping core dependency; SQL builder is pure and execution can be injected.
