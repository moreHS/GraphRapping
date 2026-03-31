# HANDOFF — 통합 수정 Phase 1-4 완료 상태

## 완료 항목

### Phase 1: 리뷰 의미 손실 복구 + KG 정리 (8 Steps)
- [x] ids.py: make_signal_id에 negated/qualifier_fingerprint 조건부 추가
- [x] enums.py: EvidenceKind, PromotionDecision, KeywordSource, FactStatus, SignalPromotionStatus
- [x] models.py: EntityMention/RelationMention/KeywordMention/KGEdge/KGResult semantic 필드
- [x] bee_normalizer.py: keyword_source, 이중부정 감지
- [x] canonical_fact_builder.py: CanonicalFact에 negated/intensity/evidence_kind/fact_status
- [x] mention_extractor.py: synthetic 표시, auto keyword → quarantine candidates
- [x] canonicalizer.py: sentiment split 제거, CANDIDATE keyword skip
- [x] adapter.py: promotion gate (_classify_promotion), metadata 전달
- [x] signal_emitter.py: fact→signal negated/intensity 전달, dedup key 강화, EVIDENCE_ONLY skip
- [x] projection_registry.py: allowed_evidence_kind, min_confidence, promotion_mode 컬럼
- [x] run_daily_pipeline.py: BEE negated/intensity/confidence 전달, keyword candidates quarantine
- [x] 18 신규 테스트 (test_phase1_semantic_preservation.py)

### Phase 2: 유저 레이어 강화
- [x] enums.py: USER_STATE/CONCERN/GOAL/CONTEXT/BEHAVIOR edge type groups
- [x] canonicalize_user_facts.py: 5개 family builder 분리
- [x] purchase_ingest.py: PurchaseFeatures + derive_purchase_features()
- [x] personal_agent_adapter.py: purchase_features 파라미터 추가
- [x] scorer.py: skin_type_fit, goal_fit_master/review_signal, purchase_loyalty, novelty_bonus
- [x] scoring_weights.yaml: 13개 feature weights 재조정
- [x] build_serving_views.py: behavior 섹션 (owned/repurchase/recent_purchase)
- [x] candidate_generator.py: already_owned 필드, goal_master/goal_review 분리

### Phase 3: 증분 파이프라인 안정화
- [x] review_repo.py: load_full_review_snapshot() 추가 (RawReviewRecord 변환)
- [x] run_incremental_pipeline.py: 빈 child row 재처리 금지 → snapshot 로드
- [x] signal_repo.py: get_dirty_product_ids_for_review() (comparison/co-use 포함)

### Phase 4: Corpus KG / Serving 고도화
- [x] aggregate_product_signals.py: distinct_review_count, avg_confidence, synthetic_ratio, corpus_weight, is_promoted
- [x] is_corpus_promoted() 함수 (review≥3, confidence≥0.6, synthetic_ratio≤0.5)
- [x] server.py: graph API에 ?view=corpus|evidence 파라미터

## 테스트 상태
- 139 tests 전부 통과 (121 기존 + 18 Phase 1 신규)

## 남은 작업
- [ ] configs/projection_registry.csv에 신규 컬럼 실제 값 채우기 (현재 default만)
- [ ] migrations/001_add_semantic_metadata.sql 작성 (ALTER TABLE)
- [ ] build_serving_views.py: evidence_graph_view / corpus_graph_view 함수 완성
- [ ] scorer.py: corpus_weight 기반 feature 계산 (Phase 4.4 일부)
- [ ] candidate_generator.py: CandidatePrefilter SQL class
- [ ] aggregate_product_signals.py: force_recompute_windows 파라미터 완성
- [ ] 데모 UI에서 전체 검증 (서버 재시작 + graph 확인)
- [ ] Phase 2-4 전용 테스트 추가
- [ ] DECISIONS/ 기록 업데이트

## 주의사항
- BEE_ATTR sentiment split 제거는 kg_mode="on" 경로만 영향
- make_signal_id는 조건부 append로 기존 signal hash 호환 유지
- run_incremental_pipeline의 UnitOfWork import는 pool 기반 (async)
