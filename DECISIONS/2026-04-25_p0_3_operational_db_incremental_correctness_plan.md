# P0-3 Operational DB / Incremental Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB migration bootstrap, DB raw snapshot reverse transform, incremental persistence path를 운영 가능한 correctness 기준으로 복구한다.

**Architecture:** Migration은 `schema_migrations` bootstrap 순서를 보장한다. Review repo는 DB child rows를 loader input contract에 맞는 `RawReviewRecord` payload로 복원한다. Incremental pipeline은 `process_review()` 결과를 반드시 `persist_review_bundle()`로 저장한 뒤 dirty aggregate를 갱신한다.

**Tech Stack:** Python 3.11, async repository code, pytest.

---

## 현재 문제

### 1. Migration bootstrap 순서

`src/db/migrate.py`는 `ddl_raw.sql`부터 실행한 뒤 매 파일마다 `schema_migrations`에 insert한다.

하지만 `schema_migrations` table은 `ddl_ops.sql`에서 생성된다. 현재 순서에서는 첫 파일 적용 기록 시점에 table이 없을 수 있다.

### 2. DB snapshot -> RawReviewRecord reverse transform

`src/db/repos/review_repo.py::load_full_review_snapshot()`는 DB child row를 아래처럼 복원한다.

```python
{"mention_text": ..., "start_offset": ...}
{"phrase_text": ..., "bee_attr_raw": ...}
{"subj_text": ..., "relation_raw": ...}
```

하지만 `RawReviewRecord`와 loader 입력 계약은 아래 shape다.

```python
ner: [{"word": ..., "entity_group": ..., "start": ..., "end": ..., "sentiment": ...}]
bee: [{"word": ..., "entity_group": ..., "start": ..., "end": ..., "sentiment": ...}]
relation: [{"subject": {...}, "object": {...}, "relation": ..., "source_type": ...}]
```

이 불일치 때문에 incremental reprocess에서 NER/BEE/REL이 빈 값처럼 처리될 수 있다.

### 3. rel_raw metadata 손실

`review_ingest`는 rel row에 `obj_keywords`를 만든다. KG mode는 NER-BeE keyword 추출에 이 필드를 쓴다.

현재 `rel_raw` DDL/repo insert는 `obj_keywords`와 `raw_sentiment`를 저장하지 않는다.

### 4. Incremental persist 누락

`run_incremental_pipeline.py`는 `persist_review_bundle`를 import하지만 실제 호출하지 않는다.

결과:
- reprocess 결과가 DB에 저장되지 않는다.
- `canonical_fact`, `wrapped_signal`, `signal_evidence`, quarantine이 갱신되지 않는다.
- dirty aggregate가 stale signal 기준으로 재계산될 수 있다.

## 비목표

- 실제 Postgres integration test 환경을 새로 구성하지 않는다.
- aggregate SQL-first 최적화는 이 작업에서 하지 않는다.
- tombstone 정책 전체 재설계는 하지 않는다.
- KG mode 기본값 변경은 하지 않는다.

## 변경 파일

- Modify: `src/db/migrate.py`
- Modify: `sql/ddl_raw.sql`
- Modify: `src/db/repos/review_repo.py`
- Modify: `src/jobs/run_incremental_pipeline.py`
- Add: `tests/test_db_migration_order.py`
- Add: `tests/test_review_snapshot_reverse_transform.py`
- Add or modify: incremental pipeline unit test if feasible without real DB

## 설계

### 1. Migration order

최소 수정:
- `DDL_ORDER`에서 `ddl_ops.sql`을 가장 앞에 둔다.

변경 전:

```python
DDL_ORDER = [
    "ddl_raw.sql",
    ...
    "ddl_ops.sql",
]
```

변경 후:

```python
DDL_ORDER = [
    "ddl_ops.sql",
    "ddl_raw.sql",
    ...
]
```

이렇게 하면 `schema_migrations`가 먼저 생성된다.

### 2. rel_raw schema preservation

`sql/ddl_raw.sql`에 아래 컬럼을 추가한다.

```sql
raw_sentiment text,
obj_keywords jsonb,
```

기존 DB에도 안전하게 적용되도록 ALTER도 추가한다.

```sql
ALTER TABLE rel_raw ADD COLUMN IF NOT EXISTS raw_sentiment text;
ALTER TABLE rel_raw ADD COLUMN IF NOT EXISTS obj_keywords jsonb;
```

`batch_insert_rel_raw()`는 해당 값을 insert한다.

### 3. Reverse transform helpers

`review_repo.py`에 순수 helper를 추가한다.

```python
def _db_ner_to_record_item(row: dict) -> dict:
    return {
        "word": row.get("mention_text", ""),
        "entity_group": row.get("entity_group", ""),
        "start": row.get("start_offset"),
        "end": row.get("end_offset"),
        "sentiment": row.get("raw_sentiment"),
    }
```

동일하게 BEE/REL helper를 둔다.

REL:

```python
{
    "subject": {
        "word": row.get("subj_text", ""),
        "entity_group": row.get("subj_group", ""),
        "start": row.get("subj_start"),
        "end": row.get("subj_end"),
    },
    "object": {
        "word": row.get("obj_text", ""),
        "entity_group": row.get("obj_group", ""),
        "start": row.get("obj_start"),
        "end": row.get("obj_end"),
        "keywords": row.get("obj_keywords") or [],
    },
    "relation": row.get("relation_raw", ""),
    "source_type": row.get("source_type"),
}
```

`load_full_review_snapshot()`는 이 helper들을 사용한다.

### 4. Incremental persist

`run_incremental()`에서 `process_review()` 후 반드시 persist한다.

```python
bundle = process_review(...)
persist_stats = await persist_review_bundle(pool, bundle)
```

집계:
- `total_signals += len(bundle.wrapped_signals)`
- `total_quarantined += len(bundle.quarantine_entries)`
- `all_dirty_products.update(persist_stats.get("dirty_product_ids", []))`
- `all_dirty_products.update(bundle.dirty_product_ids)`

기존 `quarantine.pending_count` 사용은 제거한다.

### 5. Tests

DB 없는 단위 테스트:
- migration order test
- DB row -> RawReviewRecord item helper test
- rel row includes `keywords`

DB integration은 별도 P1/Postgres 환경에서 처리한다.

## Task 1: Migration order test and fix

**Files:**
- Modify: `src/db/migrate.py`
- Add: `tests/test_db_migration_order.py`

- [x] Step 1: Add test.

```python
from src.db.migrate import DDL_ORDER


def test_ddl_ops_runs_before_migration_records_are_written():
    assert DDL_ORDER[0] == "ddl_ops.sql"
```

- [x] Step 2: Run failing test.

```bash
python -m pytest tests/test_db_migration_order.py -q
```

- [x] Step 3: Move `ddl_ops.sql` to first item in `DDL_ORDER`.

- [x] Step 4: Run test again.

## Task 2: Preserve rel metadata in DDL/repo

**Files:**
- Modify: `sql/ddl_raw.sql`
- Modify: `src/db/repos/review_repo.py`

- [x] Step 1: Add `raw_sentiment` and `obj_keywords` to rel_raw create table.

- [x] Step 2: Add idempotent ALTER columns.

- [x] Step 3: Update `batch_insert_rel_raw()` insert SQL and params.

Expected insert column list:

```sql
relation_raw, relation_canonical, source_type, raw_sentiment, obj_keywords
```

## Task 3: Reverse transform tests and helpers

**Files:**
- Modify: `src/db/repos/review_repo.py`
- Add: `tests/test_review_snapshot_reverse_transform.py`

- [x] Step 1: Add helper tests.

Expected:

```python
from src.db.repos.review_repo import (
    _db_ner_to_record_item,
    _db_bee_to_record_item,
    _db_rel_to_record_item,
)


def test_db_ner_to_record_item_uses_loader_contract_keys():
    row = {"mention_text": "이 제품", "entity_group": "PRD", "start_offset": 0, "end_offset": 3, "raw_sentiment": "중립"}
    assert _db_ner_to_record_item(row) == {
        "word": "이 제품",
        "entity_group": "PRD",
        "start": 0,
        "end": 3,
        "sentiment": "중립",
    }
```

- [x] Step 2: Implement helpers.

- [x] Step 3: Update `load_full_review_snapshot()` to use helpers.

## Task 4: Incremental persistence fix

**Files:**
- Modify: `src/jobs/run_incremental_pipeline.py`

- [x] Step 1: Rename `result` to `bundle` after `process_review()`.

- [x] Step 2: Add `persist_review_bundle(pool, bundle)` call.

- [x] Step 3: Replace quarantine total calculation.

Old:

```python
total_quarantined += quarantine.pending_count
```

New:

```python
total_quarantined += len(bundle.quarantine_entries)
```

- [x] Step 4: Use returned dirty products.

```python
persist_stats = await persist_review_bundle(pool, bundle)
all_dirty_products.update(persist_stats.get("dirty_product_ids", []))
```

## Task 5: Verification

- [x] Run focused tests.

```bash
python -m pytest tests/test_db_migration_order.py tests/test_review_snapshot_reverse_transform.py -q
```

- [x] Run existing incremental-related tests.

```bash
python -m pytest tests/ -q
```

- [x] Run static check as observation.

```bash
python -m ruff check src --statistics
```

## Completion Record

Date: 2026-04-25

Changed files:
- `src/db/migrate.py`
- `sql/ddl_raw.sql`
- `src/ingest/review_ingest.py`
- `src/db/repos/review_repo.py`
- `src/jobs/run_incremental_pipeline.py`
- `tests/test_db_migration_order.py`
- `tests/test_review_snapshot_reverse_transform.py`

Implemented:
- `ddl_ops.sql`를 DDL_ORDER 첫 순서로 이동해 `schema_migrations` 기록 table을 먼저 만든다.
- `rel_raw`에 `raw_sentiment`, `obj_keywords jsonb`를 추가하고 기존 DB용 `ALTER TABLE ... IF NOT EXISTS`를 추가했다.
- relation object sentiment/keywords가 ingest -> rel_raw -> DB snapshot reverse transform 경로에서 유지되도록 했다.
- DB NER/BEE/REL child rows를 `RawReviewRecord` loader contract shape으로 복원하는 helper를 추가했다.
- incremental reprocess 후 `persist_review_bundle(pool, bundle)`를 호출해 L1/L2/L2.5/QA를 실제 저장하도록 연결했다.
- relink dirty product를 놓치지 않도록 저장 전 기존 `review_catalog_link`를 읽고 저장 후 dirty set에 반영한다.
- tombstone 처리 성공 시에도 watermark 후보로 인정되도록 `last_processed_review`를 갱신한다.

Focused tests:
- `python -m pytest tests/test_db_migration_order.py tests/test_review_snapshot_reverse_transform.py -q`
- Result: `5 passed`

Full tests:
- `python -m pytest tests/ -q`
- Result: `307 passed`

Static check:
- `python -m ruff check src/db/migrate.py src/db/repos/review_repo.py src/ingest/review_ingest.py src/jobs/run_incremental_pipeline.py tests/test_db_migration_order.py tests/test_review_snapshot_reverse_transform.py`
- Result: `All checks passed!`
- `python -m ruff check src --statistics`
- Result: `75 errors` remain globally (`41 F401`, `29 E402`, `4 E741`, `1 F541`)

Remaining issues:
- 실제 Postgres integration 환경에서 migration/persist를 실행하는 검증은 아직 없다.
- `review_raw`에는 원래 `author_key` 컬럼이 없으므로 DB snapshot에서 `author_key`는 복원하지 않는다.
- 전체 ruff 잔여 이슈는 P0-3 범위 밖이다.

Next priority:
- P1-1 In-memory serving contract와 SQL DDL/repo 동기화

## Rollback / safety

If moving `ddl_ops.sql` first breaks dependency assumptions:
- Split schema_migrations bootstrap into a dedicated SQL statement before the loop.
- Keep DDL_ORDER otherwise unchanged.

If storing `obj_keywords` increases DB payload unexpectedly:
- Keep JSONB column nullable.
- Only write non-empty keyword lists.
