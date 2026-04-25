# P0-2 Quarantine Batch/Web Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** per-review quarantine entries가 batch result, demo state, dashboard API에서 일관되게 집계되도록 복구한다.

**Architecture:** `process_review()`는 지금처럼 review bundle에 quarantine entries를 담는다. `run_batch()`와 `load_demo_data()`는 shared `QuarantineHandler.pending_count`가 아니라 이미 flush된 `bundle.quarantine_entries`를 source of truth로 사용한다.

**Tech Stack:** Python 3.11, pytest, existing in-memory demo state.

---

## 현재 문제

`process_review()`는 review 단위 bundle 생성 시 `quarantine.flush()`를 호출한다.

```python
bundle = ReviewPersistBundle(
    ...
    quarantine_entries=quarantine.flush(),
)
```

따라서 `run_batch()`가 마지막에 보는 `quarantine.pending_count`는 이미 비어 있을 수 있다.

현재 증상:
- `review_results[*].quarantine_count` 합은 존재한다.
- `batch_result["total_quarantined"]`는 0이다.
- `demo_state.quarantine_stats`는 `{}`이다.
- `/api/quarantine/summary`, `/api/quarantine/entries`는 비어 보인다.

## 비목표

- quarantine table schema는 수정하지 않는다.
- DB persistence path의 `quarantine_repo.flush_quarantine()`는 수정하지 않는다.
- product matching 자체는 P0-1에서 이미 처리했으므로 여기서는 다루지 않는다.

## 변경 파일

- Modify: `src/jobs/run_daily_pipeline.py`
- Modify: `src/web/state.py`
- Add: `tests/test_quarantine_batch_summary.py`
- Modify: `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`
- Modify: this decision plan completion record

## 설계

### 1. Batch source of truth

`run_batch()`는 처리 중 만든 `all_bundles`를 이미 들고 있다.

수정:

```python
all_quarantine_entries = [
    entry
    for bundle in all_bundles
    for entry in bundle.quarantine_entries
]
total_quarantined = len(all_quarantine_entries)
quarantine_by_table = _quarantine_counts_by_table(all_quarantine_entries)
```

`batch_result`에 아래 값을 추가한다.

```python
"total_quarantined": total_quarantined,
"quarantine_by_table": quarantine_by_table,
"quarantine_entries": all_quarantine_entries,
```

주의:
- `quarantine_entries`는 `QuarantineEntry` 객체 list로 둔다.
- web state가 dict 변환을 담당한다.
- 기존 `review_results[*].quarantine_count`는 그대로 유지한다.

### 2. Web state source of truth

`load_demo_data()`는 더 이상 `quarantine.pending_by_table()`와 `quarantine.flush()`를 보지 않는다.

수정:

```python
batch_entries = batch_result.get("quarantine_entries", [])
demo_state.quarantine_stats = batch_result.get("quarantine_by_table", {})
demo_state.quarantine_entries = [
    {"table": e.table, **e.data} for e in batch_entries
]
```

fallback:
- 혹시 old batch_result에는 `quarantine_entries`가 없을 수 있으므로, review_results의 count 합을 쓸 수도 있지만 entries API를 위해서는 bundle entries가 필요하다.
- 이 프로젝트 내부에서는 `run_batch()`와 같이 고치므로 fallback은 최소화한다.

### 3. Tests

새 테스트는 product mismatch를 의도적으로 만들어 quarantine을 발생시킨다.

이유:
- P0-1 이후 기본 mock은 더 이상 product_match quarantine만으로 쉽게 실패하지 않는다.
- 의도적 mismatch fixture가 가장 작고 안정적이다.

검증할 것:
- `run_batch()["total_quarantined"] > 0`
- `run_batch()["quarantine_by_table"]["quarantine_product_match"] > 0`
- `len(run_batch()["quarantine_entries"]) == total_quarantined`
- `load_demo_data()`로 web state를 만들었을 때 `demo_state.quarantine_stats`와 `demo_state.quarantine_entries`가 비어 있지 않다.

## Task 1: Add failing batch quarantine test

**Files:**
- Add: `tests/test_quarantine_batch_summary.py`

- [ ] Step 1: Build a small product/user/review fixture.

Use checked-in loaders where possible:
- products: one active mock product from `product_catalog_es.json`
- users: one normalized mock user from `user_profiles_normalized.json`
- reviews: `RawReviewRecord` with unknown brand/product and no relations

- [ ] Step 2: Assert `run_batch()` reports bundle quarantine in batch totals.

Expected test body:

```python
def test_run_batch_counts_flushed_bundle_quarantine_entries():
    ...
    result = run_batch(...)
    assert result["total_quarantined"] > 0
    assert result["quarantine_by_table"]["quarantine_product_match"] > 0
    assert len(result["quarantine_entries"]) == result["total_quarantined"]
```

- [ ] Step 3: Run test and confirm it fails before implementation.

Run:

```bash
python -m pytest tests/test_quarantine_batch_summary.py -q
```

Expected:
- Fails because `total_quarantined` is 0 or `quarantine_by_table` is missing.

## Task 2: Fix `run_batch()` aggregation

**Files:**
- Modify: `src/jobs/run_daily_pipeline.py`

- [ ] Step 1: Add helper.

```python
def _quarantine_counts_by_table(entries) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.table] = counts.get(entry.table, 0) + 1
    return counts
```

- [ ] Step 2: Replace `total_quarantined = quarantine.pending_count`.

Use `all_bundles`:

```python
all_quarantine_entries = [
    entry
    for bundle in all_bundles
    for entry in bundle.quarantine_entries
]
total_quarantined = len(all_quarantine_entries)
```

- [ ] Step 3: Return new fields.

```python
"quarantine_by_table": _quarantine_counts_by_table(all_quarantine_entries),
"quarantine_entries": all_quarantine_entries,
```

## Task 3: Fix demo state aggregation

**Files:**
- Modify: `src/web/state.py`

- [ ] Step 1: Replace pending handler read.

Old:

```python
demo_state.quarantine_stats = quarantine.pending_by_table()
demo_state.quarantine_entries = [
    {"table": e.table, **e.data} for e in quarantine.flush()
]
```

New:

```python
batch_quarantine_entries = batch_result.get("quarantine_entries", [])
demo_state.quarantine_stats = batch_result.get("quarantine_by_table", {})
demo_state.quarantine_entries = [
    {"table": e.table, **e.data} for e in batch_quarantine_entries
]
```

- [ ] Step 2: Keep `quarantine` object creation for `run_batch()` call compatibility.

Do not remove `quarantine = QuarantineHandler()` yet.

## Task 4: Add web state regression test

**Files:**
- Modify: `tests/test_quarantine_batch_summary.py`

- [ ] Step 1: Add `load_demo_data()` regression.

Create a temporary review JSON with unknown product.

Expected:

```python
assert state.quarantine_stats.get("quarantine_product_match", 0) > 0
assert len(state.quarantine_entries) > 0
```

## Task 5: Verification

- [ ] Run focused tests.

```bash
python -m pytest tests/test_quarantine_batch_summary.py -q
```

- [ ] Run related smoke tests.

```bash
python -m pytest tests/test_mock_pipeline_smoke.py tests/test_product_matcher.py -q
```

- [ ] Run full suite.

```bash
python -m pytest tests/ -q
```

- [ ] Run static check as observation.

```bash
python -m ruff check src --statistics
```

## Completion Record

Date: 2026-04-25

Changed files:
- `src/jobs/run_daily_pipeline.py`
- `src/web/state.py`
- `tests/test_quarantine_batch_summary.py`
- `DECISIONS/2026-04-25_p0_2_quarantine_batch_web_summary_plan.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

Implemented:
- `run_batch()` now aggregates quarantine entries from per-review bundles.
- `batch_result` now exposes `quarantine_by_table` and `quarantine_entries`.
- `load_demo_data()` now uses `batch_result` as the quarantine source of truth after `run_batch()`.
- Added regression tests for batch summary and web demo state.

Focused tests:
- `python -m pytest tests/test_quarantine_batch_summary.py -q` -> 2 passed
- `python -m pytest tests/test_mock_pipeline_smoke.py tests/test_product_matcher.py -q` -> 12 passed

Full tests:
- `python -m pytest tests/ -q` -> 302 passed

Static check:
- `python -m ruff check src --statistics` -> 87 errors remain
  - 45 F401 unused-import
  - 37 E402 module-import-not-at-top-of-file
  - 4 E741 ambiguous-variable-name
  - 1 F541 f-string-missing-placeholders

Remaining issues:
- P0-3 migration/incremental correctness still needs separate fix.
- ruff cleanup remains outside P0-2 scope.

Next priority:
- P0-3 Operational DB/incremental correctness.

## Rollback / safety

If API payload size becomes too large because `batch_result["quarantine_entries"]` holds all entries:
- Keep `quarantine_entries` only in in-memory demo path, or
- add a `include_quarantine_entries: bool = True` flag to `run_batch()`.

For current mock/demo scale this is acceptable and simpler.
