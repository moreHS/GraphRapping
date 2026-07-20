# GraphRapping 개발 과정 — 논의·결정·실행 통합 기록

최종 갱신: 2026-07-19 · 성격: 시간순 내러티브(각 단계의 **왜**와 **무엇**을 잇는
색인). 상세 근거는 각 링크(DECISIONS/, fable_doc/plans/, docs/architecture/)가 정본.

## 0. 시스템이 푸는 문제

한국어 화장품 리뷰 NLP 산출(rs.jsonl, 외부 inference-gerter 모델) →
**3레이어 지식그래프**(raw evidence → canonical fact/wrapped signal →
aggregate/serving) → **근거-우선(evidence-first) 추천·검색·개인화** 데모.
관통 원칙: *근거 없는 추천은 내보내지 않는다* — 모든 추천은 evidence family로
자격을 얻고, 모든 설명은 신호→fact→원문으로 역추적 가능해야 한다(§5 provenance).

## 1. 소스 정합 베이스라인 (2026-06 초~중순)

- 906리뷰/517상품/50유저 소스 정합 픽스처 확립 — 상품 식별은
  `source_channel + source_key_type + source_product_id` 복합키
  ([결정](../DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md),
  [AmoreSim 연동](../DECISIONS/2026-06-17_product_source_identity_amoresim_integration.md)).
- mockdata 상품 id를 실 5자리 코드로 교체(06-10) → 외부 시스템 join 가능해짐.

## 2. Evidence-first 추천 확립 (2026-06-19~23)

- **핵심 결정**: 후보 자격은 evidence family의 OR —
  카탈로그 진실/리뷰그래프/약한 semantic/구매행동. source-stats 단독 자격 금지
  ([결정](../DECISIONS/2026-06-19_evidence_first_personalization_recommendation.md)).
- scoped preference(맥락별 선호), "활동 카테고리 ≠ 선호" 구분, dense golden
  픽스처와 semantic 매칭 정비(06-22~23 DECISIONS 일련).

## 3. fable 종합 업그레이드 Phase 0~4 (2026-07-07~10)

품질·식별·서빙·semantic·검색 일괄 업그레이드
([마스터 플랜](03_improvement_plan.md), 커밋 `c5239ed`+`4762d4a`).
결정 라운드: **glb 채널 식별 D안**(`name_hint` 마커,
[결정](../DECISIONS/2026-07-08_glb_channel_identity_strategy.md)) ·
**retention R=24개월**([결정](../DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md)) ·
multi-hop CTE는 실수요 audit로 보류
([audit](../DECISIONS/2026-07-08_multihop_graph_demand_audit.md)) ·
0.5 랭킹 라벨 전략은 사용자 숙고 보류(현재도 유지).

## 4. Phase 6 — 서비스형 프론트 + LLM 쿼리 이해 (2026-07-10)

25개 가중치 슬라이더 노출 문제 → **의도 프리셋 3종**(균형/신뢰/발견) + 개발자
모드 분리, 카드 인라인 "왜 이 추천" 그래프, `/api/ask` LLM 쿼리 이해(Azure/사전
폴백)와 **한국어 부정 처리**("레티놀 없는" — 보수적 전처리+성분축 검증)
([결정](../DECISIONS/2026-07-10_phase6_service_frontend_decisions.md),
커밋 `162ceab`+`4347861`).

## 5. Phase 7 — 그래프 지능화 (2026-07-13~15)

**진단**([06_graph_ontology_assessment.md](06_graph_ontology_assessment.md)):
연결성 신호의 추천 기여 0/140, 링킹 미해소 상위가 한국어 굴절형, wide 서빙
도달 5%로 붕괴. → [계획](plans/2026-07-13_phase7_graph_intelligence.md)
(커밋 `c71ab7d`+`64258a6`):

- **Track A 죽은 배선 소생**: comparison을 boost-only 버킷으로(§13 계약 신설 —
  단독 자격 불가), COMPARE 모드 실동작(모드 스레딩), 온톨로지 validator v2.
- **Track B 링킹 바닥**: 해소 경로 이중화 통합 + **보수적 한국어 어미 접기**
  (사전 멤버십 게이트·부정 가드 — 잘못 접기 0 설계) → 격리 -25%,
  BEE_KEYWORD 신호 238→802 ([결정](../DECISIONS/2026-07-13_phase7_b1_keyword_path_unification.md),
  [별칭 레이어](../DECISIONS/2026-07-13_phase7_b2_keyword_alias.md)).
- **Track C**: concern/goal 타입 재해석(사전 exact-match만, 순가산) +
  **승격 게이트 D90/ALL 3→2** → wide 서빙 도달 5%→17.4%
  ([결정](../DECISIONS/2026-07-13_phase7_c2_promotion_gate.md)).
- **Track D**: user-user Jaccard(D1)·co-mention(D2) — 배선 완성·데이터 대기
  판정([D1](../DECISIONS/2026-07-14_phase7_d1_user_similarity_collaborative.md),
  [D2](../DECISIONS/2026-07-14_phase7_d2_comention.md)).
- Fable 종합리뷰(07-16) **APPROVE** — boost-only 이중 게이트·스냅샷 불변 계약
  코드 수준 검증.

## 6. Phase 8 — 공유노드 상품 유사도, G1~G5 (2026-07-15~16)

**발단(사용자 통찰)**: "A상품의 '촉촉'과 B상품의 '촉촉'은 같은 노드 — 그걸로
2홉/멀티홉 연결이 왜 안 되나." →
[설계 논의록](../DECISIONS/2026-07-15_phase8_shared_node_design_dialogue.md)
(정정 3단계 포함)에서 확정된 결정:

1. **유사도 = 공유노드 IDF 가중 합 + top-N** — 하드AND·노드병합·하드게이트 없음.
2. **category_gate는 소비 맥락 파라미터** — 유사상품 ON / 일반추천 OFF(다양성) /
   쿼리 상류. "유사상품 추천과 그냥 추천은 다른 것".
3. **keyword 복합키** `keyword::{bee_attr}:{keyword}:{polarity}` — "가볍다"가
   제형/발림성/패키지에서 다른 의미이므로 bee_attr로 스코핑(사용자 재확정 —
   병합안 기각). 서빙이 bee_attr·polarity를 버리므로 **raw wrapped_signal
   sidecar 소싱**(3자 재리뷰 Opus/Sonnet/Codex가 잡은 사실제약의 해소).
4. 브랜드/카테고리 허브는 **IDF 자동 감쇠**(이니스프리 186상품 → 최저 가중) —
   하드 배제 없음.

실행([계획+완료보고](plans/2026-07-15_phase8_shared_node_projection.md), 커밋 8개):
- **P8-1 G1** 계산 모듈 — 실측: gate-ON 동일카테고리 100%, 커버리지 99%,
  복합키 분리 실증(kw_moisturizing이 7개 bee_attr로).
- **P8-2** 활성화 훅(웹+demo) + **G2** 그래프 SHARES_ATTRIBUTE 엣지·근거 툴팁 +
  **G3** `/similar` 위젯 — 브라우저 실증.
- **P8-3a G4** 일반추천 boost — 스토어 사이드카(프로파일 attach 금지, API 오염
  방지), retrieval 집계 제외, 4중 캡(포화30·가중0.02·boost-only·집계 제외).
  [코덱스 12건 반영 계획 v2](plans/2026-07-16_phase8-3_g4_similar_boost_g5_query_related.md).
  교훈: **"dense diff=0" 전제가 원천≠서빙 외삽으로 틀림** — 구현자가 착수 전
  시뮬로 잡아 중단·보고, 사용자 재승인 후 진행(diff는 예측대로 1유저 범위).
- **P8-3b G5** 검색/ask "관련 상품 더보기" — hard exclusion 보존(기피 성분
  재유입 차단), ask-search 쿼리 부정 누출은 Fable 리뷰가 잡아 수정.

## 7. 마감 스윕 + 스케일 측정 (2026-07-18)

[잔여 검토](08_remaining_items_review_2026-07-18.md) 실측 기반 실행
(커밋 `ebb4b8c`+`56371ed`):
- **A1**: boost-only 4종 retrieval 집계 통일 — 발화 0 실측으로 무비용 시점 포착.
- **A2**: **브랜드 단독 공유 이웃 미노출**(제거 15.8% — 유사상품의 16%가
  "브랜드만 같은 짝"이었음이 정책을 역으로 입증,
  [결정](../DECISIONS/2026-07-18_phase8_brand_only_neighbor_policy.md)).
- **A3**: 유사도 빌드 10k 상품 ~39s(브랜드 멱법칙 지배) 측정 → **사용자 판정:
  사전 계산 방식이므로 수용**, df-cap 보류(실데이터 전환 시 refresh
  백그라운드화만 등재).

## 8. 구매이력 백필 — 실데이터 연결 (2026-07-18)

G4/D1의 병목 = owned 엣지(합성 1/50). 사용자 지시로 **개인화 에이전트의 PG
원본 경로** 사용([결정+완료보고](../DECISIONS/2026-07-18_purchase_history_backfill.md),
커밋 `ab42a67`+`cf36944`):

- **유저 매칭을 하지 않는 설계**: 실유저와 합성 유저를 1:1 매칭하지 않음 —
  실유저 50명이 자기 프로파일 전체(스킨/구매/챗 요약, 동일 정규화)를 가지고
  **유저 평면을 통째로 교체**(opt-in env). 매칭이 필요한 유일한 지점은
  **상품 축**: 구매 `rprs_prd_cd`(9자리 대표상품코드) ↔ 카탈로그
  `REPRESENTATIVE_PROD_CODE`(=variant_family_id), ~53% 해소, 미일치 드롭.
- **발생 1회 = 이벤트 1건**: 패밀리→멤버 SKU 전개를 이벤트로 만들면 기존
  파생이 가짜 재구매 fact를 생성(코덱스 크로스리뷰가 재현) → 결정적 대표
  SKU 1건으로 수정, 테스트 고정.
- **프라이버시 설계**: 가명화(incs_no 해시 12자, 충돌 시 중단), 출력은
  git-ignored `mockdata/real/`만(0600 원자 쓰기), 자격증명은 타 프로젝트
  .env 경로 참조만, 커밋 문서는 집계 수치만. 루프백 전용 운영 제약.
- **결과**: owned 39/50 유저 → **G4 boost 실발화 39/39** — Phase 8이 실구매
  데이터로 살아있음을 실증. D1 collab은 데이터 준비 완료(활성화는 별도 결정).

## 9. 입력 커넥터 트랙(IC) — 실 DB 전환 준비 (2026-07-19~20)

서비스화 평가([10](10_service_transition_assessment.md))의 갭 #3 실행.
[계획 v2](plans/2026-07-19_input_connectors_readiness.md)(코덱스 8건 반영 —
"shape 어댑터 불요" 정정·누적 landing·9자리 완화 등) → Fable 실측 재검토 →
3배치 실행(각 배치 Opus 구현·Fable 완료검토):

- **IC-1**: 계약 검증기 4종(raw rs.jsonl/relation landing/상품/유저 — 골든
  픽스처 4종 무수정 통과, RS↔Relation 매핑 표 단일 진실) + staging 규약
  (`mockdata/real/{users,reviews,products}/`) + env 배선 2종(호출시점 해석,
  우선순위 명문화) + full-load `review_format`(raw 직접 소비) + CLI 구매 정합.
- **IC-2**: 리뷰 커넥터(리더 인터페이스+파일 백엔드+**누적 스냅샷 landing** —
  부분 코퍼스 금지, 동일 키 상이 payload hard-fail) + 상품 커넥터(검증+baseline
  diff 리포터 — 집계와 SKU id만). e2e: landing→env→데모/full-load 소비 증명.
- **IC-3**: GraphRapping 자체 **`.env` 일원화**(로더 의존성 0, os.environ 우선,
  실값 이관 완료) + **상품 ES 백엔드**(recommend-agent 접속 패턴 —
  REST ApiKey+search_after; 라이브 스모크 **실상품 ~45k** 확인) + 유저
  K=100(staging 최초 로드 완료, 매칭률 49.2%). **리뷰 백엔드만 대기**
  (inference-gerter relation 스텝 합류 후 — 정찰: SageMaker NER→BEE,
  Snowflake 테이블+S3 산출).

무파괴 계약 전 배치 검증: env 미설정 시 기존 경로 byte-identical, 스냅샷 diff 0.

## 10. 현재 상태와 보류 항목 (2026-07-20 기준)

- 게이트: ruff / mypy(120) / pytest **1431 passed, 50 skipped, 0 failed**.
  origin 동기화(`2204cb6`). 인수인계 기준점: 루트 `HANDOFF.md`.
- **재개 트리거 있는 항목**: 리뷰 백엔드(relation 합류 통보 시) · 유사도
  영속화+refresh 백그라운드화(실카탈로그 45k 전환 전 선행 권장) · retention
  (실데이터 연속 적재 시작) · glb 온보딩.
- **보류(결정 대기)**: 0.5 랭킹 라벨 전략(체계적 튜닝 개시 시 재상정) ·
  B3 임베딩(승인 초안 [05](05_embedding_model_approval_request_draft.md),
  제출은 사용자) · Track E 액션/인텐트 본체(외부 모델 스펙) ·
  **D1 collab attach 활성화**(실유저 owned 데이터 준비 완료 — 결정만, 켜면
  스냅샷 재승인 1회) · Track F 인사이트(수요 확인 시).
