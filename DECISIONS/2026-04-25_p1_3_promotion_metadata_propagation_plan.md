# P1-3 Promotion Metadata Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** canonical fact의 promotion/evidence metadata가 wrapped signal과 aggregate까지 보존되어 `synthetic_ratio`, corpus promotion, audit 판단이 실제 입력을 반영하게 한다.

**Architecture:** P1-1에서 DB canonical fact metadata 저장을 복구했으므로, P1-3은 fact -> signal -> aggregate 경로를 맞춘다. `WrappedSignal`에 metadata 필드를 추가하고, `signal_repo`/`ddl_signal.sql`이 이를 저장한다. `aggregate_product_signals()`가 이미 읽는 `evidence_kind`가 실제 signal dict에 존재하도록 `_signal_to_dict()`와 DB row schema를 동기화한다.

**Tech Stack:** Python 3.11, dataclasses, SQL DDL, pytest.

---

## 현재 문제

### 1. WrappedSignal metadata loss

`CanonicalFact`는 아래 필드를 가진다.

```python
evidence_kind
fact_status
target_linked
attribution_source
confidence
```

하지만 `WrappedSignal`에는 해당 필드가 없어 signal로 내려가며 손실된다.

### 2. aggregate synthetic_ratio dead path

`aggregate_product_signals()`는 아래처럼 `evidence_kind`를 읽는다.

```python
synthetic_count = sum(1 for s in window_sigs if s.get("evidence_kind") == "BEE_SYNTHETIC")
```

하지만 `_signal_to_dict()`와 `wrapped_signal` DB row가 `evidence_kind`를 제공하지 않아, 실제 파이프라인에서는 `synthetic_ratio`가 0으로 고정될 수 있다.

### 3. signal DB schema/repo mismatch

`signal_repo.replace_signals_for_review()`는 signal metadata를 저장하지 않는다. DB incremental aggregate는 `SELECT * FROM wrapped_signal`을 기준으로 재집계하므로 DB row에도 metadata가 있어야 한다.

## 비목표

- corpus promotion threshold 자체는 바꾸지 않는다.
- relation 없는 BEE를 승격하지 않는다.
- projection registry CSV를 대규모 재설계하지 않는다.
- SQL-first aggregate의 post-hoc promotion 계산은 별도 최적화로 남긴다.

## 변경 파일

- Modify: `src/wrap/signal_emitter.py`
- Modify: `src/wrap/projection_registry.py`
- Modify: `src/jobs/run_daily_pipeline.py`
- Modify: `src/jobs/run_incremental_pipeline.py`
- Modify: `src/db/repos/signal_repo.py`
- Modify: `sql/ddl_signal.sql`
- Add: `tests/test_signal_metadata_propagation.py`
- Add: `tests/test_signal_repo_contract.py`

## 설계

### 1. WrappedSignal metadata fields

`WrappedSignal`에 아래 필드를 추가한다.

```python
evidence_kind: str | None = None
fact_status: str = "CANONICAL_PROMOTED"
source_confidence: float | None = None
target_linked: bool | None = None
attribution_source: str | None = None
```

`SignalEmitter.emit_from_fact()`가 `CanonicalFact`에서 값을 복사한다.

### 2. Signal dict propagation

`run_daily_pipeline._signal_to_dict()`가 metadata를 포함한다.

```python
"evidence_kind": signal.evidence_kind,
"fact_status": signal.fact_status,
"source_confidence": signal.source_confidence,
"target_linked": signal.target_linked,
"attribution_source": signal.attribution_source,
```

### 3. Aggregate dict completeness

`_agg_to_dict()`는 DB persist에 필요한 corpus fields를 모두 포함한다.

```python
distinct_review_count
avg_confidence
synthetic_ratio
corpus_weight
is_promoted
```

### 4. Signal DB sync

`wrapped_signal` DDL과 `signal_repo.replace_signals_for_review()`에 metadata columns를 추가한다.

```sql
evidence_kind text,
fact_status text not null default 'CANONICAL_PROMOTED',
source_confidence real,
target_linked boolean,
attribution_source text,
```

## Task 1: Signal metadata tests

**Files:**
- Add: `tests/test_signal_metadata_propagation.py`

- [x] Step 1: Add test that `SignalEmitter` copies fact metadata to signal.

- [x] Step 2: Add test that `_signal_to_dict()` exposes `evidence_kind`.

- [x] Step 3: Add test that aggregate `synthetic_ratio` is non-zero when signal dict has `BEE_SYNTHETIC`.

## Task 2: Signal metadata implementation

**Files:**
- Modify: `src/wrap/signal_emitter.py`
- Modify: `src/wrap/projection_registry.py`
- Modify: `src/jobs/run_daily_pipeline.py`
- Modify: `src/jobs/run_incremental_pipeline.py`

- [x] Step 1: Add fields to `WrappedSignal`.

- [x] Step 2: Set fields in `emit_from_fact()`.

- [x] Step 3: Include fields in `_signal_to_dict()`.

- [x] Step 4: Include all corpus fields in both daily and incremental `_agg_to_dict()`.

- [x] Step 5: Enforce optional `allowed_evidence_kind` and `min_confidence` gates in `ProjectionRegistry.project()`.

## Task 3: Signal repo contract

**Files:**
- Add: `tests/test_signal_repo_contract.py`
- Modify: `sql/ddl_signal.sql`
- Modify: `src/db/repos/signal_repo.py`

- [x] Step 1: Add source-level test that signal repo writes metadata fields.

- [x] Step 2: Add DDL columns and idempotent ALTERs.

- [x] Step 3: Update insert column list, placeholders, and args.

## Task 4: Verification

- [x] Run focused tests.

```bash
python -m pytest tests/test_signal_metadata_propagation.py tests/test_signal_repo_contract.py -q
```

- [x] Run related tests.

```bash
python -m pytest tests/test_phase1_semantic_preservation.py tests/test_signal_evidence_source_of_truth.py tests/test_serving_profile_promotion_gate.py -q
```

- [x] Run full test suite.

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
- `src/wrap/signal_emitter.py`
- `src/wrap/projection_registry.py`
- `src/jobs/run_daily_pipeline.py`
- `src/jobs/run_incremental_pipeline.py`
- `src/db/repos/signal_repo.py`
- `sql/ddl_signal.sql`
- `tests/test_signal_metadata_propagation.py`
- `tests/test_signal_repo_contract.py`

Implemented:
- `WrappedSignal`에 `evidence_kind`, `fact_status`, `source_confidence`, `target_linked`, `attribution_source`를 추가했다.
- `SignalEmitter`가 `CanonicalFact` metadata를 signal로 복사한다.
- `_signal_to_dict()`가 signal metadata를 노출해 in-memory aggregate가 `evidence_kind`를 읽을 수 있게 했다.
- daily/incremental `_agg_to_dict()`가 corpus promotion fields를 모두 보존한다.
- `wrapped_signal` DDL/repo에 metadata columns를 추가했다.
- `ProjectionRegistry.project()`가 optional `allowed_evidence_kind`, `min_confidence` gate를 적용한다.
- 수정 파일 범위의 기존 import lint도 함께 정리했다.

Focused tests:
- `python -m pytest tests/test_signal_metadata_propagation.py tests/test_signal_repo_contract.py -q`
- Result: `6 passed`

Related tests:
- `python -m pytest tests/test_signal_metadata_propagation.py tests/test_signal_repo_contract.py tests/test_phase1_semantic_preservation.py tests/test_signal_evidence_source_of_truth.py tests/test_serving_profile_promotion_gate.py -q`
- Result: `34 passed`

Full tests:
- `python -m pytest tests/ -q`
- Result: `320 passed`

Static check:
- `python -m ruff check src/wrap/projection_registry.py src/wrap/signal_emitter.py src/jobs/run_daily_pipeline.py src/jobs/run_incremental_pipeline.py src/db/repos/signal_repo.py tests/test_signal_metadata_propagation.py tests/test_signal_repo_contract.py`
- Result: `All checks passed!`
- `python -m ruff check src --statistics`
- Result: `45 errors` remain globally (`30 F401`, `10 E402`, `4 E741`, `1 F541`)

Remaining issues:
- SQL-first aggregate path still does not compute promotion fields in SQL; Python aggregate path preserves them.
- `signal_evidence` remains the exact fact-level source of truth when multiple facts merge into one signal.

Next priority:
- P2-1 추천 mode/scoring/UI/docs 정합성 정리

## Rollback / safety

If signal metadata merge semantics become ambiguous:
- keep first non-empty metadata value for now.
- rely on `signal_evidence` for exact fact-level provenance.
