# 2026-04-25 감사 후속 실행 로드맵

## 배경

2026-04-25 현재 GraphRapping은 테스트 커버리지는 넓지만, 실제 end-to-end 운영 흐름에는 끊기는 지점이 남아 있다.

검증 기준:
- `python -m pytest tests/ -q` 통과: 294 passed
- `python -m ruff check src --statistics` 실패: 88 errors
- `python -m mypy src` 실패: mypy 미설치
- mock 리뷰 직접 로드 시 15개 리뷰 전부 `QUARANTINE`, signal 0
- quarantine은 per-review bundle에는 존재하지만 dashboard/state 집계에는 0으로 표시

이 문서는 Codex와 Claude Code가 같은 우선순위와 결정을 공유하기 위한 실행 기준이다.

## 확정된 운영 방향

### 리뷰 로더 계약

리뷰 로더는 동시에 쓰는 구조가 아니다.

- `relation_loader`
  - Relation 프로젝트가 이미 `ner[]`, `bee[]`, `relation[]` 형태로 만든 중간 산출물용이다.
  - 현재 학습/검증/레거시 fixture 재현에 사용한다.

- `rs_jsonl_loader`
  - S3/운영 원본에 가까운 rs.jsonl 형식용이다.
  - relation model 서빙 이후에는 relation이 포함된 rs.jsonl을 production canonical input으로 본다.

한 pipeline run에서는 보통 하나의 loader만 선택한다.

```text
relation JSON  -- relation_loader  \
                                      -> RawReviewRecord[] -> process_review/run_batch
rs.jsonl       -- rs_jsonl_loader   /
```

### rs.jsonl relation/BEE 승격 전략

현재 rs.jsonl sample은 relation model 서빙 전 상태라 relation이 비어 있을 수 있다.

확정 원칙:
- relation 없는 BEE는 추천용 signal로 억지 승격하지 않는다.
- product title, offset, sentiment만으로 target-linked BEE라고 추정하지 않는다.
- relation model이 relation을 붙인 rs.jsonl이 들어오면, 그 relation을 근거로 BEE attribution을 수행한다.
- relation 없는 rs.jsonl은 valid input이지만 `relation_pending` 상태로 관측한다.
- 운영 최종 방향은 `rs_jsonl_loader` 중심이고, `relation_loader`는 legacy/intermediate/test loader로 유지한다.

## 실행 우선순위

### P0-1. 상품 매칭 / mock 데이터 계약 복구

Status: completed on 2026-04-25.

목표:
- mock 리뷰가 상품 catalog와 정상 연결되어 signal이 0이 아닌 상태가 되게 한다.
- product name에 brand prefix가 붙어도 동일 상품을 deterministic하게 매칭한다.
- mock smoke test에서 `reviews > 0`, `matched > 0`, `signals > 0`을 확인한다.

주요 파일:
- `src/link/product_matcher.py`
- `src/common/text_normalize.py`
- `mockdata/README.md`
- `tests/test_product_matcher.py`
- 신규 smoke/regression test

상세 계획:
- `DECISIONS/2026-04-25_p0_1_product_match_data_contract_plan.md`

완료 기준:
- Product matcher brand-prefix regression 통과
- Mock relation fixture smoke test 통과
- Full test suite `300 passed`

### P0-2. Quarantine 집계/표시 복구

Status: completed on 2026-04-25.

목표:
- review-level quarantine entries가 batch/web summary에서도 보이게 한다.
- `run_batch.total_quarantined`, `demo_state.quarantine_stats`, `/api/quarantine/*`가 같은 기준을 사용한다.

주요 파일:
- `src/jobs/run_daily_pipeline.py`
- `src/web/state.py`
- `tests/test_end_to_end.py` 또는 신규 `tests/test_quarantine_batch_summary.py`

원칙:
- `process_review()`의 per-review bundle quarantine은 유지한다.
- batch total은 shared handler의 잔여 버퍼가 아니라 `bundle.quarantine_entries` 합으로 계산한다.
- web state도 batch result 또는 bundle entries를 기준으로 재구성한다.

상세 계획:
- `DECISIONS/2026-04-25_p0_2_quarantine_batch_web_summary_plan.md`

완료 기준:
- Batch quarantine summary regression 통과
- Web demo state quarantine entries regression 통과
- Full test suite `302 passed`

### P0-3. 운영 DB/증분 파이프라인 correctness

Status: completed on 2026-04-25.

목표:
- migration이 깨지지 않게 `schema_migrations` 생성 순서를 보장한다.
- incremental reprocess가 실제로 `persist_review_bundle()`을 호출해 DB에 반영되게 한다.
- DB child rows를 `RawReviewRecord` 포맷으로 정확히 복원한다.

주요 파일:
- `src/db/migrate.py`
- `src/db/repos/review_repo.py`
- `src/jobs/run_incremental_pipeline.py`
- `tests/test_incremental_*`

원칙:
- migration은 `ddl_ops.sql`을 먼저 실행하거나, `schema_migrations`만 bootstrap 한다.
- snapshot reverse transform은 loader input key(`word`, `entity_group`, `start`, `end`, `sentiment`)로 맞춘다.
- incremental은 process 후 persist 없이 aggregate만 갱신하는 흐름을 허용하지 않는다.

상세 계획:
- `DECISIONS/2026-04-25_p0_3_operational_db_incremental_correctness_plan.md`

완료 기준:
- Migration order regression 통과
- DB snapshot reverse transform regression 통과
- Full test suite `307 passed`
- 수정 파일 범위 ruff 통과

### P1-1. In-memory serving contract와 SQL DDL/repo 동기화

Status: completed on 2026-04-25.

목표:
- in-memory serving profile에서 쓰는 필드가 DB mart에도 보존된다.
- family/purchase personalization 필드가 DB 경로에서 손실되지 않는다.

주요 필드:
- product: `variant_family_id`, `representative_product_name`
- user: `recent_purchase_brand_ids`, `repurchase_brand_ids`, `repurchase_category_ids`, `owned_product_ids`, `owned_family_ids`, `repurchased_family_ids`
- canonical: `negated`, `intensity`, `evidence_kind`, `fact_status`, `target_linked`, `attribution_source`
- provenance: `source_domain`, `source_kind`

주요 파일:
- `sql/ddl_mart.sql`
- `sql/ddl_canonical.sql`
- `sql/ddl_signal.sql`
- `src/db/repos/mart_repo.py`
- `src/db/repos/canonical_repo.py`

상세 계획:
- `DECISIONS/2026-04-25_p1_1_serving_sql_contract_sync_plan.md`

완료 기준:
- Mart repo contract regression 통과
- Canonical repo contract regression 통과
- Related semantic/provenance/serving tests `32 passed`
- Full test suite `311 passed`

### P1-2. rs.jsonl relation-ready contract 공식화

Status: completed on 2026-04-25.

목표:
- relation 없는 rs.jsonl과 relation 포함 rs.jsonl의 의미를 테스트/문서/관측성으로 분리한다.
- relation model 서빙 전까지는 두 loader를 모두 유지한다.
- relation 포함 rs.jsonl이 들어오면 BEE attribution 승격 path가 테스트로 보장된다.

주요 파일:
- `src/loaders/rs_jsonl_loader.py`
- `src/jobs/run_daily_pipeline.py`
- `mockdata/SCHEMA_RS_JSONL.md`
- `PROJECT_OVERVIEW_KO.md`
- 신규 relation-ready fixture/test

결정:
- relation 없는 BEE를 자동 승격하지 않는다.
- `relation_pending_review_count`, `relation_row_count`, `ner_bee_relation_count`, `bee_without_relation_count` 같은 loader-level 관측 지표를 남긴다.

상세 계획:
- `DECISIONS/2026-04-25_p1_2_rs_jsonl_relation_ready_contract_plan.md`

완료 기준:
- rs.jsonl relation-pending regression 통과
- rs.jsonl relation-ready regression 통과
- Web demo state input contract stats 연결
- Full test suite `314 passed`

### P1-3. Promotion metadata 관통

Status: completed on 2026-04-25.

목표:
- `evidence_kind`, confidence, promotion status가 canonical fact -> wrapped signal -> aggregate까지 유지된다.
- `synthetic_ratio`가 항상 0으로 계산되는 현재 문제를 제거한다.
- projection registry의 optional gate 필드를 실제 코드와 CSV schema에 반영한다.

주요 파일:
- `configs/projection_registry.csv`
- `src/wrap/projection_registry.py`
- `src/wrap/signal_emitter.py`
- `src/jobs/run_daily_pipeline.py`
- `src/mart/aggregate_product_signals.py`
- `sql/ddl_signal.sql`

상세 계획:
- `DECISIONS/2026-04-25_p1_3_promotion_metadata_propagation_plan.md`

완료 기준:
- Signal metadata propagation regression 통과
- Signal repo contract regression 통과
- Projection registry optional gate regression 통과
- Full test suite `320 passed`

### P2-1. 추천 mode/scoring/UI/docs 정합성 정리

Status: completed on 2026-04-25.

목표:
- scoring config, backend scorer, frontend default weights, README/architecture 설명을 맞춘다.
- dead path인 `goal_review`를 제거하거나 명시적으로 비활성화한다.
- negative contribution이 explanation/debug에서 누락되지 않게 한다.
- README의 실행 명령을 실제 entrypoint와 맞춘다.

주요 파일:
- `configs/scoring_weights.yaml`
- `src/rec/scorer.py`
- `src/rec/candidate_generator.py`
- `src/rec/explainer.py`
- `src/static/app.js`
- `README.md`
- `ARCHITECTURE.md`
- `pyproject.toml`

상세 계획:
- `DECISIONS/2026-04-25_p2_1_recommendation_scoring_docs_consistency_plan.md`

완료 기준:
- Recommendation contract regression 통과
- YAML/backend/frontend feature key sync
- Negative contribution preservation regression 통과
- Full test suite `324 passed`

## 진행 규칙

1. 각 우선순위는 구현 전에 `DECISIONS/YYYY-MM-DD_<priority>_<topic>_plan.md`로 상세 계획을 남긴다.
2. 계획 문서에는 목표, 비목표, 수정 파일, 테스트, rollback 기준을 명시한다.
3. 구현 후 같은 문서에 완료 기록 또는 별도 completion report를 남긴다.
4. 사용자 변경으로 이미 dirty한 파일은 되돌리지 않는다.
5. 한 번에 여러 P0를 섞지 않고, smoke test가 통과하는 단위로 닫는다.

## 현재 다음 작업

현재 계획된 P0/P1/P2 항목은 모두 완료했다.
Global lint cleanup도 완료했으며 `python -m ruff check src`가 통과한다.
Postgres integration verification scaffold도 완료했다.
Docker-backed Postgres integration execution도 완료했으며 `bash scripts/run_postgres_integration.sh`가 실제 Postgres에서 통과한다.
Mypy type stability도 완료했으며 `python -m mypy src`가 통과한다.
CI quality gate도 추가했으며 push/PR 기본 job과 manual Docker-backed Postgres integration job이 정의되어 있다.

남은 후보:
- external/shared Postgres DB URL 기준 운영 환경 검증
- top-level `src.*` import package를 장기적으로 `graphrapping.*` package로 정리
- GitHub Actions 원격 실행 결과 확인
