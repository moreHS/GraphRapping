# P1-1 Serving / SQL Contract Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** in-memory serving/canonical objects에서 이미 생성하는 필드를 SQL DDL과 DB repository가 손실 없이 저장하게 한다.

**Architecture:** P1-1은 "이미 코드가 생성하는 필드"와 "DB가 저장하는 필드"의 동기화만 다룬다. `serving_product_profile`, `serving_user_profile`, `canonical_fact`, `fact_provenance`에 누락 컬럼을 추가하고 repo insert/update 경로를 맞춘다. signal-level promotion propagation과 `synthetic_ratio` correctness는 P1-3에서 별도 처리한다.

**Tech Stack:** Python 3.11, async repository code, SQL DDL, pytest.

---

## 현재 문제

### 1. Serving product profile field loss

`src/mart/build_serving_views.py::build_serving_product_profile()`는 아래 필드를 반환한다.

```python
"variant_family_id": product_master.get("variant_family_id"),
"representative_product_name": product_master.get("_es_meta", {}).get("REPRESENTATIVE_PROD_NAME"),
```

하지만 `sql/ddl_mart.sql::serving_product_profile`와 `src/db/repos/mart_repo.py::upsert_serving_product_profile()`는 이 필드를 저장하지 않는다.

영향:
- DB serving path를 타면 family-level personalization이 약해진다.
- UI/API가 `representative_product_name`을 기대할 때 DB profile에서는 값이 비어 있을 수 있다.

### 2. Serving user behavior field loss

`build_serving_user_profile()`는 아래 purchase/family 행동 필드를 반환한다.

```python
recent_purchase_brand_ids
repurchase_brand_ids
repurchase_category_ids
owned_product_ids
owned_family_ids
repurchased_family_ids
```

하지만 `serving_user_profile` DDL/repo는 선호 요약 필드만 저장한다.

영향:
- DB serving path에서 재구매/보유 제품/패밀리 기반 추천 feature가 사라진다.

### 3. Canonical fact metadata field loss

`CanonicalFact` dataclass는 아래 메타데이터를 가진다.

```python
negated
intensity
evidence_kind
fact_status
target_linked
attribution_source
```

하지만 `canonical_fact` DDL/repo insert/update는 `negated`, `intensity`만 qualifier로 우회 보존하고, 나머지 fact-level metadata는 저장하지 않는다.

영향:
- DB에서 fact를 재조회하거나 audit할 때 evidence-only/promoted 판단과 BEE attribution 상태를 잃는다.

### 4. Fact provenance source field loss

`FactProvenance` dataclass와 DDL에는 `source_domain`, `source_kind`가 있다. 하지만 `_replace_provenance()` insert SQL은 해당 컬럼을 쓰지 않아 DB default(`review`, `raw`)로 덮인다.

영향:
- user/product/manual/system provenance가 review/raw로 오염된다.

## 비목표

- `wrapped_signal`에 promotion metadata를 추가하고 aggregate `synthetic_ratio`를 고치는 작업은 P1-3에서 진행한다.
- SQL-first aggregate의 promotion 계산 누락은 P1-3에서 처리한다.
- 실제 Postgres integration test 환경을 새로 구성하지 않는다.
- 기존 dirty user edits는 되돌리지 않는다.

## 변경 파일

- Modify: `sql/ddl_mart.sql`
- Modify: `sql/ddl_canonical.sql`
- Modify: `src/db/repos/mart_repo.py`
- Modify: `src/db/repos/canonical_repo.py`
- Add: `tests/test_mart_repo_contract.py`
- Add: `tests/test_canonical_repo_contract.py`

## 설계

### 1. Mart DDL sync

`serving_product_profile` create table과 idempotent ALTER에 추가한다.

```sql
variant_family_id text,
representative_product_name text,
```

`serving_user_profile` create table과 idempotent ALTER에 추가한다.

```sql
recent_purchase_brand_ids jsonb,
repurchase_brand_ids jsonb,
repurchase_category_ids jsonb,
owned_product_ids jsonb,
owned_family_ids jsonb,
repurchased_family_ids jsonb,
```

### 2. Mart repo write sync

`upsert_serving_product_profile()` insert/update에 product fields를 추가한다.

Expected write keys:

```python
row.get("variant_family_id")
row.get("representative_product_name")
```

`upsert_serving_user_profile()` insert/update에 behavior fields를 추가한다.

Expected JSON keys:

```python
recent_purchase_brand_ids
repurchase_brand_ids
repurchase_category_ids
owned_product_ids
owned_family_ids
repurchased_family_ids
```

### 3. Canonical DDL sync

`canonical_fact` create table과 idempotent ALTER에 추가한다.

```sql
negated boolean,
intensity real,
evidence_kind text,
fact_status text not null default 'CANONICAL_PROMOTED',
target_linked boolean,
attribution_source text,
```

### 4. Canonical repo write sync

`_insert_fact()`, `_refresh_fact()`, `_reactivate_fact()`가 metadata를 쓴다.

Expected values:

```python
fact.negated
fact.intensity
fact.evidence_kind
fact.fact_status
fact.target_linked
fact.attribution_source
```

`_replace_provenance()`는 `FactProvenance.source_domain`, `source_kind`를 쓴다.

## Task 1: Mart contract tests

**Files:**
- Add: `tests/test_mart_repo_contract.py`

- [x] Step 1: Add source-level regression tests.

```python
import inspect

from src.db.repos import mart_repo


def test_serving_product_repo_writes_family_display_fields():
    src = inspect.getsource(mart_repo.upsert_serving_product_profile)
    assert "variant_family_id" in src
    assert "representative_product_name" in src


def test_serving_user_repo_writes_purchase_family_behavior_fields():
    src = inspect.getsource(mart_repo.upsert_serving_user_profile)
    for field in (
        "recent_purchase_brand_ids",
        "repurchase_brand_ids",
        "repurchase_category_ids",
        "owned_product_ids",
        "owned_family_ids",
        "repurchased_family_ids",
    ):
        assert field in src
```

- [x] Step 2: Run tests and verify failure before implementation.

```bash
python -m pytest tests/test_mart_repo_contract.py -q
```

Expected before implementation: fails because repo source does not contain all fields.

## Task 2: Mart DDL/repo sync

**Files:**
- Modify: `sql/ddl_mart.sql`
- Modify: `src/db/repos/mart_repo.py`

- [x] Step 1: Add product/user serving columns to DDL create table.

- [x] Step 2: Add idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for all new columns.

- [x] Step 3: Update `upsert_serving_product_profile()` insert column list, placeholders, update set, and args.

- [x] Step 4: Update `upsert_serving_user_profile()` insert column list, placeholders, update set, and args.

- [x] Step 5: Run mart contract tests.

```bash
python -m pytest tests/test_mart_repo_contract.py -q
```

Expected after implementation: pass.

## Task 3: Canonical repo contract tests

**Files:**
- Add: `tests/test_canonical_repo_contract.py`

- [x] Step 1: Add tests for canonical fact metadata and provenance source fields.

```python
import inspect

from src.db.repos import canonical_repo


def test_canonical_fact_repo_writes_fact_metadata():
    src = "\n".join(
        inspect.getsource(fn)
        for fn in (
            canonical_repo._insert_fact,
            canonical_repo._refresh_fact,
            canonical_repo._reactivate_fact,
        )
    )
    for field in (
        "negated",
        "intensity",
        "evidence_kind",
        "fact_status",
        "target_linked",
        "attribution_source",
    ):
        assert field in src


def test_fact_provenance_repo_writes_generic_source_fields():
    src = inspect.getsource(canonical_repo._replace_provenance)
    assert "source_domain" in src
    assert "source_kind" in src
```

- [x] Step 2: Run tests and verify failure before implementation.

```bash
python -m pytest tests/test_canonical_repo_contract.py -q
```

Expected before implementation: fails because repo source does not contain all metadata fields.

## Task 4: Canonical DDL/repo sync

**Files:**
- Modify: `sql/ddl_canonical.sql`
- Modify: `src/db/repos/canonical_repo.py`

- [x] Step 1: Add canonical metadata columns to `canonical_fact` create table.

- [x] Step 2: Add idempotent `ALTER TABLE canonical_fact ADD COLUMN IF NOT EXISTS ...` statements.

- [x] Step 3: Update `_insert_fact()` SQL and args to write metadata.

- [x] Step 4: Update `_refresh_fact()` SQL and args to refresh metadata on reprocess.

- [x] Step 5: Update `_reactivate_fact()` SQL and args to restore metadata on reactivation.

- [x] Step 6: Update `_replace_provenance()` SQL and args to write `source_domain`, `source_kind`.

- [x] Step 7: Run canonical contract tests.

```bash
python -m pytest tests/test_canonical_repo_contract.py -q
```

Expected after implementation: pass.

## Task 5: Verification

- [x] Run focused tests.

```bash
python -m pytest tests/test_mart_repo_contract.py tests/test_canonical_repo_contract.py -q
```

- [x] Run related semantic/provenance tests.

```bash
python -m pytest tests/test_phase1_semantic_preservation.py tests/test_signal_evidence_source_of_truth.py tests/test_serving_profile_promotion_gate.py tests/test_serving_uses_promoted_only.py -q
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
- `sql/ddl_mart.sql`
- `sql/ddl_canonical.sql`
- `src/db/repos/mart_repo.py`
- `src/db/repos/canonical_repo.py`
- `tests/test_mart_repo_contract.py`
- `tests/test_canonical_repo_contract.py`

Implemented:
- `serving_product_profile`에 `variant_family_id`, `representative_product_name`을 추가하고 repo insert/update에 연결했다.
- `serving_user_profile`에 purchase/family behavior fields 6개를 추가하고 repo insert/update에 연결했다.
- `canonical_fact`에 fact-level metadata 6개를 추가하고 insert/refresh/reactivate 경로에서 보존한다.
- `fact_provenance` 저장 경로가 `source_domain`, `source_kind`를 명시적으로 쓰도록 수정했다.
- `canonical_repo.py`의 미사용 `Any` import를 제거했다.

Focused tests:
- `python -m pytest tests/test_mart_repo_contract.py tests/test_canonical_repo_contract.py -q`
- Result: `4 passed`

Related tests:
- `python -m pytest tests/test_phase1_semantic_preservation.py tests/test_signal_evidence_source_of_truth.py tests/test_serving_profile_promotion_gate.py tests/test_serving_uses_promoted_only.py -q`
- Result: `32 passed`

Full tests:
- `python -m pytest tests/ -q`
- Result: `311 passed`

Static check:
- `python -m ruff check src/db/repos/mart_repo.py src/db/repos/canonical_repo.py tests/test_mart_repo_contract.py tests/test_canonical_repo_contract.py`
- Result: `All checks passed!`
- `python -m ruff check src --statistics`
- Result: `74 errors` remain globally (`40 F401`, `29 E402`, `4 E741`, `1 F541`)

Remaining issues:
- `wrapped_signal`에는 아직 `evidence_kind`/promotion metadata가 없어 aggregate `synthetic_ratio`는 P1-3에서 다룬다.
- 실제 Postgres migration/persist integration test는 아직 없다.
- Contract tests는 현재 DB 없이 source-level regression으로 동작한다.

Next priority:
- P1-2 rs.jsonl relation-ready contract 공식화

## Rollback / safety

If any DDL column creates compatibility risk:
- keep the column nullable except `fact_status`, which has a default.
- preserve idempotent `ALTER TABLE ... IF NOT EXISTS`.

If repository placeholder count becomes error-prone:
- prefer a focused helper in `mart_repo.py` or `canonical_repo.py` only if tests show repeated mistakes.
