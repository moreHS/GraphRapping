# Sprint 4 Post-Implementation Hotfix Plan v3 (GPT APPROVE 대기)

## Context
Sprint 4 구현 완료 (96 tests, 50 modules, ~8.8K LOC) 후 크로스 리뷰.
GPT 2회 REJECT → v3에서 2개 blocker spec 반영.

## FIX-1. ★ Incremental pipeline persistence (Critical)

### 1a. process_review → bundle-returning 분리 (GPT spec)
`src/jobs/run_daily_pipeline.py` 수정:
- `build_review_persist_bundle(record, source, ...) -> ReviewPersistBundle` — 신규
- `_build_review_persist_bundle_from_ingested(ingested, ...) -> ReviewPersistBundle` — 핵심 로직
- `process_review(...)` → thin wrapper: `build_review_persist_bundle()` + `_bundle_to_review_result()`
- 기존 summary dict 호환성 유지

Bundle 매핑:
```
review_raw = ingested.review_raw
review_catalog_link = {review_id, source_brand, source_product_name, matched_product_id, match_status, match_score, match_method}
ner_rows/bee_rows/rel_rows = ingested.*
canonical_entities/facts = builder.*
wrapped_signals = emit_result.signals
signal_evidence_rows = emit_result.evidence_rows
quarantine_entries = quarantine.flush()
dirty_product_ids = {target_product_id}
```

REL loop에서 `rel_row["relation_canonical"] = canon_result.canonical_predicate` 설정.
`QuarantineHandler.extend(entries)` 메서드 추가.

### 1b. REL offsets DDL + repo 수정 (GPT spec)
`sql/ddl_raw.sql` — rel_raw에 4개 컬럼 추가:
```sql
subj_start int, subj_end int, obj_start int, obj_end int
-- + ALTER TABLE ADD COLUMN IF NOT EXISTS (idempotent upgrade)
```

`src/db/repos/review_repo.py:batch_insert_rel_raw()` — INSERT에 offset 4개 포함.
ingest는 이미 offset 생성 중 (review_ingest.py:165) → 변경 불필요.

### 1c. DB loader for incremental replay (GPT spec)
`src/db/repos/review_repo.py`에 추가:
```python
async def load_ingested_review_snapshot(review_id, review_version=None) -> IngestedReview:
    # review_raw (latest or specified version)
    # ner_raw/bee_raw/rel_raw WHERE (review_id, review_version) — offset 포함
    # → IngestedReview 재구성
```

### 1d. Incremental pipeline 수정
`src/jobs/run_incremental_pipeline.py` 수정:
- 변경 review → `load_ingested_review_snapshot()` → `_build_review_persist_bundle_from_ingested()` → `persist_review_bundle()`
- tombstone는 기존 handle_tombstone() 유지
- dirty products → re-aggregate

### 1e. L1 child row idempotency
`batch_insert_*` 전에 해당 (review_id, review_version) 존재 여부 체크:
```python
existing = await uow.fetchval(
    "SELECT COUNT(*) FROM ner_raw WHERE review_id=$1 AND review_version=$2",
    review_id, version)
if existing > 0:
    return  # skip — already inserted for this version
```

## FIX-2. signal_repo ON CONFLICT 명시
`src/db/repos/signal_repo.py:64`:
```sql
ON CONFLICT (signal_id, fact_id, evidence_rank) DO NOTHING
```

## FIX-3. product_repo 함수명 중복 해소
`src/db/repos/product_repo.py:60`: `upsert_canonical_entity` → `upsert_product_entity`
+ 호출 코드 (`persist.py` 등) 일괄 rename.

## FIX-4. mart_repo import 정리
`import json`을 module level로 이동.

## FIX-5. review_repo type hint
`_append_history(... as_of_ts: datetime)` type hint 추가.
`from datetime import datetime` import 추가.

## FIX-6. hasattr 제거
`run_daily_pipeline.py:298`: `hasattr` → `emit_result.evidence_rows` 직접 접근.

## 수정 순서
1. rel_raw DDL 수정 (1b)
2. review_repo: offset insert + load_ingested_review_snapshot + L1 idempotency (1c, 1e)
3. QuarantineHandler.extend() 추가
4. run_daily_pipeline: bundle 분리 (1a)
5. run_incremental_pipeline: persist 경로 (1d)
6. FIX-2~6 일괄
7. 테스트

## 검증 체크리스트
- [ ] 기존 96 tests 유지
- [ ] build_review_persist_bundle() → ReviewPersistBundle 정상 반환
- [ ] process_review() → 기존 summary dict 호환 유지
- [ ] rel_raw에 subj_start/end, obj_start/end INSERT 정상
- [ ] load_ingested_review_snapshot() → IngestedReview with offsets
- [ ] incremental: load → bundle → persist_review_bundle() 호출 확인
- [ ] incremental: dirty products re-aggregate 확인
- [ ] L1 child row: 같은 (review_id, version) 2회 → 중복 없음
- [ ] signal_evidence ON CONFLICT 명시 확인
- [ ] product_repo rename → persist.py 호출 정상
