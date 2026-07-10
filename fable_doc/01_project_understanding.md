# 01. 프로젝트 구조 파악

작성일: 2026-07-07 · 기준 커밋 `431dae3`

GraphRapping이 무엇이고 어떻게 동작하는지에 대한 전체 지도. 약점 진단(02)과
개선 계획(03)의 전제가 되는 사실 기록이다.

## 1. 프로젝트 목적

상품마스터, 리뷰 기반 트리플 데이터, 유저프로파일 — 3가지 소스를 연결해 KG(지식그래프)를
구축하고, 추천/검색/개인화에 활용 가능한 그래프 시스템 + 온톨로지를 구현한다.

핵심 설계 철학은 **evidence-first**: 모든 추천은 검증 가능한 근거(evidence)에서
출발해야 하며, 근거 없는 상품은 점수가 높아도 추천 자격(eligibility)을 얻지 못한다.

## 2. 데이터 소스 3종

### 2.1 리뷰 트리플 — rs.jsonl (외부 NLP 산출물)

- 생성 주체: 별도 upstream 시스템(inference-gerter). Snowflake 원본 → prepare →
  NER/BEE 분석 → rs.jsonl. GraphRapping은 이 파일을 **입력으로 받기만** 한다.
- 1 line = 1 review. 공통 필드: `id, text, date, product_id, prd_nm, channel,
  ner_spans[], bee_spans[], relation[](추가 예정), p_chain_inputs[]`
- NER 라벨 5종: AGE / CAPACITY / BASE_COLOR / BRAND / CATEGORY
- BEE 라벨 39종(제품 34 + 서비스 3 + 고객 2): 보습력, 발색력, 지속력, 밀착력 등 한국어 라벨
- 감성: 긍정/부정/중립/복합 → POS/NEG/NEU/MIXED
- `relation_pending`(relation 비어있음) / `relation_ready`(relation 존재) 두 상태 모두 허용
- **채널별 product_id 출처가 다름** (주의):
  - own(031)=ecp_onln_prd_srno, own(036/039/048)=통합 온라인 상품코드,
    extn=std_prd_cd, **glb=상품명이 코드로 사용**
- 스키마 정본: [mockdata/SCHEMA_RS_JSONL.md](../mockdata/SCHEMA_RS_JSONL.md)

### 2.2 상품마스터

- 517개 상품(현 fixture), 브랜드 38개. `product_master` 테이블이 L0 truth
- source identity는 `source_channel + source_key_type + source_product_id` 복합키.
  `product_id`는 AmoreSimulation 호환용 primary key일 뿐 clean source identity가 아님
  (`35119` collision 실존 → `SOURCE_KEY_COLLISION` 마커 처리)
- `source_truth_quality`: SOURCE_GROUNDED / MISSING_* / SYNTHETIC_* / SOURCE_KEY_COLLISION
- source review stats(Snowflake `f_prd_rv_hist` 6개월/전체 리뷰량·평점)는
  `serving_product_profile`에 조인되지만 **trust/tie-break 전용** — 추천 자격 근거 아님

### 2.3 유저프로파일 — personal-agent

- 원천: `/Users/amore/workplace/agent-aibc/persnal-agent`의 3그룹 프로필
  - `basic`: skin_type / skin_tone / skin_concerns
  - `purchase_analysis`: preferred_brand(전체/카테고리별 6종), active_product_category,
    preferred_repurchase_category, 구매/재구매 상품 요약
  - `chat`: face/hair/body/scalp/makeup별 concerns·goals·texture·finish·color·scent,
    ingredients preferred/avoid/allergy
  - `purchase_features`: owned_product_ids, owned_family_ids, repurchased_*,
    recently_purchased_brand_ids
- [personal_agent_adapter.py](../src/user/adapters/personal_agent_adapter.py)가
  canonical user fact(edge)로 변환. 전체 필드→edge 매핑 표는
  [recommendation_signal_flow_2026_06_23.md](../docs/architecture/recommendation_signal_flow_2026_06_23.md)가 정본
- 요약 상품명 해석은 **고신뢰 exact match만** 허용(fuzzy 의도적 배제)
- 리뷰어(reviewer proxy)와 실 유저는 **절대 병합하지 않음** (invariant)

## 3. 5-Layer 아키텍처

```
Layer 0    Product/User Master Truth (불변 원천)
Layer 1    Raw Evidence (ner_raw, bee_raw, rel_raw — append-only, review_version별)
Layer 2    Canonical Fact (68 relations, deterministic ID, 전 관계 무손실 보존)
Layer 2.5  Wrapped Signal (projection registry로 명시 선택된 것만)
Layer 3    Aggregate/Serving (windowed 집계, corpus promotion 통과분만)
Layer 4    Recommendation (candidate → score → rerank → explain)
```

**Evidence graph vs Serving graph 분리** (핵심 원칙):

- Evidence graph(L0~2): 리뷰 단위 스코프, 불변, 감사/디버그/탐색 용도
- Serving graph(L2.5~3): corpus 승격 스코프, 추천/개인화 용도
- 추천은 evidence graph를 직접 소비하지 않는다. 3중 promotion gate를 통과한
  신호만 serving에 도달: ① Adapter(per-edge: synthetic/auto→evidence-only)
  ② SignalEmitter(per-fact: projection_registry의 IMMEDIATE/CORPUS_THRESHOLD/NEVER)
  ③ Aggregator(corpus: distinct_review_count ≥ 2(30d)/3(90d,all),
  avg_confidence ≥ 0.6, synthetic_ratio ≤ 0.5)

**kg_mode 계약** (`off | shadow | on`): legacy NER/BEE/REL 처리와 KG 파이프라인의
전환 장치. `get_kg_mode()`가 인자 > 환경변수 > 호출자별 기본값 순으로 해석, 잘못된
값은 즉시 ValueError(fail-closed). 배치 기본 `off`, 데모 UI 기본 `on`.

## 4. 온톨로지/스키마 구성

형식 온톨로지(OWL/SHACL)는 없고, **4개 core config + 10여 개 사전 yaml**로 구성된
config-driven 스키마다.

| 파일 | 역할 | 규모 |
|---|---|---|
| [configs/kg_entity_types.json](../configs/kg_entity_types.json) | 엔티티 타입 정의 (NER 기본 11종 + BEE_ATTR 42종). `neo4j_label` 메타 포함(실사용처 없음) | 53종 |
| [configs/kg_relation_types.json](../configs/kg_relation_types.json) | 관계 타입 + 감성 매핑(긍정→POS) | 68종(65 표준 + 특수) |
| [configs/predicate_contracts.csv](../configs/predicate_contracts.csv) | predicate별 허용 subject/object 타입, polarity, qualifier, L3 투영 가능 여부 | 72행 |
| [configs/projection_registry.csv](../configs/projection_registry.csv) | L2→L2.5 투영 규칙 (edge_type, transform, 미해결 시 액션). reviewer-proxy 계열은 DROP | ~50행 |

사전 yaml: bee_attr_dict, concern_dict, goal_alias_map, keyword_surface_map,
texture_keyword_map, tool_dict, segment_dict, skin_type_concern_map,
concern_bee_attr_map, date_context_dict, relation_canonical_map,
recommendation_semantic_compatibility, scoring_weights, user_weighting.

**Common Concept Plane** (Product와 User의 조인 평면, `concept_id` 기준):
Brand, Category, Ingredient, BEEAttr, Keyword, Concern, Goal, Tool, Context — 9종.

**새 엔티티/관계 추가 체크리스트** (탐색으로 확인):
1. `kg_entity_types.json` 타입 추가
2. `kg_relation_types.json` 관계 정의
3. `predicate_contracts.csv` 계약 행 추가
4. `projection_registry.csv` 투영 규칙 추가
5. `src/kg/config.py`는 자동 로드 — 코드 수정 불필요
6. 필요 시 `src/normalize/ner_normalizer.py`의 `_NER_TO_CANONICAL` 매핑
7. 도메인 분류가 필요하면 `src/common/concept_resolver.py` + 해당 사전 yaml

## 5. KG 파이프라인 (리뷰 단위 evidence graph)

```
MentionExtractor → SameEntityMerger → Canonicalizer → Adapter
  → CanonicalFactBuilder → SignalEmitter
```

- 출력은 **evidence-scope**(리뷰 단위)이며 전역 KG가 아님
- Adapter의 promotion gate가 엣지를 PROMOTE / KEEP_EVIDENCE_ONLY / DROP / QUARANTINE으로 분류
- **BEE 타겟 어트리뷰션** ([bee_attribution.py](../src/link/bee_attribution.py)):
  BEE 신호가 리뷰 대상 상품을 가리키는지 우선순위 체인으로 검증
  (direct_rel → placeholder_resolved → same_entity_resolved → comparison_resolved).
  무관 신호는 evidence-only로 격하
- 상품 매칭 ([product_matcher.py](../src/link/product_matcher.py)):
  정규화 exact → alias → SequenceMatcher fuzzy (0.93 auto-accept / 0.80~0.93 review
  / 미만 quarantine)
- 다중 모달리티: 같은 fact를 NER/BEE/REL 여러 경로로 증명 가능
  (`source_modalities text[]` union)
- Quarantine 5종: product_match / placeholder / unknown_keyword / projection_miss /
  untyped_entity — 실패를 버리지 않고 격리, dictionary_growth 루프의 입력

## 6. 추천 파이프라인 (Layer 4)

```
serving_user_profile + serving_product_profiles
  → generate_candidates (hard filter + concept overlap + evidence eligibility)
  → scorer (24 feature, shrinkage)
  → reranker (diversity bonus)
  → explainer (score-faithful paths)
  → hook_generator + next_question
```

### 6.1 Evidence family (eligibility 근거 3종 + 비근거)

| family | 구성 |
|---|---|
| PRODUCT_MASTER_TRUTH | brand, category(명시 선호時), catalog_keyword, ingredient, goal_master |
| REVIEW_GRAPH_RELATION | keyword, bee_attr, semantic_*, context, concern, concern_bridge, tool, coused, comparison |
| PURCHASE_BEHAVIOR | owned_family, repurchased_family, repurchase_brand/category, recent_purchase_brand |
| (비근거) | active_category(약한 context only), source_review_*(trust/tie-break only), review_summary_sidecar(표시 전용) |

### 6.2 스코어러 상세

- feature 24개(문서상 19개 + 추가), 각각 [0,1] 정규화 후 가중 합산
  (예: `keyword_match = min(count/3, 1)`)
- **residual matching**: `residual_bee_attr = max(0, bee_attr_units - keyword_units)`
  — keyword와 BEE의 이중 계산 방지
- **shrinkage**: `support/(support+k)`, k=10 — 리뷰 support 적은 상품 점수 자동 감쇠
- 가중치: [scoring_weights.yaml](../configs/scoring_weights.yaml) 수동 튜닝
  (keyword_match 0.16 최대, novelty_bonus 0.02 최소).
  brand_confidence = {purchase 1.0, chat_strong 0.7, chat_weak 0.4}
- score layer 7종으로 집계: master_truth / review_graph / review_graph_weak_evidence /
  product_activity / profile_fit / purchase_behavior / source_trust

### 6.3 Scoped preferences

유저 선호에 카테고리 scope(skincare/makeup/haircare/bodycare/fragrance) 부여 —
"메이크업 전용 매트 선호"가 스킨케어 추천 점수에 새지 않도록 `scope_allows()`로 검증.
`ACTIVE_IN_CATEGORY`는 선호(`PREFERS_CATEGORY`)가 아니라 약한 활동 컨텍스트
([DECISIONS/2026-06-23](../DECISIONS/2026-06-23_active_category_is_not_preference.md)).

### 6.4 시맨틱 매칭 — 전부 규칙 기반

[recommendation_semantic_compatibility.yaml](../configs/recommendation_semantic_compatibility.yaml):
rule = {axis, value, polarity, user_signals, matches(대상+strength), blocks(상충 차단)}.
예: `보습` 선호 → `kw_moist(1.0)`, `kw_moisturizing(0.95)`,
`bee_attr_moisturizing_power(0.8)` 매칭, `매트` 계열은 block.
임베딩/벡터 검색은 전무 — 코드베이스에 embedding/vector 참조 0건.

### 6.5 Explainer

- **score-faithful**: 설명은 점수 계산에 쓰인 매칭 경로에서만 생성 (별도 서사 없음)
- ExplanationPath = {concept_type, concept_id, user_edge, product_edge, contribution},
  기여도 상위 top-5 절사
- 한국어 템플릿 하드코딩. ProvenanceExplanationPath(리뷰 스니펫/fact_ids/review_ids)는
  자료구조만 정의, 구현 미완

## 7. DB 스키마 맵

| 계층 | 테이블 | 특성 |
|---|---|---|
| L0 | product_master, user_master, purchase_event_raw | truth, is_active soft-delete |
| L1 | review_raw(+history), ner_raw, bee_raw, rel_raw, review_catalog_link | review_version별 append-only |
| L2 | canonical_entity, canonical_fact, fact_provenance | subject_iri/predicate/object_iri 트리플, diff upsert |
| L2.5 | wrapped_signal, signal_evidence | per-review full-replace, provenance 정본 |
| L3 | agg_product_signal, agg_user_preference, serving_product_profile, serving_user_profile | window(30d/90d/all), promotion gate |
| QA | quarantine_* 5종 | TTL 없음 |
| ops | schema_migrations, pipeline_run | run 상태/카운터/watermark/lock_holder_pid |

- 인덱스: `idx_aps_product_window`(집계 핫패스), `idx_cf_pred_subj/obj`(트리플 순회용 —
  현재 미활용), `idx_ws_window`(시간 범위)
- 마이그레이션: 8개 idempotent DDL(`CREATE TABLE IF NOT EXISTS`) + schema_migrations
  기록. down migration 없음, ALTER hotfix가 DDL 파일에 누적
- dedup key: signal `(review_id, target_product_id, edge_type, dst_id, polarity,
  negated, qualifier_fingerprint, registry_version)` / fact `(review_id, subject_iri,
  predicate, object_ref, polarity, qualifier_fingerprint)`

## 8. 파이프라인 운영 구조

- 실행: full load([run_full_load_db.py](../src/jobs/run_full_load_db.py)) /
  incremental([run_incremental_pipeline_db.py](../src/jobs/run_incremental_pipeline_db.py)).
  **라이브러리 엔트리포인트만 존재** — CLI/스케줄러 없음, 데모 웹의
  `/api/pipeline/run` 또는 스크립트로 수동 트리거
- **watermark 증분 커서**: `(updated_at, review_id)` 전순서 커서. 스킵 리뷰 발생 시
  안전 진행점만 기록(early-stop) → 다음 run에서 재처리
- **advisory lock**: `pg_try_advisory_lock` 고정 키로 FULL↔INCREMENTAL cross-mutex.
  pool max_size ≥ 2 강제(데드락 방지), lock_holder_pid 기록
- idempotency: 동일 fixture 재적재 시 행 중복 없음 (test_idempotency 검증)
- cleanup: `agg_*` 90일 stale → is_active=false, 단
  `GRAPHRAPPING_AGG_CLEANUP_ENABLED=1` **opt-in**
- 리뷰 처리 루프는 순차(내부 병렬화 없음), 집계는 SQL 배치(batch_size=1000)

## 9. 웹/API (데모)

- FastAPI + 전역 in-memory `DemoState` 싱글톤 ([state.py:67](../src/web/state.py))
- `/api/recommend`는 `demo_state.serving_products/users` 메모리 순회 —
  **server.py에 DB 접근 코드 0건** (직접 grep 확인)
- `/api/recommend`는 결과별 `eligibility`(evidence_families, *_paths,
  rejection_reasons) 반환 — 테스터가 근거 유형을 눈으로 검증 가능
- 검색 API 없음

## 10. 테스트/픽스처 체계

- 테스트 110개 파일: mock 단위 + Postgres 통합(ephemeral 컨테이너 or 로컬 DB)
- fixture 이원화 ([DECISIONS/2026-06-22](../DECISIONS/2026-06-22_dense_golden_fixture_and_semantic_evidence_matching.md)):
  - **wide 906/517**: source identity, 조인 계약, 파이프라인 회귀용
  - **dense golden 33상품**: 추천/승격 품질용 (상품당 평균 ~27.5 리뷰)
- 품질 감사 자산: [scripts/audit_recommendation_evidence.py](../scripts/audit_recommendation_evidence.py),
  tests/test_golden_profile_recommendation_audit.py — evidence-family/coverage/score-layer 검증
- 미커버: 실 스케일 성능, 장애 주입, quarantine 누적 시나리오

## 11. 스택/의존성

```
python >= 3.11
asyncpg, fastapi, pydantic, pyyaml, python-dotenv, uvicorn   # 런타임 전부
pytest(+asyncio/cov/timeout), ruff, mypy                     # dev
```

그래프 DB, 임베딩/ML, 스케줄러, 메트릭 라이브러리 **전무** — 시스템의 모든 "시맨틱"은
사전/규칙, 모든 "그래프"는 Postgres 트리플 테이블 + 집계로 구현되어 있다.

## 12. 다운스트림/업스트림 경계

- upstream: inference-gerter(NLP) → rs.jsonl, personal-agent → 유저 프로필
- downstream: AmoreSimulation이 [db_consumer_contract.md](../docs/architecture/db_consumer_contract.md)
  기준으로 read-only 소비 (write-back 금지, promoted-only + is_active 필터)
- consumer contract 검증: [contract_validator.py](../src/db/contract_validator.py)::validate_all
