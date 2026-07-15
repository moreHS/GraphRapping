# P7-4 D1 — user-user 유사도 (협업 신호 프로토타입)

작성: 2026-07-14 · 상위 계획: `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md` §D1 ·
진단 근거: `fable_doc/06_graph_ontology_assessment.md` §2 (U2 협업필터 = IMPLEMENTATION-absent)

## 배경 / 목적

"그래프여서 다른 결과"의 최단 경로 = 유저-유저 연결(similarity) → "취향이 비슷한
고객들이 선호한 상품" 신호. serving_user_profile의 선호 벡터는 이미 존재하고
계산 코드만 부재였다. G4 invariant는 reviewer proxy↔실유저 병합만 금지 —
실유저↔실유저 유사도는 정책 장벽 없음(확인). 프로토타입의 목적은 **배선 검증**.

## 결정 요약

1. **유사도 metric = Jaccard** (선호 id가 집합형이라 자연스러움; 코사인은 0/1
   지시벡터에서 분자는 동일하나 Jaccard의 합집합 분모가 "크지만 우연히 겹치는"
   프로필을 추가로 페널티 → 취향 유사 신호에 더 적합). 축은 실측으로 채워지고
   변별력 있는 것만: **concern/goal/brand/keyword/ingredient** (namespace 구분).
   제외: preferred_bee_attr(distinct 1 — 전원 공유, 변별력 0), context/category(빈값).
2. **콜드/희소 방어**: 두 유저가 최소 `min_common_prefs=3` 개의 *구체적* 선호를
   공유해야 이웃. 미달이면 빈 신호(정상 출력, 에러 아님).
3. **협업 후보 상품 = 이웃의 실제 user→product edge(owned_product_ids)에서만**.
   개념 선호는 *유사도*를, 실 product edge만 *후보 상품*을 만든다. 본인 소유 제외.
4. **evidence family**: P7-1a의 boost-only 버킷 **재사용** — `BOOST_ONLY_TYPES`에
   `collab` 추가. 단, comparison과 달리 **어느 모드에서도 단독 자격 불가**:
   신규 `BOOST_ONLY_ADMISSIBLE_TYPES = {comparison}` 도입으로 collab을 admission에서
   영구 제외. collab은 항상 first-class evidence에 올라타는 순수 부스트.
5. **scorer 가중 위치**: `collaborative_affinity`를 `features:` 맵에 넣지 않음
   (프론트 계약 테스트 `test_frontend_default_weights_match_yaml_features`가
   src/static/app.js 수정을 강요 → 금지 파일). comparison 우회와 동일하게 별도
   top-level 키 `collaborative_affinity_weight: 0.02`(coused_product_bonus 앵커,
   보수적)로 두고 scorer가 직접 읽어 **전 모드 적용**(comparison은 mode-scoped였음).
   contribution 키는 SCORING_FEATURE_KEYS 밖(comparison_alternative 선례와 동일).
6. **score layer**: 신규 layer 키 금지(`test_golden_profile_recommendation_audit`가
   score_layers 키셋 고정) → 기존 `review_graph_score` 그룹에 편입(comparison 이웃).

## 판정 — **배선 완성 + 실데이터 대기** (위임 규칙대로 자동 전환)

데모 50유저 실측(`src/rec/user_similarity` 직접 + audit 파이프라인 재현):

| 실측 | 값 |
|---|---|
| 선호 3개 이상 공유 유저쌍 (유사도 구조) | **234쌍** (median Jaccard 0.19, max 1.0) |
| 협업 신호 ≥1 보유 유저 | **17/50** |
| **distinct 협업 후보 상품** | **1개 (`58763`)** |
| user→product edge 보유 유저 (owned_product_ids) | **1/50** |
| top-10에 collab overlap 등장 (50유저×7탭=350 시나리오) | **4행** (전부 상품 58763, makeup 탭, antiaging 유저) |

**해석**: 유저-유저 **유사도 구조는 풍부**(234쌍). 그러나 유사도를 추천으로
바꾸는 **product-edge 층이 데이터 부재**(전 fixture에서 1유저가 1상품 소유).
→ 17유저의 협업 신호가 전부 그 단일 상품(58763)으로 붕괴. top-N 발화 4건도 전부
동일 상품. 이는 유의미한 협업 시연이 아니라 **단일 소유자 아티팩트**.

**배선은 end-to-end 검증됨**: collab overlap 생성 → boost-only 자격(단독 실격) →
strength 채널 스코어링 → top-N 등장 → 설명 문구, 전 경로가 실측 발화. 실 유저
액션/구매 스트림(user→product edge)이 채워지는 순간 그대로 활성화된다.

## 기본 경로 불변 (byte-identical) 증거

- 활성화 진입점 `attach_collaborative_signals`는 서빙/audit 파이프라인에서
  **호출되지 않음**(server.py는 D1 파일 범위 밖). 따라서 committed 파이프라인엔
  `collaborative_product_ids` 필드 부재 → candidate_generator가 `collab` overlap
  0개 생성 → `collaborative_affinity` 기여 0 → 스냅샷/기대셋 불변.
- 검증: `test_ranking_snapshot_regression`(dense+wide) green, `dense_golden.json`
  diff에 collab 문자열 0건, `scoring_weights.yaml`의 `features:` 맵 무변경(신규
  키는 top-level). 가중 0.02가 nonzero여도 overlap 부재로 기여 0 → 불변 보장.
- 게이트: ruff/mypy(115) clean, pytest **1174 passed, 50 skipped, 0 failed**
  (1150 기준 +24 신규 테스트).

## Follow-up

1. **활성화 배선(별도 작업, server.py 소유)**: serving load / audit에서
   `attach_collaborative_signals(serving_users)` 호출 → 실데이터에서 다중
   product-edge가 생기면 골든 스냅샷 diff는 "의도 변경 재승인" 워크플로우로 처리.
2. Track E(액션/인텐트 스트림)가 user→product behavior edge를 공급하면 D1이
   즉시 유의미해진다 — D1은 그 변곡점의 사전 배선.
3. co-mention 상품-상품(D2)은 리뷰 self-join으로 데이터 없이 계산 가능 —
   협업 신호 중 데이터 충족도가 D1보다 높을 수 있음(P7-4 병렬 항목).
