# 02. 이슈 진단 — 강점 / 약점 / 보완점

작성일: 2026-07-07 · 기준 커밋 `431dae3`
분석 방법: 병렬 코드 탐색 3방향 + 핵심 주장 직접 검증 + Codex Architect 크로스 리뷰 반영

## 1. 강점 (유지할 것)

개선 작업이 이 강점들을 훼손하면 안 된다. 특히 1~2번은 이 시스템의 차별점이다.

| # | 강점 | 근거 |
|---|---|---|
| S1 | **Evidence-first 추천 계약** — eligibility gate + 3 evidence family(MASTER_TRUTH / REVIEW_GRAPH / PURCHASE_BEHAVIOR) 분리. 근거 없는 상품은 점수 무관 탈락. API가 근거 유형을 반환해 검증 가능 | [recommendation_evidence_index.py](../src/rec/recommendation_evidence_index.py), [candidate_generator.py](../src/rec/candidate_generator.py), [DECISIONS/2026-06-19](../DECISIONS/2026-06-19_evidence_first_personalization_recommendation.md) |
| S2 | **3중 promotion gate + provenance 체인** — adapter → signal_emitter → aggregator 게이트, `signal_evidence`가 설명 체인 정본(`source_fact_ids`는 deprecated cache) | [ARCHITECTURE.md](../ARCHITECTURE.md), [db_consumer_contract.md §5](../docs/architecture/db_consumer_contract.md) |
| S3 | **Idempotency-first 파이프라인** — review_version upsert, per-review full-replace, watermark 증분 커서(early-stop), advisory lock 직렬화(FULL↔INCREMENTAL cross-mutex, pool≥2 강제) | [persist.py](../src/db/persist.py), [pipeline_lock.py](../src/db/pipeline_lock.py), [run_incremental_pipeline.py](../src/jobs/run_incremental_pipeline.py) |
| S4 | **Quarantine-first QA** — 실패 5종(product_match/placeholder/unknown_keyword/projection_miss/untyped_entity)을 버리지 않고 격리, dictionary growth 루프의 입력 | [quarantine_handler.py](../src/qa/quarantine_handler.py), [dictionary_growth.py](../src/qa/dictionary_growth.py) |
| S5 | **Config 기반 온톨로지** — 68 relations / 42 BEE / predicate contract / projection registry가 설정 파일로 관리, 코드 수정 최소화. 레이어 간 결합도 위반 사례 거의 없음(rec은 serving만, mart는 wrapped_signal만 읽음) | configs/ 4개 core 파일, [kg/config.py](../src/kg/config.py) |
| S6 | **스코어링 디테일 성숙** — scoped preferences(카테고리 scope 누수 방지), residual matching(이중 계산 방지), shrinkage(support 기반 신뢰도 보정), BEE 타겟 어트리뷰션(무관 신호 격하) | [scorer.py](../src/rec/scorer.py), [scoped_preferences.py](../src/rec/scoped_preferences.py), [bee_attribution.py](../src/link/bee_attribution.py) |
| S7 | **문서화/검증 문화** — DECISIONS/ 의사결정 기록, consumer contract의 정직한 한계 명시(§12 retention), 테스트 110개 파일, evidence audit 스크립트, contract_validator | [db_consumer_contract.md](../docs/architecture/db_consumer_contract.md), [audit_recommendation_evidence.py](../scripts/audit_recommendation_evidence.py) |

## 2. 약점 진단 (A~G)

각 항목에 심각도(전략/품질/차단/장기)와 개선 계획 매핑(03 문서의 Phase 항목)을 표기.

### A. 정체성 갭 — "그래프 시스템"인데 그래프 능력이 없음 [전략]

문서 스스로 인정하는 갭: *"Graph centrality 미구현. 'graph' naming이지만 실제로는
RDB 기반 score aggregation + threshold gate"* ([db_consumer_contract.md §12.2](../docs/architecture/db_consumer_contract.md)).

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| A1 | **multi-hop 순회 코드 0건.** canonical_fact는 (subject_iri, predicate, object_iri) RDF 트리플 구조이고 순회용 인덱스(`idx_cf_pred_subj/obj`)까지 있는데, 이를 그래프로 질의하는 코드가 없다. 유일한 간접 추론(concern_bridge: BEE→concern 추정)은 YAML 하드코딩 1-hop. 단, multi-hop이 실제 필요한 사용 사례도 아직 증명된 바 없음 — "구현 안 함"이 현시점 잘못이라는 뜻은 아니고, "그래프 시스템"이라는 목적과의 갭 | src/ 전체 grep(traversal/hop/path/walk 0건), [concern_bee_attr_map.yaml](../configs/concern_bee_attr_map.yaml) | Phase 4.0→4.1 |
| A2 | **온톨로지 비정형.** 4개 core config + 10여 개 사전 yaml에 스키마가 산재하고, 파일 간 정합성(관계가 계약에 있는지, 투영 대상 타입이 존재하는지) 자동 검증이 없다. `neo4j_label` 필드는 정의만 있고 사용처 없음(죽은 의도). 새 관계 추가 시 최대 7곳 수정 | [kg_entity_types.json](../configs/kg_entity_types.json), [kg_relation_types.json](../configs/kg_relation_types.json), [predicate_contracts.csv](../configs/predicate_contracts.csv), [projection_registry.csv](../configs/projection_registry.csv) | Phase 4.3 |
| A3 | **검색 경로 부재.** 목적에 '검색' 포함이나 추천 API만 구현. src/rec 전체와 web/server.py에 검색/질의 경로 없음 | [server.py](../src/web/server.py) | Phase 4.2 |

### B. 데이터 정합성 / identity 계약 리스크 [품질·오염, 크로스 리뷰에서 승격]

실데이터/채널 확대 시 조용히 커지는 리스크. 추천 품질보다 downstream(AmoreSimulation)
오염 위험이 크다는 것이 크로스 리뷰의 지적.

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| B1 | **source identity collision.** `product_id`는 downstream 호환 키일 뿐 clean identity가 아니다. clean identity는 `source_channel + source_key_type + source_product_id` 복합키이며, 실제 collision(`35119`)이 존재해 `SOURCE_KEY_COLLISION` 마커로 처리 중. 현재 1건이지만 채널 확대 시 증가하는 구조이고, 감지/처리가 로더별 수동 계약에 의존 | [db_consumer_contract.md §3](../docs/architecture/db_consumer_contract.md), README | Phase 1.1 |
| B2 | **glb 채널은 상품명이 product_id.** Amazon/Sephora 온보딩 시 identity 붕괴 위험(상품명 변경=다른 상품 취급, 동명 상품 충돌). 스키마 문서에 "주의"로만 존재하고 대응 전략 미결정 | [SCHEMA_RS_JSONL.md §3](../mockdata/SCHEMA_RS_JSONL.md) | Phase 1.2 |
| B3 | **profile scope / active-category 의미 drift 위험.** "active category ≠ 선호", "scoped preference를 전역 선호로 평탄화 금지", "AVOIDS_INGREDIENT는 hard filter" 같은 개인화 계약이 문서와 일부 런타임 구조에 있으나, 회귀 방어가 signal_flow 문서의 **수동 검토 체크리스트** 중심. 계약이 깨지면 겉보기에 그럴듯한 **잘못된 개인화**가 되어 눈검사로 잡기 어려움 | [DECISIONS/2026-06-23](../DECISIONS/2026-06-23_active_category_is_not_preference.md), [recommendation_signal_flow_2026_06_23.md](../docs/architecture/recommendation_signal_flow_2026_06_23.md) 검토 체크리스트 | Phase 0.2 |

### C. 랭킹 품질 측정 수단 부재 [품질·속도, 최우선]

정확한 상태: evidence-family audit([audit_recommendation_evidence.py](../scripts/audit_recommendation_evidence.py))와
골든 프로파일 audit 테스트(tests/test_golden_profile_recommendation_audit.py)가 이미
coverage / evidence-family / score-layer를 검증한다. **부재한 것은 그 위의 순위 품질
정량화**다. ("평가 인프라 전무"는 과장 — 크로스 리뷰 지적 반영)

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| C1 | **랭킹 메트릭 부재.** 순위가 좋아졌는지 판단할 지표(NDCG/precision류)와 ground-truth 기대셋이 없다. 품질 판단이 프론트엔드 수동 검사에 의존. 2026-06-22 DECISIONS도 "evidence-family 사용률 리포트 추가"를 미완 follow-up으로 명시 | [2026-06-22 DECISIONS](../DECISIONS/2026-06-22_dense_golden_fixture_and_semantic_evidence_matching.md) | Phase 0.1/0.3/0.5 |
| C2 | **가중치 수동 튜닝.** 24개+ feature의 가중치가 scoring_weights.yaml 수동값. 변경 효과를 정량 비교할 수단이 없어 튜닝이 감에 의존 | [scoring_weights.yaml](../configs/scoring_weights.yaml), [scorer.py](../src/rec/scorer.py) | Phase 0 완료 후 |
| C3 | **provenance explainer 미완.** ProvenanceExplanationPath(리뷰 스니펫/fact_ids/review_ids) 자료구조만 정의, 구현 없음. "이 추천의 근거 리뷰 원문"을 보여줄 수 없어 품질 판정도 느려짐 | [explainer.py](../src/rec/explainer.py) | Phase 0.4 |
| C4 | **완전 콜드 유저 fallback 없음.** partial profile(basic/chat 일부)은 지원하나, 프로필 전무 유저에 대한 명시 정책 부재 | [personal_agent_adapter.py](../src/user/adapters/personal_agent_adapter.py) | Phase 5 백로그 |

### D. 시맨틱 층의 천장 — 사전/규칙의 한계 [품질]

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| D1 | **임베딩/벡터 전무.** 시맨틱 매칭 전부가 사전 + YAML 규칙(코드베이스에 embedding/vector 참조 0건). 동의어/신조어/오타 대응 불가, 사전 유지보수 비용이 어휘 증가에 선형 비례. 단 value-and-polarity rule로 정밀도를 지키는 것은 **기존 결정**이므로, 해법은 규칙 교체가 아니라 "규칙 유지보수의 자동 보조" | [semantic_compatibility.py](../src/rec/semantic_compatibility.py), pyproject.toml, [DECISIONS/2026-06-22 repair](../DECISIONS/2026-06-22_recommendation_master_graph_evidence_usage_repair.md) | Phase 3.2 |
| D2 | **broad semantic 누수 관측.** `지속력→bee_attr_lasting_power` 규칙이 카테고리 무관 발화 — 스킨케어 탭에서 여러 상품이 동일한 약한 근거로 도배되는 케이스가 signal_flow 문서에 기록됨. 규칙에 카테고리 scope/gating 부재 | [recommendation_signal_flow_2026_06_23.md](../docs/architecture/recommendation_signal_flow_2026_06_23.md) "Broad Semantic 케이스" | Phase 3.1 |
| D3 | **한글 퍼지 매칭 한계.** 상품명 매칭이 ASCII 지향 SequenceMatcher(0.93/0.80 임계). 한글 자모 분해/띄어쓰기 변형 특성 미반영 → quarantine_product_match로 새는 비율 증가 요인 | [product_matcher.py:17](../src/link/product_matcher.py) | Phase 3.3 |

### E. 서빙 경로 미분리 [프로덕션 전환 차단]

정확한 상태: DB `serving_*` 테이블과 consumer contract는 **이미 존재**한다
(AmoreSimulation은 DB를 직접 읽음). 문제는 GraphRapping 자체 웹/API surface가
mart reader로 분리되지 않은 것. ("서빙이 데모 수준" 표현 정정 — 크로스 리뷰 반영)

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| E1 | **API가 in-memory 상태만 순회.** FastAPI가 전역 `DemoState` 싱글톤의 serving_products/users를 순회하며, server.py에 DB 접근 코드 0건(직접 grep 확인). 파이프라인이 DB를 갱신해도 API는 메모리 재적재 전까지 모름 | [state.py:67](../src/web/state.py), [server.py](../src/web/server.py) | Phase 2.1 |
| E2 | **후보 생성 기본 경로가 전 상품 순회.** `generate_candidates()`가 `for product in product_profiles` 선형 스캔. SQL prefilter([mart_repo.py:411](../src/db/repos/mart_repo.py))와 prefiltered 경로([candidate_generator.py:326](../src/rec/candidate_generator.py))가 존재하나 기본 경로가 아니고, in-memory 경로와의 **동치성 검증이 없음** | [candidate_generator.py:88](../src/rec/candidate_generator.py) | Phase 2.2 |
| E3 | **실 스케일 미검증.** 906/517 fixture 기준 개발. 현 단계에선 문제가 아니며, 부하검증은 서빙 경로가 contract로 고정된 뒤 수행할 항목(순서가 중요) | mockdata/ | Phase 5 백로그 |

### F. 운영 성숙도 [연속 운영 전 필요]

fixture 단계에선 휴면 리스크. 실데이터 연속 적재(incremental 상시 운영) 시작 전에
정책 결정이 선행되어야 한다.

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| F1 | **Retention 미구현(문서화만).** 무한 누적 3종: ① quarantine 5종 TTL 없음 — 906리뷰 baseline에서 이미 9,255행(신호의 ~10배) ② `agg_product_signal` all window 영구 누적 — 90일 stale cleanup은 opt-in env var이고, 활성 상품은 last_seen_at 갱신으로 cleanup 영구 회피 ③ ner/bee/rel_raw가 review_version마다 append. Wave 6 계획은 "리뷰 max 보존 기간 사용자 지정" 대기 상태 | [db_consumer_contract.md §12](../docs/architecture/db_consumer_contract.md), [mart_repo.py:293](../src/db/repos/mart_repo.py) | Phase 1.3 (설계) + Phase 5 (구현) |
| F2 | **스케줄러/재시도/알림/CLI 부재.** run_daily_pipeline은 이름과 달리 스케줄링 없음(수동 트리거만). 실패 시 자동 재시도 없음, pipeline_run.error_message 폴링 필요. 운영자가 Python 코드를 직접 불러야 함 | [ddl_ops.sql](../sql/ddl_ops.sql), README | Phase 1.4, 2.3 |
| F3 | **마이그레이션 한계.** idempotent DDL 8개 + schema_migrations 기록 방식. down migration 없음, ALTER hotfix가 DDL 파일에 누적 | [migrate.py](../src/db/migrate.py), [ddl_raw.sql:36](../sql/ddl_raw.sql) | Phase 5 백로그 |

### G. 확장성 제약 [장기]

| # | 내용 | 근거 | 개선 |
|---|---|---|---|
| G1 | **한국어/뷰티 도메인 하드코딩.** 코드 내: placeholder 패턴(이거/이것/이 제품 — bee_attribution.py:26-35), 날짜 정규식(주/달/년 — date_splitter.py:51-76), 감성 매핑(긍정→POS — kg/config.py:17), 카테고리 6그룹+한국어 키워드(category_groups.py), explainer 한국어 템플릿. 설정 내: BEE 42종/concern 30+/goal 20+ 한국어 사전. 다국어/타 도메인 확장 시 사전 구조부터 개편 필요 | [bee_attribution.py:26](../src/link/bee_attribution.py), [date_splitter.py:51](../src/normalize/date_splitter.py), [category_groups.py](../src/rec/category_groups.py) | Phase 5 백로그 |
| G2 | **유저 소스 단일 어댑터 강결합.** personal-agent 3그룹 스키마에 어댑터가 밀착. 새 유저 데이터 소스(예: 앱 행동 로그) 추가 시 어댑터 계약 일반화 필요 | [personal_agent_adapter.py](../src/user/adapters/personal_agent_adapter.py) | Phase 5 백로그 |
| G3 | **kg_mode 이중 파이프라인 부채.** off/shadow/on 전환 장치는 마이그레이션 강점이나, legacy(off) 경로가 무기한 유지되면 이중 유지보수. shadow parity 판정 기준과 legacy 제거 시점 미정 | [ARCHITECTURE.md](../ARCHITECTURE.md) kg_mode contract | Phase 5 백로그 |
| G4 | **리뷰어-유저 미연결(의도적).** reviewer proxy와 실 유저 병합 금지 invariant로 협업 필터링류 신호가 원천 차단됨. 프라이버시상 의도라면 명시적 한계로 기록하고, 완화 필요 시 별도 결정 필요 | [ARCHITECTURE.md](../ARCHITECTURE.md) invariants | 결정 필요 시 DECISIONS |

## 3. 요약 매트릭스

| 차원 | 현황 | 평가 |
|---|---|---|
| 아키텍처 골격 (5-layer, 게이트, provenance) | 명확한 계약, 결합도 위반 거의 없음 | ✅ 우수 |
| 데이터 일관성 (idempotency, watermark, lock) | 구현 완료 + 테스트 검증 | ✅ 우수 |
| 온톨로지 | config 기반 확장 가능, 단 비정형·검증 부재 | ⚠️ 보완 |
| 그래프 능력 | 트리플 저장만, 순회/centrality 없음 | ❌ 갭 |
| 시맨틱 매칭 | 규칙 기반 정밀도 우선, 재현율/유지보수 한계 | ⚠️ 보완 |
| 추천 품질 측정 | evidence audit 있음, 랭킹 메트릭 없음 | ❌ 갭 (최우선) |
| identity/계약 방어 | 문서 계약은 명확, 자동 방어 부분적 | ⚠️ 보완 |
| 서빙 | consumer contract 있음, 자체 API는 in-memory | ⚠️ 보완 |
| 운영 (retention/스케줄/관측) | 문서화만, 구현 대기 | ⚠️ 시점 관리 |
| 확장성 (다국어/도메인/소스) | 한국어·뷰티·단일 소스 결합 | 장기 과제 |
