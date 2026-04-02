# HANDOFF — vNext final patch 완료 상태

## 이번 세션 완료 항목

### Phase A — mock schema alignment
- [x] P0-1: product_loader가 SALE_PRICE→price, MAIN_EFFECT→main_benefits, MAIN_INGREDIENT→ingredients, REPRESENTATIVE_PROD_CODE→variant_family_id 실제 매핑
- [x] P0-2: user adapter 3가지 수정
  - OWNS_PRODUCT → Product entity reference (ConceptType.BRAND 아닌 실제 product identity)
  - preferred_texture → BEE_ATTR("Texture") axis + KEYWORD("GelLike" 등) 2-layer 생성
  - REPURCHASES_PRODUCT_OR_FAMILY → REPURCHASES_BRAND + REPURCHASES_CATEGORY 분리
- [x] P0-2 downstream: enums.py, build_serving_views.py, candidate_generator.py, scorer.py 일관 반영

### Phase B — serving/runtime enforcement
- [x] P0-3: promoted-only 전경로 강제 검증
  - **버그 발견/수정**: run_daily_pipeline.py, run_incremental_pipeline.py의 `_agg_to_dict()`에 `is_promoted` 누락 → 모든 promoted 시그널이 serving에서 탈락되던 문제 수정
- [x] P0-4: provenance SoT = signal_evidence 강제, source_fact_ids 캐시 주석 강화
- [x] P0-5: review mock에 source_review_key/author_key 추가 (8 distinct authors, 15 reviews)
- [x] P1-3: texture 2-layer scorer (residual BEE_ATTR) + explainer (제형 축 + 구체 표현 분리 설명)

### Phase C — mock 계약 및 회귀 테스트
- [x] P1-4: raw/normalized user mock 계약 명확화 (README에 공식 입력=normalized 명시)
- [x] P1-5: shared_entities/review_kg 회귀 테스트 (cross-source integrity + evidence kind coverage)

## 테스트 상태
- **218 tests 전부 통과** (이전 180 → 218, 신규 38)

## 신규 테스트 파일
- tests/test_product_loader_mock_schema.py (5 tests) — P0-1
- tests/test_user_adapter_semantics.py (3 tests) — P0-2
- tests/test_serving_profile_promotion_gate.py (5 tests) — P0-3
- tests/test_signal_evidence_source_of_truth.py (+2 tests 추가) — P0-4
- tests/test_mock_review_contract.py (3 tests) — P0-5
- tests/test_texture_preference_flow.py (6 tests) — P1-3
- tests/test_mock_user_contract.py (4 tests) — P1-4
- tests/test_mock_integrity.py (5 tests) — P1-5
- tests/test_mock_review_kg_regression.py (6 tests) — P1-5

## 수정된 소스 파일
- src/loaders/product_loader.py — ES 필드 실제 매핑 + _es_meta
- src/user/adapters/personal_agent_adapter.py — 3가지 concept 매핑 수정
- src/common/enums.py — REPURCHASES_BRAND, REPURCHASES_CATEGORY 추가
- src/mart/build_serving_views.py — repurchase_category_ids 분리
- src/rec/candidate_generator.py — owned_product_ids product: prefix 처리
- src/rec/scorer.py — product: prefix normalization
- src/rec/explainer.py — texture 2-layer 설명
- src/wrap/signal_emitter.py — source_fact_ids 캐시 주석
- src/jobs/run_daily_pipeline.py — _agg_to_dict에 is_promoted 추가
- src/jobs/run_incremental_pipeline.py — _agg_to_dict에 is_promoted 추가
- src/loaders/relation_loader.py — source_review_key, author_key 매핑
- sql/ddl_signal.sql — source_fact_ids 캐시 DDL 주석
- mockdata/review_triples_raw.json — stable keys 추가
- mockdata/README.md — 계약 명확화

## 최종 완료 기준 체크리스트
1. [x] product loader가 mock product truth를 실제 ingest에 반영
2. [x] OWNS_PRODUCT가 실제 product identity로 동작
3. [x] preferred_texture가 BEE_ATTR(Texture) + KEYWORD 두 층으로 반영
4. [x] serving product profile 기본 경로는 promoted signal만 사용
5. [x] provenance 정본은 signal_evidence로 일관
6. [x] raw/normalized user mock 계약이 문서와 실행 경로에서 충돌하지 않음
7. [x] shared_entities/review_kg_output이 실제 테스트 자산으로 사용됨
8. [x] product truth + review corpus signal + user profile이 shared concept plane에서 일관 연결

## 향후 작업 (deferred)
- [ ] P2-1: fact_provenance 범용화 (source_domain/source_kind 확장)
- [ ] P2-2: repo boundary 정리 (코어 serving vs evidence 경계 문서화)
- [ ] NER-BeE flatten → anchor evidence transition
- [ ] BEE contract 추가 필드 (evidence_text, evidence_span, derived_qualifiers)
- [ ] kg_mode legacy/shadow 완전 분리
- [ ] SQL prefilter 실제 DB 통합 테스트
- [ ] corpus_weight 기반 scorer feature 계산
