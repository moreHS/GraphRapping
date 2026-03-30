# GraphRapping 구현 완료 보고서

## 1. 프로젝트 개요

`GraphRapping`은 리뷰 추출 결과(NER/BEE/REL)를 제품 정본 중심의 의미 그래프로 정규화하고, 사용자 선호 그래프와 `shared concept_id`로 결합해 설명 가능한 추천을 생성하는 Postgres-first 하이브리드 시스템이다.

| 구분 | 수량 | LOC |
|------|------|-----|
| Python 소스 | 35 모듈 (+ 10 `__init__.py`) | 4,468 |
| 테스트 | 15 파일 / 92 테스트 함수 | 1,197 |
| SQL DDL | 7 파일 / 26 테이블 + 인덱스 | 559 |
| 설정 파일 | 10 파일 | 669 |
| **총계** | **~77 파일** | **~6,893** |

---

## 2. 아키텍처

```
[Layer 0: Truth]
product_master / user_master / purchase_event_raw
        │
        ├─ product/user/purchase ingest
        ▼
[Layer 1: Raw / Evidence]
review_raw / review_catalog_link / ner_raw / bee_raw / rel_raw
        │
        ├─ product_matcher → placeholder_resolver → normalizers
        ▼
[Common Concept Plane]
concept_registry / concept_alias / entity_concept_link
        │
        ├─ Product/User를 shared concept_id로 연결
        ▼
[Layer 2: Canonical Fact]
canonical_entity / canonical_fact / fact_provenance / fact_qualifier
        │
        ├─ 65 canonical relations 무손실 보존
        ▼
[Layer 2.5: Signal]
wrapped_signal / signal_evidence
        │
        ├─ Projection Registry 기반 deterministic projection
        ▼
[Layer 3: Serving / Aggregate]
agg_product_signal (30d/90d/all) / agg_user_preference
serving_product_profile / serving_user_profile
        │
        ▼
[Layer 4: Recommendation Runtime]
candidate_generator → scorer → reranker → explainer → hook → next_question

[QA Sidecar]
5종 quarantine (product_match / placeholder / unknown_keyword / projection_miss / untyped_entity)
```

---

## 3. 데이터 흐름 (리뷰 1건 기준)

| 단계 | 처리 | 입력 → 출력 | 핵심 파일 |
|------|------|------------|----------|
| 1 | Review Ingest | `RawReviewRecord` → `review_id`, `reviewer_proxy_id`, `event_time_utc`(항상 non-null), NER/BEE/REL rows | `src/ingest/review_ingest.py` |
| 2 | Product Match | brand/product raw → `matched_product_id` 또는 `QUARANTINE` | `src/link/product_matcher.py` |
| 3 | Placeholder Resolution | NER/REL → mention별 resolved IRI (union-find for same_entity) | `src/link/placeholder_resolver.py` |
| 4 | NER DATE Split | DATE mention → TemporalContext/Frequency/Duration/AbsoluteDate entity | `src/normalize/date_splitter.py` |
| 5 | BEE Normalization | BEE phrase → BEE_ATTR + KEYWORD(s) + polarity/negation/intensity | `src/normalize/bee_normalizer.py` |
| 6 | Relation Canonicalization | raw relation → 65 canonical predicate (idempotent) | `src/normalize/relation_canonicalizer.py` |
| 7 | Type Derivation | ambiguous object → Tool/Concern/UserSegment (dict→pattern→quarantine) | `src/normalize/tool_concern_segment_deriver.py` |
| 8 | Canonical Fact Building | resolved entities + normalized triples → `canonical_fact` + `fact_provenance` | `src/canonical/canonical_fact_builder.py` |
| 9 | Contract Validation | predicate + type → 위반 시 `_invalid_facts` → quarantine | `canonical_fact_builder.py` + `quarantine_handler.py` |
| 10 | Signal Emission | canonical facts → `wrapped_signal[]` + `evidence_rows[]` (transform: identity/reverse/product_linkage) | `src/wrap/signal_emitter.py` |
| 11 | window_ts Hop | `event_time_utc` → signal `window_ts` | `run_daily_pipeline.py` |
| 12 | Aggregate | signals → `agg_product_signal` (30d/90d/all window) | `src/mart/aggregate_product_signals.py` |
| 13 | Serving Profile | master truth + concept IRIs + aggregates + freshness → `serving_product_profile` | `src/mart/build_serving_views.py` |
| 14 | Recommendation | user/product profiles → candidates → scoring → explanation → hook → next question | `src/rec/` |

**보조 흐름 (User)**:

| 흐름 | 처리 | 핵심 파일 |
|------|------|----------|
| User Ingest | `UserRecord` → `user_master` | `src/ingest/user_ingest.py` |
| User Preference | external profile → adapter → `canonical_user_fact` → `agg_user_preference` → `serving_user_profile` | `src/user/adapters/personal_agent_adapter.py` → `canonicalize_user_facts.py` → `aggregate_user_preferences.py` |

---

## 4. 모듈 상세

| 패키지 | 모듈 수 | LOC | 핵심 역할 |
|--------|------:|----:|----------|
| `src/common` | 4 | 434 | ID 규칙(deterministic MD5), enum 정의(15개), config loader, text normalize |
| `src/ingest` | 4 | 485 | Product/User/Purchase/Review 적재, event_time UTC 파싱 |
| `src/link` | 3 | 355 | Product matching(exact→alias→fuzzy→quarantine), placeholder resolution(union-find), alias resolver |
| `src/normalize` | 6 | 689 | BEE(attr+keyword+polarity/negation/intensity), DATE(4분류), REL(65 canonical), NER, Tool/Concern/Segment 파생 |
| `src/canonical` | 1 | 280 | Layer 2 fact builder: entity 등록, fact 생성(dedup), provenance 연결, qualifier, contract 검증 |
| `src/wrap` | 3 | 437 | Projection registry(14-column CSV), signal emitter(transform dispatch + merge + evidence), BEE keyword helper |
| `src/mart` | 3 | 333 | Product aggregate(windowed), user preference aggregate, serving profile(truth + concept IRI + signals + freshness) |
| `src/user` | 2 | 204 | Personal-agent adapter(profile 변환), user canonical facts(17 preference edge types) |
| `src/rec` | 6 | 622 | Candidate generator(hard filter + concept overlap), scorer(linear + shrinkage), reranker, explainer(score-faithful), hook, next-question |
| `src/qa` | 2 | 228 | Quarantine handler(6 메서드), evidence sampler(top-k) |
| `src/jobs` | 1 | 401 | Pipeline orchestration: `process_review()` 단건 + `run_batch()` 배치 |

---

## 5. 설정 파일

| 파일 | 행 수 | 역할 | 소비 모듈 |
|------|------:|------|----------|
| `projection_registry.csv` | 73행 | Layer 2→2.5 projection 규칙 (14-column) | `projection_registry.py` |
| `predicate_contracts.csv` | 70행 | 65 predicate type contract | `canonical_fact_builder.py` |
| `bee_attr_dict.yaml` | 198행 | 39 BEE attribute 사전 | `bee_normalizer.py` |
| `keyword_surface_map.yaml` | 48행 | surface form → keyword_id | `bee_normalizer.py` |
| `relation_canonical_map.json` | 76행 | 65 identity mapping (확장 가능) | `relation_canonicalizer.py` |
| `scoring_weights.yaml` | 33행 | feature weight + shrinkage_k + brand confidence | `scorer.py` |
| `concern_dict.yaml` | 52행 | concern 사전 | `tool_concern_segment_deriver.py` |
| `segment_dict.yaml` | 34행 | user segment 사전 | `tool_concern_segment_deriver.py` |
| `tool_dict.yaml` | 22행 | makeup tool 사전 | `tool_concern_segment_deriver.py` |
| `date_context_dict.yaml` | 61행 | DATE 분류 규칙 | 확장 포인트 (현재 코드 내장 규칙 우선) |

---

## 6. 테스트 커버리지 (92 tests, all passing)

| 테스트 파일 | 함수 수 | 검증 대상 |
|------------|------:|----------|
| `test_ids.py` | 15 | ID determinism, fact_id semantics, qualifier fingerprint, registry_version |
| `test_date_splitter.py` | 13 | DATE 4분류 (TemporalContext/Frequency/Duration/AbsoluteDate) |
| `test_bee_normalizer.py` | 9 | BEE_ATTR + KEYWORD, polarity, negation(double-negation flip), intensity |
| `test_recommendation.py` | 15 | Candidate generation, scoring(shrinkage), explanation, hook, next-question |
| `test_signal_emitter.py` | 10 | Transform dispatch(identity/reverse/product_linkage), qualifier check, merge, evidence |
| `test_projection_registry.py` | 8 | Registry load, predicate mapping, completeness check, determinism |
| `test_placeholder_resolver.py` | 7 | Union-Find, same_entity merge, placeholder resolution |
| `test_product_matcher.py` | 6 | Exact/norm/alias/fuzzy/quarantine chain |
| `test_event_time_propagation.py` | 6 | created_at→UTC, collected_at fallback, never-null, source_row_num |
| `test_concept_link_integrity.py` | 4 | User-product concept IRI join, reviewer proxy isolation |
| `test_predicate_contracts.py` | 4 | 65 predicates 계약 존재, 위반 시 reject |
| `test_truth_override_protection.py` | 4 | CATALOG_VALIDATION scoring 제외, has_ingredient/brand_of 매핑 |
| `test_reviewer_isolation.py` | 4 | Proxy IRI ≠ user IRI, user facts에 proxy 미유입 |
| `test_idempotency.py` | 3 | 2× 처리 → 1 fact + 2 provenance, deterministic review_id |
| `test_end_to_end.py` | 2 | 전체 파이프라인 acceptance criteria |

---

## 7. 7대 불변 원칙 준수 현황 (Sprint 3 시점 기준 — Sprint 4 후 최종 상태는 §17 참조)

| # | 원칙 | 상태 | 근거 |
|---|------|------|------|
| 1 | Layer 2에서 65 relations 보존 | ✅ 준수 | `relation_canonicalizer.py:18` (65 predicates), `canonical_fact.predicate` |
| 2 | Layer 3는 Projection Registry만 | ✅ 준수 | `signal_emitter.py:75` → `ProjectionRegistry.project()` 필수 호출 |
| 3 | Product/User join은 shared concept_id만 | ✅ 준수 | `product_ingest.py` concept seed + `candidate_generator.py` concept IRI 비교 |
| 4 | reviewer proxy ≠ real user | ✅ 준수 | `ids.py:44` namespace 분리, `test_reviewer_isolation.py` 검증 |
| 5 | Product master truth override 금지 | ✅ 준수 | `has_ingredient`/`brand_of` → `CATALOG_VALIDATION` only, scoring 제외 |
| 6 | 모든 signal provenance 역추적 | ⚠️ 부분 (Sprint 3 시점) | DDL + evidence_rows 생성은 완료, DB 영속화 + explainer 직접 조회는 미완 → **Sprint 4에서 완전 준수로 승격 (§17)** |
| 7 | 실패는 explicit quarantine | ✅ 준수 | 5종 quarantine + `quarantine_invalid_fact()`, 파이프라인 전 경로 연결 |

---

## 8. 감사 결과 및 수정 이력

| 단계 | 결과 | 핵심 지적 |
|------|------|----------|
| GPT Code Reviewer 1차 | REJECT | CRITICAL 7 (REL wiring, transform, event_time, concept join, evidence, silent drop) |
| GPT 별도 감사 | REJECT | P0 5개 (Layer 2 wiring, projection transform, event_time, concept join, explanation) |
| 수정 계획 v1 | REJECT | P0-1 너무 광범위, contract 선행 필요 |
| 수정 계획 v2 | REJECT | REL→NER join 계약 없음, main_benefits 누락 |
| 수정 계획 v3 | REJECT | user predicate scope, invalid-fact quarantine, event_time hop, product_linkage 위치, benefits 필드명 |
| **수정 계획 v4** | **APPROVE** | 5개 전부 해소 |

**P0 수정 5건**:

| P0 | 문제 | 수정 | 검증 |
|----|------|------|------|
| REL wiring | placeholder resolution 결과 미사용 | `run_daily_pipeline.py` 전면 재작성: mention IRI, DATE split, type derivation 전부 연결 | test_end_to_end |
| Transform | identity만 동작, reverse/product_linkage 미구현 | `signal_emitter.py`: 3종 transform dispatcher + qualifier check + weight rule | test_signal_emitter (6개) |
| event_time | 항상 None | `review_ingest.py`: UTC 파싱 (절대 None 아님) + window_ts hop | test_event_time_propagation (5개) |
| Concept join | raw ID vs concept IRI 불일치 | `build_serving_views.py` + `candidate_generator.py`: concept IRI 필드 + 비교 | test_concept_link_integrity (4개) |
| Evidence | signal_evidence 미생성 | `signal_emitter.py`: evidence_rows 생성, `EmitResult` 확장 | test_signal_emitter.evidence |

---

## 9. 미구현/제한사항 (Sprint 3 시점 기준 — Sprint 4 후 최종 상태는 §19 참조)

| 항목 | Sprint 3 시점 상태 | Sprint 4 후 상태 |
|------|-------------------|-----------------|
| Graph materialization (AGE/Neo4j) | optional | ❌ optional (변경 없음) |
| Dictionary growth loop | DDL만 존재 | ✅ **Sprint 4C에서 구현** |
| Incremental pipeline | batch recompute 중심 | ✅ **Sprint 4B에서 구현** |
| pgvector evidence retrieval | 미구현 | ❌ 미구현 (변경 없음) |
| Reranker diversity | stub (bonus=0.0 고정) | ✅ **Sprint 4C에서 실구현** |
| DB 영속화 레이어 | 미연결 | ✅ **Sprint 4A에서 구현** |
| Explainer provenance DB wiring | overlap 기반 fallback | ✅ **Sprint 4B에서 구현** |

---

## 10. 다음 단계

| 우선순위 | 항목 | 설명 |
|---------|------|------|
| P1 | DB 영속화 레이어 | Layer 1~3 row를 Postgres에 upsert하는 repository/DAO 구현 |
| P1 | 증분 처리 | event_time 기준 re-windowing, late-arrival, tombstone 처리 |
| P1 | Explainer provenance wiring | fact_provenance/signal_evidence DB 조회 → explainer 연결 |
| P2 | Reranker diversity | brand/category diversity bonus + calibration logging |
| P2 | Dictionary growth loop | quarantine → surface clustering → approval → backfill |
| P2 | Graph projection | AGE → Neo4j (SQL query pain 발생 시) |
| P2 | pgvector evidence | 리뷰 원문 기반 evidence retrieval |

**현재 완료 수준**: "도메인 규칙이 선명한 메모리 기반 reference implementation". 다음 투자는 기능 추가보다 **DB 영속화, 증분 처리, provenance wiring**이 우선.

---
---

# Sprint 4 완료 보고서 (부록)

## 11. Sprint 4 개요

Sprint 1~3의 in-memory reference implementation 위에 **DB 영속화**, **증분 처리**, **provenance wiring**, **dictionary growth**, **reranker diversity**를 구현한 스프린트.

| 구분 | Sprint 1-3 | Sprint 4 추가 | 최종 합계 |
|------|-----------|-------------|----------|
| Python 소스 모듈 | 35 | 18 신규 + 5 수정 | 53 |
| 테스트 파일 / 테스트 수 | 15 / 92 | 1 / 4 추가 | 16 / 96 |
| SQL DDL + Queries | 7 | 2 신규 + 3 수정 | 9 |
| Config 파일 | 10 | 0 | 10 |
| **총 LOC** | **~6,893** | **~3,051** | **~9,500+** |

---

## 12. Sprint 4 아키텍처 확장

```
[기존 5-Layer + Common Concept + QA] (Sprint 1-3)
        │
        ▼
[NEW] DB Persistence Layer
├── connection.py          ← asyncpg pool
├── unit_of_work.py        ← per-review atomic transaction
├── persist_bundle.py      ← ReviewPersistBundle dataclass
├── persist.py             ← L1→L2→L2.5+QA atomic, L3 batch orchestrator
├── migrate.py             ← idempotent DDL migration (9 files)
└── repos/
    ├── product_repo.py    ← product_master + concept seed + entity link
    ├── review_repo.py     ← versioned review + history + L1 idempotency + snapshot loader
    ├── user_repo.py       ← user_master + summary + purchase events
    ├── canonical_repo.py  ← diff-based fact reprocess (insert/refresh/reactivate/close)
    ├── signal_repo.py     ← full-replace per review (no partial patch)
    ├── mart_repo.py       ← aggregate + serving profile upserts
    ├── quarantine_repo.py ← 5종 quarantine flush to DB
    └── provenance_repo.py ← signal→evidence→fact→raw snippet chain queries

[NEW] Incremental Pipeline
└── run_incremental_pipeline.py ← watermark-based, tombstone, dirty aggregate

[NEW] Operational State
├── schema_migrations      ← DDL version tracking
├── pipeline_run           ← watermark_ts + watermark_rid + run stats
└── reranker_contribution_log ← diversity bonus logging

[MODIFIED] Pipeline Bundle Refactor
└── run_daily_pipeline.py  ← process_review() → ReviewPersistBundle 반환
    ├── build_review_persist_bundle()
    └── bundle_to_result_dict() (backward compat)

[NEW] Dictionary Growth
└── dictionary_growth.py   ← quarantine → cluster → suggest → approve → backfill

[MODIFIED] ExplanationService
└── explainer.py           ← ProvenanceProvider protocol + DB-backed explanation

[MODIFIED] Reranker
└── reranker.py            ← brand/category diversity bonus (MMR-style)
```

---

## 13. Sprint 4 데이터 흐름 (DB Persistence 경로)

### 13-1. 리뷰 처리 + 영속화 (per-review atomic)

| 단계 | 처리 | 입력 → 출력 | 핵심 파일 |
|------|------|------------|----------|
| 1 | `process_review()` | `RawReviewRecord` → `ReviewPersistBundle` | `run_daily_pipeline.py` |
| 2 | `persist_review_bundle()` | Bundle → DB (1 atomic transaction) | `persist.py` |
| 2a | L1: review_raw upsert | review + history append (version_op: INSERT/UPDATE/TOMBSTONE/REACTIVATE) | `review_repo.py` |
| 2b | L1: child rows insert | ner/bee/rel_raw (review_version, append-only, idempotency guard) | `review_repo.py` |
| 2c | L2: entities upsert | canonical_entity (confidence-aware merge) | `canonical_repo.py` |
| 2d | L2: facts diff-upsert | canonical_fact (unchanged 유지, removed close, reactivated 재활성) | `canonical_repo.py` |
| 2e | L2: provenance/qualifier | fact_provenance + fact_qualifier (full-replace per fact) | `canonical_repo.py` |
| 2f | L2.5: signals replace | wrapped_signal + signal_evidence (full-replace per review) | `signal_repo.py` |
| 2g | QA: quarantine flush | 5종 quarantine entries → DB | `quarantine_repo.py` |
| 3 | L3: aggregate batch | dirty_product_ids → agg_product_signal (30d/90d/all) | `mart_repo.py` |
| 4 | L3: serving profiles | product/user serving profile upsert | `mart_repo.py` |

### 13-2. 증분 처리 (Incremental Pipeline)

| 단계 | 처리 | 핵심 |
|------|------|------|
| 1 | watermark 조회 | `pipeline_run` 테이블에서 마지막 `(watermark_ts, watermark_rid)` |
| 2 | 변경 review 조회 | `WHERE (updated_at, review_id) > (wm_ts, wm_rid) AND updated_at <= run_start` |
| 3a | 신규/수정 review | `load_ingested_review_snapshot()` → `process_review()` → `persist_review_bundle()` |
| 3b | Tombstone review | `handle_tombstone()` → close facts + delete signals + dirty products |
| 4 | Dirty re-aggregate | 변경된 product만 agg_product_signal + serving_product_profile 갱신 |
| 5 | Watermark 갱신 | L3 commit 성공 후 `pipeline_run.watermark_ts = max(processed updated_at)` |

### 13-3. 설명 Provenance 경로

```
User request → scored product → ExplanationService.explain_with_provenance()
  → signal_ids → provenance_repo.get_signal_evidence(signal_id)
    → fact_ids → provenance_repo.get_fact_provenance(fact_id)
      → review_id, snippet, offsets → provenance_repo.get_review_snippet()
        → 실제 리뷰 원문 발췌
```

---

## 14. Sprint 4 모듈 상세

### 14-1. DB Infrastructure (Sprint 4A)

| 모듈 | LOC | 핵심 역할 |
|------|----:|----------|
| `db/connection.py` | 39 | asyncpg pool (min=2, max=10), singleton lifecycle |
| `db/unit_of_work.py` | 58 | `async with UoW(pool) as uow:` — auto-commit/rollback, `as_of_ts` 공유 |
| `db/persist_bundle.py` | 40 | ReviewPersistBundle: L1~L2.5+QA 전체 artifact bundle |
| `db/persist.py` | 100 | `persist_review_bundle()` (per-review atomic) + `persist_aggregates()` (batch) |
| `db/migrate.py` | 59 | 9 DDL 파일 순서 실행, `schema_migrations` 기록, idempotent |

### 14-2. Repositories (Sprint 4A/B)

| 모듈 | LOC | 핵심 역할 | 주요 계약 |
|------|----:|----------|----------|
| `repos/review_repo.py` | 225 | review versioning + history + L1 child insert + snapshot loader | version bump=ON CONFLICT UPDATE, history=append-only, children=idempotency guard |
| `repos/canonical_repo.py` | 165 | diff-based fact reprocess | insert/refresh/reactivate/close, provenance full-replace |
| `repos/signal_repo.py` | 71 | full-replace per review | delete old slice → insert new, dirty product tracking |
| `repos/mart_repo.py` | 137 | aggregate + serving upserts | ON CONFLICT UPDATE for all mart tables |
| `repos/product_repo.py` | 75 | product master + concept seed | confidence-aware entity upsert |
| `repos/user_repo.py` | 57 | user master + purchase events | append-only purchase log |
| `repos/quarantine_repo.py` | 82 | 5종 quarantine flush | table routing by entry.table |
| `repos/provenance_repo.py` | 75 | explanation chain queries | signal→evidence→fact→provenance→snippet |

### 14-3. Pipeline (Sprint 4A/B)

| 모듈 | LOC | 핵심 역할 |
|------|----:|----------|
| `jobs/run_daily_pipeline.py` | 464 | `process_review()` → ReviewPersistBundle, `run_batch()`, `bundle_to_result_dict()` |
| `jobs/run_incremental_pipeline.py` | 287 | watermark cursor, tombstone, dirty aggregate, pipeline_run lifecycle |

### 14-4. QA/Rec 확장 (Sprint 4C)

| 모듈 | LOC | 핵심 역할 |
|------|----:|----------|
| `qa/dictionary_growth.py` | 148 | surface clustering → candidate → approve → concept_registry 갱신 |
| `rec/reranker.py` | 113 | MMR-style brand/category diversity + contribution logging |
| `rec/explainer.py` | 239 | ExplanationService (ProvenanceProvider protocol, DB-backed chain) |

### 14-5. SQL (Sprint 4)

| 파일 | 행 수 | 핵심 변경 |
|------|------:|----------|
| `ddl_raw.sql` | 183 | `review_raw.updated_at`, `review_raw_history` 테이블, L1 child `review_version`, rel_raw offsets |
| `ddl_signal.sql` | 48 | `source_fact_id` → `source_fact_ids text[]` |
| `ddl_ops.sql` (신규) | 34 | `schema_migrations`, `pipeline_run`, `reranker_contribution_log` |
| `analyst_queries.sql` (신규) | 124 | 8개 analyst SQL (BEE_ATTR 분석, concern 분포, 비교 네트워크, quarantine 현황 등) |

---

## 15. Sprint 4 핵심 설계 계약 (GPT 6회 크로스 리뷰)

| 계약 | 내용 | 리뷰 라운드 |
|------|------|-----------|
| **Fact reactivation** | one-row-per-fact_id, valid_from/valid_to lifecycle, diff-based (unchanged유지/removed close/reactivated 재활성) | Sprint 4 계획 v4 |
| **Signal reprocess** | full-replace per review_id (partial patch 금지), evidence 재생성 | Sprint 4 계획 v4 |
| **Review versioning** | review_raw (current) + review_raw_history (immutable ledger, INSERT/UPDATE/TOMBSTONE/REACTIVATE) | Sprint 4 계획 v4 |
| **Watermark** | (updated_at, review_id) total-order cursor, L3 commit 후에만 갱신 | Sprint 4 계획 v6 |
| **L1 append-only** | ner/bee/rel_raw에 review_version, 기존 rows 보존, 새 version append | Sprint 4 계획 v4 |
| **Dirty products** | union(old signals + new signals + relink + tombstone) | Sprint 4 계획 v6 |
| **Bundle return** | process_review() → ReviewPersistBundle (bundle_to_result_dict for backward compat) | Hotfix v3 |
| **REL offsets** | rel_raw에 subj_start/end, obj_start/end 저장 → lossless reconstruction | Hotfix v3 |
| **L1 idempotency** | batch_insert_* 전에 (review_id, review_version) 존재 여부 체크 | Hotfix v3 |
| **Signal evidence 정본** | `signal_evidence` 테이블이 정본. `wrapped_signal.source_fact_ids`는 편의/캐시 컬럼 | 최종 피드백 반영 |
| **Co-use product** | `USED_WITH_PRODUCT_SIGNAL`은 registry/serving/analyst에 구현됨. scorer feature 확장은 루틴/번들 추천 시 | 최종 피드백 반영 |
| **Purchase brand confidence** | `derive_brand_confidence()` → `refresh_user_preferences()` → concept IRI 기반 brand weight 부스트 | 최종 피드백 반영 |

---

## 16. Sprint 4 감사 결과 및 수정 이력

### 16-1. 구현 후 크로스 리뷰

| 단계 | 결과 | 핵심 발견 |
|------|------|----------|
| 내부 탐색 + GPT Code Reviewer | Critical 1, High 4, Medium 4 | incremental persist gap, REL offset 미저장, bundle 미반환 |
| Hotfix 계획 v1 | GPT REJECT | process_review → bundle 분리 미정, REL offset DDL 미정 |
| Hotfix 계획 v2 | GPT REJECT | L1 audit + fact reactivation 계약 미정 |
| **Hotfix 계획 v3** | **GPT APPROVE** | bundle split spec + REL offset DDL + load_snapshot + L1 idempotency |

### 16-2. 적용된 Hotfix 6건

| Fix | 심각도 | 내용 |
|-----|--------|------|
| FIX-1a | Critical | `process_review()` → `ReviewPersistBundle` 반환 + `bundle_to_result_dict()` |
| FIX-1b | Critical | `rel_raw` DDL에 `subj_start/end, obj_start/end` 4개 offset 컬럼 |
| FIX-1c | Critical | `load_ingested_review_snapshot()` + L1 idempotency guard |
| FIX-2 | Medium | `signal_evidence ON CONFLICT (signal_id, fact_id, evidence_rank)` |
| FIX-3 | Medium | `upsert_canonical_entity` → `upsert_product_entity` rename |
| FIX-4 | Medium | `import json` module level 이동 |

---

## 17. Sprint 4 포함 7대 불변 원칙 준수 현황 (갱신)

| # | 원칙 | 상태 | Sprint 4 변경 |
|---|------|------|-------------|
| 1 | Layer 2에서 65 relations 보존 | ✅ 준수 | diff-based reprocess가 unchanged facts 유지 |
| 2 | Layer 3는 Projection Registry만 | ✅ 준수 | 변경 없음 |
| 3 | Product/User join은 shared concept_id만 | ✅ 준수 | 변경 없음 |
| 4 | reviewer proxy ≠ real user | ✅ 준수 | review_raw_history에 reviewer_proxy_id 분리 보존 |
| 5 | Product master truth override 금지 | ✅ 준수 | 변경 없음 |
| 6 | 모든 signal provenance 역추적 | ✅ **완전 준수** | ExplanationService + provenance_repo → DB 기반 signal→fact→raw 체인 완성 |
| 7 | 실패는 explicit quarantine | ✅ 준수 | quarantine_repo.flush_quarantine() → 5종 테이블 DB write |

**Sprint 4에서 원칙 6이 "부분 준수"에서 "완전 준수"로 승격**: provenance_repo + ExplanationService로 DB 기반 역추적 경로 완성.

---

## 18. 테스트 현황 (Sprint 4 포함 최종)

| 테스트 파일 | 함수 수 | Sprint | 검증 대상 |
|------------|------:|--------|----------|
| test_ids.py | 15 | 1 | ID determinism |
| test_date_splitter.py | 13 | 1 | DATE 4분류 |
| test_bee_normalizer.py | 9 | 2 | BEE_ATTR + KEYWORD + polarity/negation/intensity |
| test_recommendation.py | 15 | 3 | Candidate + scoring + explanation + hook + question |
| test_signal_emitter.py | 10 | 3+fix | Transform dispatch + qualifier + merge + evidence |
| test_projection_registry.py | 8 | 3+fix | Registry completeness + determinism |
| test_placeholder_resolver.py | 7 | 1 | Union-Find + placeholder resolution |
| test_product_matcher.py | 6 | 1 | Match chain (exact→alias→fuzzy→quarantine) |
| test_event_time_propagation.py | 6 | fix | event_time UTC parsing + never-null + source_row_num |
| test_concept_link_integrity.py | 4 | fix | Concept IRI join + reviewer proxy isolation |
| test_predicate_contracts.py | 4 | fix | 65 predicates 계약 + 위반 reject |
| test_truth_override_protection.py | 4 | fix | CATALOG_VALIDATION scoring 제외 |
| test_reviewer_isolation.py | 4 | fix | Proxy IRI ≠ user IRI |
| test_idempotency.py | 3 | fix | 2× 처리 중복 없음 |
| test_end_to_end.py | 2 | 2 | 전체 파이프라인 acceptance |
| **test_reranker.py** | **4** | **4C** | **Diversity bonus + contribution logging** |
| **총계** | **96** | | **전체 통과** |

---

## 19. 미구현/제한사항 (Sprint 4 후 갱신)

| 항목 | Sprint 4 전 | Sprint 4 후 | 비고 |
|------|-----------|-----------|------|
| DB 영속화 레이어 | ❌ 미연결 | ✅ **완료** | repos + persist + migrate |
| 증분 처리 | ❌ 미구현 | ✅ **완료** | watermark + tombstone + dirty aggregate |
| Explainer provenance | ⚠️ 부분 | ✅ **완료** | ExplanationService + provenance_repo |
| Reranker diversity | ❌ stub | ✅ **완료** | MMR-style brand/category diversity |
| Dictionary growth | ❌ 미구현 | ✅ **완료** | cluster → suggest → approve |
| Analyst queries | ❌ 없음 | ✅ **완료** | 8개 pre-built SQL |
| Graph projection (AGE/Neo4j) | ❌ optional | ❌ optional | SQL query pain 발생 시 |
| pgvector evidence | ❌ 미구현 | ❌ 미구현 | 유사 evidence 탐색 |
| **실제 Postgres 통합 테스트** | — | ⚠️ **필요** | 현재 unit test만, DB 연동 integration test 미작성 |

---

## 20. 다음 단계 (Sprint 4 후)

| 우선순위 | 항목 | 설명 |
|---------|------|------|
| P1 | Postgres 통합 테스트 | 실제 DB 환경에서 persist/migrate/incremental end-to-end 검증 |
| P1 | 실데이터 적재 | Relation 프로젝트 산출물(50K+ reviews) 실제 파이프라인 실행 |
| P2 | Graph projection | AGE/Neo4j (SQL path query 비용이 높아질 때) |
| P2 | pgvector evidence | 리뷰 원문 기반 유사 evidence retrieval |
| P2 | API 서버 | FastAPI 기반 추천/설명 API endpoint |

**현재 완료 수준**: "DB 영속화까지 갖춘, 운영 직전 수준의 reference implementation". 도메인 로직, DB 스키마, 영속화 경로, 증분 처리, provenance 체인이 모두 연결됨. 실운영 투입을 위해서는 **실 DB 통합 테스트 + 동시성 검증**이 선행되어야 함. 다음 투자는 **Postgres 통합 테스트 → 실데이터 적재 → API 서버**.

### 전제조건 명시
- **입력 데이터 전제**: 현재 GraphRapping 입력은 upstream Relation 프로젝트에서 이미 canonicalized된 65 predicate를 전제로 한다. raw/variant relation이 섞여 올 경우 `relation_canonical_map.json`을 Relation 프로젝트의 633→65 full mapping으로 교체해야 한다.
- **date_context_dict.yaml**: config-first 원칙. YAML이 정본이며 코드 내장 규칙은 fallback. YAML에 추가된 term은 코드 변경 없이 적용됨.
