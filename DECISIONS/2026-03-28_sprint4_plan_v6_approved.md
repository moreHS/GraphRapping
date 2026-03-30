# Sprint 4+α 상세 계획 v2

## Context
Sprint 1-3 + Post-audit fix 완료 (92 tests, 35 modules, ~7K LOC). 현재: "in-memory reference implementation".
다음 우선순위: **DB 영속화 > Provenance wiring > Incremental > Dictionary growth**.
GPT Plan Reviewer v1 REJECT 5개 사유 반영.

---

## Sprint 4A: DB Persistence Layer

### 4A-0. ★ Persistence Contract 정의 (v2 추가)
`process_review()` 반환값을 DB write 가능한 명시적 artifact bundle로 확장.

**per-review artifact bundle**:
```python
@dataclass
class ReviewPersistBundle:
    # Layer 1
    review_raw: dict
    review_catalog_link: dict
    ner_rows: list[dict]
    bee_rows: list[dict]
    rel_rows: list[dict]
    # Layer 2
    canonical_entities: list[CanonicalEntity]
    canonical_facts: list[CanonicalFact]    # includes provenance + qualifiers
    # Layer 2.5
    wrapped_signals: list[WrappedSignal]
    signal_evidence_rows: list[dict]
    # QA
    quarantine_entries: list[QuarantineEntry]
    # Meta
    dirty_product_ids: set[str]    # affected products for L3 re-aggregate
    dirty_user_ids: set[str]       # affected users for L3 re-aggregate
```

`process_review()` → `ReviewPersistBundle` 반환으로 변경.
`run_batch()` → bundles 수집 후 batch persist.

### 4A-1. DB connection module
- `src/db/__init__.py`
- `src/db/connection.py`: asyncpg pool manager
  - `create_pool(database_url)` / `close_pool()`
  - pool lifecycle 관리
  - `.env`에서 `DATABASE_URL` 읽기

### 4A-2. Transaction/Unit-of-Work (base_repository 대신)
**v2 변경**: heavy generic base_repository 대신 thin explicit repos + UoW
- `src/db/unit_of_work.py`: per-review atomic transaction wrapper
  - `async with UnitOfWork(pool) as uow:` → transaction begin/commit/rollback
  - Layer 1→2→2.5 + quarantine를 한 review 단위 atomic commit
  - Layer 3 (aggregate/serving)는 별도 batch transaction

### 4A-3. Layer 0/1 repositories
- `src/db/repos/product_repo.py`:
  - `upsert_product_master(product: dict)` — ON CONFLICT (product_id) DO UPDATE
  - `upsert_concept_seeds(concepts: list[dict])` — ON CONFLICT DO NOTHING
  - `upsert_entity_concept_links(links: list[dict])` — ON CONFLICT DO NOTHING
  - `upsert_canonical_entity(entity: dict)` — product entity
- `src/db/repos/review_repo.py`:
  - `insert_review_raw(review: dict)` — ON CONFLICT (review_id) check review_version
  - `insert_review_catalog_link(link: dict)` — ON CONFLICT DO UPDATE (re-match case)
  - `batch_insert_ner_raw(rows: list[dict])`
  - `batch_insert_bee_raw(rows: list[dict])`
  - `batch_insert_rel_raw(rows: list[dict])`
- `src/db/repos/user_repo.py`:
  - `upsert_user_master(user: dict)`
  - `upsert_user_summary(summary: dict)`
  - `insert_purchase_events(events: list[dict])`

### 4A-4. Layer 2 repositories
- `src/db/repos/canonical_repo.py`:
  - `upsert_canonical_entity(entity: dict)` — ON CONFLICT update if higher confidence
  - `upsert_canonical_fact(fact: dict)` — ON CONFLICT (fact_id) → merge `source_modalities` array (array_cat + dedup)
  - `batch_insert_fact_provenance(rows: list[dict])` — ON CONFLICT DO NOTHING
  - `batch_insert_fact_qualifiers(rows: list[dict])` — ON CONFLICT DO NOTHING

### 4A-5. Layer 2.5/3 repositories
- `src/db/repos/signal_repo.py`:
  - `upsert_wrapped_signal(signal: dict)` — ON CONFLICT (signal_id) → merge source_fact_ids
  - `batch_insert_signal_evidence(rows: list[dict])` — ON CONFLICT DO NOTHING
- `src/db/repos/mart_repo.py`:
  - `upsert_agg_product_signal(rows: list[dict])` — ON CONFLICT (product_id, edge_type, dst_id, window_type) DO UPDATE
  - `upsert_agg_user_preference(rows: list[dict])` — ON CONFLICT (user_id, pref_type, dst_id) DO UPDATE
  - `upsert_serving_product_profile(row: dict)` — full replace per product_id
  - `upsert_serving_user_profile(row: dict)` — full replace per user_id

### 4A-6. Quarantine repository
- `src/db/repos/quarantine_repo.py`:
  - `flush_quarantine(entries: list[QuarantineEntry])` — routes each entry to correct table insert

### 4A-7. Pipeline DB integration
- `process_review()` → returns `ReviewPersistBundle`
- `run_batch()` → for each bundle: `UnitOfWork` atomic commit (L1→2→2.5+QA), then batch L3 upsert per dirty_product_ids
- **Transaction boundaries**:
  - per-review: L1 raw + L2 canonical + L2.5 signals + signal_evidence + quarantine → 1 atomic transaction
  - batch: L3 aggregate/serving → per affected product/user set (not one broad batch)

### 4A-8. Schema migration
- `src/db/migrate.py`: executes DDL files in dependency order
  - ddl_raw → ddl_concept → ddl_canonical → ddl_signal → ddl_mart → ddl_quarantine → indexes
  - Idempotent (CREATE TABLE IF NOT EXISTS)

### 4A-9. ★ Operational state tables + DDL 수정 (v3 보강)

**DDL 추가/수정**:
```sql
-- sql/ddl_ops.sql (신규)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id bigserial PRIMARY KEY,
    run_type text NOT NULL,        -- FULL|INCREMENTAL
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    status text NOT NULL DEFAULT 'RUNNING',  -- RUNNING|COMPLETED|FAILED
    watermark_ts timestamptz,      -- last processed event_time
    review_count int DEFAULT 0,
    signal_count int DEFAULT 0,
    quarantine_count int DEFAULT 0,
    error_message text
);
```

**★ review_raw DDL 수정** (watermark 소스 + tombstone 시점):
```sql
ALTER TABLE review_raw ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
-- watermark 소스: updated_at (insert시 = created_at, update/tombstone 시 갱신)
-- incremental selector: WHERE updated_at > :last_watermark
-- watermark 갱신 시점: L3 commit 완료 후
```

**★ wrapped_signal DDL 수정** (source_fact_id → source_fact_ids):
```sql
-- ddl_signal.sql 수정: source_fact_id text → source_fact_ids text[]
-- 이유: 런타임에서 merge 시 multiple fact_ids를 하나의 signal에 보관
-- signal_evidence 테이블이 상세 fact별 evidence를 관리하므로
-- source_fact_ids는 "이 signal에 기여한 fact 목록"의 역할
ALTER TABLE wrapped_signal
    ALTER COLUMN source_fact_id TYPE text[] USING ARRAY[source_fact_id],
    RENAME COLUMN source_fact_id TO source_fact_ids;
-- FK 제거 (array는 FK 불가) — referential integrity는 signal_evidence로 보장
```

**★ L1 audit 정책**: append-only 보존 (v4 확정)
```
L1 (ner_raw, bee_raw, rel_raw)은 append-only evidence layer.
reprocess 시에도 기존 row를 삭제하지 않는다 (PLAN/01:537 준수).

대신:
- ner_raw/bee_raw/rel_raw에 review_version 컬럼 추가
  ALTER TABLE ner_raw ADD COLUMN review_version int NOT NULL DEFAULT 1;
  ALTER TABLE bee_raw ADD COLUMN review_version int NOT NULL DEFAULT 1;
  ALTER TABLE rel_raw ADD COLUMN review_version int NOT NULL DEFAULT 1;
- reprocess 시 새 extraction rows는 review_version = 새 버전으로 insert
- 이전 버전 rows는 그대로 보존 (audit/provenance 역추적 가능)
- Layer 2에서는 최신 review_version 기준으로만 fact 생성
```

**★ canonical_fact reactivation + versioning 정책 (GPT 합의 spec)**

`canonical_fact`는 one-row-per-`fact_id`. surrogate 없음.
remove→re-add(v2→v3)는 같은 row를 reactivate.

Reprocess 알고리즘:
```
1. as_of_ts = now() (트랜잭션 시작 시점, 전체 공유)
2. open_fact_ids = canonical_fact WHERE review_id=:id AND valid_to IS NULL
3. new_fact_ids = 현재 active review_raw에서 추출한 facts
4. to_close = open_fact_ids - new_fact_ids → SET valid_to = as_of_ts
5. 각 extracted fact:
   - fact_id 없음 → INSERT (valid_from=as_of_ts, valid_to=NULL)
   - fact_id 있고 valid_to IS NULL → 컬럼 refresh (fact_id/created_at 제외), valid_from 유지
   - fact_id 있고 valid_to IS NOT NULL → reactivate: valid_from=as_of_ts, valid_to=NULL, 컬럼 refresh
6. fact_provenance/fact_qualifier: 각 fact_id별 full-replace (DELETE existing + INSERT new)
   ★ 이전 provenance는 review_raw_history + L1 raw rows(versioned)에 보존됨
   ★ active fact의 provenance는 항상 현재 review_version의 extraction 결과만 반영
   ★ 이로써 single-interval fact + unversioned provenance 충돌 해소:
      fact row = current active interval, provenance = current version only,
      historical evidence = review_raw_history + versioned L1 rows
7. closed facts는 삭제하지 않음 (audit 보존)
   ★ closed fact의 provenance도 삭제하지 않음 (마지막 active 시점의 evidence 보존)
```

**★ Signal reprocess/diff 정책 (GPT 합의 spec)**

wrapped_signal + signal_evidence는 current-state derived. 버전 히스토리 없음.
reprocess 시 부분 patch 금지 — 해당 review의 signal slice 전체를 full-replace.

```
1. active facts만으로 signal 재생성 (valid_to IS NULL)
2. 기존 signal slice 로드: wrapped_signal WHERE review_id = :review_id
3. 기존 signal_evidence DELETE (해당 slice)
4. 기존 wrapped_signal DELETE (해당 slice)
5. 새 signal INSERT + 새 signal_evidence INSERT
6. merge 규칙 (같은 signal_id):
   - source_fact_ids = dedup, confidence DESC then fact_id ASC 순
   - weight = max(contributor_weight)
   - bee_attr_id/keyword_id = common non-null or NULL
   - negated = bool_or(coalesce(negated, false))
   - intensity = max(intensity) ignoring NULL
   - window_ts = review_raw.event_time_utc
7. signal_evidence: 1 row per (signal_id, fact_id), evidence_rank=0..n-1, contribution=1.0
8. dirty products = union(deleted slice products, inserted slice products,
      ★ previous matched_product_id if relink occurred,
      ★ tombstoned review's matched_product_id) → L3 re-aggregate
   ★ relink case: review_catalog_link.matched_product_id가 변경되면 old+new product 모두 dirty
```

**★ review_raw versioning DDL (GPT 합의 spec)**

review_raw는 single current row. review_raw_history가 immutable version ledger.

```sql
-- DDL 수정
ALTER TABLE review_raw ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- 신규 테이블
CREATE TABLE IF NOT EXISTS review_raw_history (
  review_id text NOT NULL,
  review_version int NOT NULL CHECK (review_version >= 1),
  source text NOT NULL,
  source_review_key text,
  source_site text,
  brand_name_raw text,
  product_name_raw text,
  review_text text NOT NULL,
  reviewer_proxy_id text,
  identity_stability text NOT NULL DEFAULT 'REVIEW_LOCAL',
  event_time_utc timestamptz,
  event_time_raw_text text,
  event_tz text,
  event_time_source text NOT NULL DEFAULT 'PROCESSING_TIME',
  raw_payload jsonb NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  version_op text NOT NULL CHECK (version_op IN ('INSERT','UPDATE','TOMBSTONE','REACTIVATE')),
  review_created_at timestamptz NOT NULL,
  version_created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (review_id, review_version)
);
```

Versioning 규칙:
- review_raw: 항상 latest committed state만 유지
- review_raw_history: append-only, 절대 UPDATE/DELETE 불가
- 변경 없으면 idempotent (version 안 올림, updated_at 안 바뀜, history 안 씀)
- INSERT: version=1, version_op='INSERT'
- UPDATE: version++, version_op='UPDATE'
- TOMBSTONE: version++, is_active=false, version_op='TOMBSTONE'
- REACTIVATE: version++, is_active=true, version_op='REACTIVATE'
- downstream은 review_raw만 읽음. history는 audit/replay 전용.

**★ Review reprocess 전체 흐름**:
```
1. review_raw: ON CONFLICT (review_id) DO UPDATE SET review_version += 1, updated_at = now()
2. ner_raw/bee_raw/rel_raw: 새 rows INSERT (review_version = 새 버전). 기존 rows 보존.
3. canonical_fact: diff-based (위 정책)
4. wrapped_signal: removed facts의 signal DELETE, new/unchanged facts 재-emit
5. signal_evidence: cascade (signal 삭제 시)
6. quarantine: 해당 review_id PENDING entries DELETE (재처리로 새로 생성)
7. L3 re-aggregate: dirty_product_ids로 재집계
```

**★ Tombstone 시**:
```
1. review_raw: SET is_active = false, updated_at = now()
2. ner_raw/bee_raw/rel_raw: 보존 (audit trail, L1 append-only 원칙)
3. canonical_fact: SET valid_to = now() WHERE review_id = :id AND valid_to IS NULL
4. wrapped_signal: DELETE WHERE review_id = :id
5. signal_evidence: cascade delete
6. L3 re-aggregate: dirty_product_ids로 재집계 (tombstone 건 제외)
```

**★ Watermark 계약** (v5 보강: total-order cursor):
- cursor: `(updated_at, review_id)` — timestamp 동점 시 review_id로 total order 보장
- selector: `review_raw WHERE (updated_at, review_id) > (:last_watermark_ts, :last_watermark_rid) AND updated_at <= :current_run_start ORDER BY updated_at, review_id`
- watermark 갱신: L3 commit 성공 후 `pipeline_run.watermark_ts = max(processed updated_at)`, `watermark_rid = last review_id`
- **review reprocess는 반드시 parent review_raw.updated_at bump** (child row만 변경해도 parent 갱신)
- late-arrival: event_time이 과거여도 updated_at 기준이므로 항상 잡힘
- window aggregate: event_time_utc 기준 (watermark와 별도)

```sql
-- pipeline_run에 watermark_rid 추가
ALTER TABLE pipeline_run ADD COLUMN watermark_rid text;
```

---

## Sprint 4B: Provenance Wiring + Incremental Pipeline

### 4B-0. ★ Review mutation semantics (v2 추가)
**현상**: review_raw.review_id가 PK이므로 version bump 시 충돌.
**해결**:
- `review_raw`에 `review_version` 컬럼 이미 존재 (ddl_raw.sql:80)
- PK를 `(review_id, review_version)`으로 변경하거나,
- PK는 review_id 유지하되 `ON CONFLICT (review_id) DO UPDATE SET review_version = review_version + 1`
- **권장**: PK 유지 + ON CONFLICT UPDATE (최신 버전만 유지, 이전 버전은 archive)
- tombstone: `is_active = false` + `updated_at = now()`
- 삭제된 review의 downstream facts/signals도 `valid_to = now()` 마킹

### 4B-1. Explainer provenance DB wiring
- `src/db/repos/provenance_repo.py`:
  - `get_signal_evidence(signal_id) → list[dict]`
  - `get_fact_provenance(fact_id) → list[dict]`
  - `get_review_snippet(review_id, start_offset, end_offset) → str`
- `src/rec/explainer.py` 수정:
  - `ExplanationService` 클래스 추가 (repository-backed)
  - `explain_with_provenance(scored, overlap, provenance_repo) → Explanation`
  - primary path: signal → signal_evidence → fact_provenance → raw snippet
  - fallback: 기존 overlap 기반 (DB 없을 때)

### 4B-2. Incremental pipeline
- `src/jobs/run_incremental_pipeline.py`:
  - `pipeline_run` 테이블에서 last `watermark_ts` 조회
  - `watermark_ts` 이후 `review_raw.event_time_utc`인 리뷰만 처리
  - 새 review → full process_review() + persist
  - 수정 review (same review_id, higher version) → re-process + upsert
  - tombstone review → downstream fact/signal 무효화 + re-aggregate
  - `pipeline_run` row 생성 (watermark 갱신)
- **dirty aggregate**: process 중 변경된 product_id set → 해당 product만 L3 re-aggregate

### 4B-3. Reranker calibration (v2: 별도 분리, P2 취급)
- ★ Sprint 4B에서 **제외** → Sprint 4C로 이동
- 이유: stub 상태이며 DB persistence + provenance + incremental이 더 우선

---

## Sprint 4C: Dictionary Growth + Reranker + Analyst

### 4C-1. Dictionary growth loop
- `src/qa/dictionary_growth.py`:
  - `get_pending_unknown_keywords(limit) → list[dict]` (DB query)
  - `cluster_surfaces(keywords) → list[Cluster]` (surface similarity grouping)
  - `suggest_candidates(cluster) → list[ConceptCandidate]`
  - `approve_candidate(candidate, dictionary_version)` → concept_registry insert + keyword_surface_map update
  - `trigger_backfill(concept_id)` → re-process affected reviews

### 4C-2. Reranker calibration
- `src/rec/reranker.py` 실구현:
  - brand/category diversity bonus
  - `reranker_contribution_log` 테이블 추가 (DDL):
    ```sql
    CREATE TABLE IF NOT EXISTS reranker_contribution_log (
        log_id bigserial PRIMARY KEY,
        run_id bigint,
        user_id text, product_id text,
        original_rank int, final_rank int,
        diversity_bonus real,
        contribution_json jsonb,
        created_at timestamptz DEFAULT now()
    );
    ```

### 4C-3. Analyst queries
- `sql/analyst_queries.sql`:
  - Top BEE_ATTR by product category
  - User concern distribution
  - Product comparison network
  - Quarantine stats by type/status
  - Projection registry coverage report

---

## 검증 체크리스트

### Sprint 4A (DB Persistence)
- [ ] `create_pool()` → asyncpg pool 생성 성공
- [ ] `close_pool()` → 깨끗한 종료
- [ ] `migrate.py` → 모든 DDL 순서대로 실행, 2회 실행해도 에러 없음 (idempotent)
- [ ] product_master INSERT + concept seed + entity_concept_link → 1 transaction 성공
- [ ] review_raw + ner/bee/rel_raw batch INSERT → atomic commit
- [ ] canonical_fact UPSERT → same fact_id 충돌 시 source_modalities merge
- [ ] canonical_entity UPSERT → higher confidence 우선
- [ ] wrapped_signal UPSERT → same signal_id 충돌 시 source_fact_ids merge
- [ ] signal_evidence INSERT → ON CONFLICT DO NOTHING
- [ ] quarantine flush → 5개 테이블 각각 올바른 insert
- [ ] `process_review()` → ReviewPersistBundle 반환 + DB 기록
- [ ] `run_batch()` → L1→2→2.5 per-review atomic + L3 per-product batch
- [ ] 같은 review 2회 처리 → 중복 row 없음 (전 테이블)
- [ ] transaction rollback → partial write 없음
- [ ] pipeline_run row 생성 + watermark 기록
- [ ] schema_migrations 테이블에 버전 기록
- [ ] review_raw.updated_at이 insert/update/tombstone 시 갱신됨
- [ ] wrapped_signal.source_fact_ids가 text[] array로 정상 저장
- [ ] review reprocess: L1 child rows 새 version INSERT, 기존 보존 (append-only)
- [ ] review reprocess: canonical_fact diff-based (unchanged 유지, removed close, new insert)
- [ ] review reprocess 후 이전 version의 provenance 여전히 조회 가능 (audit)
- [ ] same review, same facts → reprocess해도 fact 변동 없음 (diff empty)
- [ ] fact remove→re-add (v1→v2→v3): 같은 fact_id row reactivate 정상
- [ ] review_raw_history에 INSERT/UPDATE/TOMBSTONE/REACTIVATE 각 1건+ 기록
- [ ] 변경 없는 review re-ingest → idempotent (version 안 올라감)
- [ ] signal reprocess: full-replace 후 evidence_rank 재생성 정상
- [ ] signal merge 후 source_fact_ids 순서 = confidence DESC, fact_id ASC

### Sprint 4B (Provenance + Incremental)
- [ ] `get_signal_evidence(signal_id)` → signal_evidence rows 반환
- [ ] `get_fact_provenance(fact_id)` → snippet 포함 provenance 반환
- [ ] `explain_with_provenance()` → signal→fact→raw snippet 체인 성공
- [ ] explanation에 실제 리뷰 원문 snippet 포함
- [ ] incremental: watermark 이후 신규 review만 처리
- [ ] incremental: 수정 review → re-process + version bump
- [ ] incremental: tombstone review → canonical_fact.valid_to + signal 삭제 + re-aggregate
- [ ] late-arrival: 과거 event_time review → updated_at 기준으로 잡힘 + 올바른 window 집계
- [ ] pipeline_run watermark 갱신: L3 commit 후에만 갱신
- [ ] review reprocess → L1 child delete-and-replace + L2 valid_to + L2.5 재생성
- [ ] tombstone → L1 유지(audit) + L2 valid_to + L2.5 삭제 + L3 재집계

### Sprint 4C (Dictionary + Reranker + Analyst)
- [ ] quarantine_unknown_keyword에 미등록 surface 적재 확인
- [ ] dictionary growth → cluster → candidate 추천
- [ ] approved keyword → concept_registry + keyword_surface_map 갱신
- [ ] backfill → 관련 review 재처리
- [ ] reranker diversity → top-k에 brand/category 다양성 반영
- [ ] reranker_contribution_log에 로그 기록
- [ ] analyst queries → 의미 있는 결과 반환

---

## 수정 순서 (의존성)

```
Sprint 4A (DB Persistence) — 1주
  4A-0. Persistence contract (ReviewPersistBundle)
  4A-9. Operational state DDL (schema_migrations, pipeline_run)
  4A-1. DB connection
  4A-2. Unit of Work
  4A-3~6. Repositories (L0/1, L2, L2.5/3, quarantine)
  4A-7. Pipeline integration
  4A-8. Migration script
    ↓
Sprint 4B (Provenance + Incremental) — 1주
  4B-0. Review mutation semantics
  4B-1. Provenance DB wiring
  4B-2. Incremental pipeline
    ↓
Sprint 4C (Dictionary + Reranker + Analyst) — 필요시
  4C-1. Dictionary growth
  4C-2. Reranker
  4C-3. Analyst queries
```
