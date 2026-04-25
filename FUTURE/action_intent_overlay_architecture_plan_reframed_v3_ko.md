# Action / Intent Overlay Layer 아키텍처 및 구현 계획 보고서 (재구성본 v2)

## 0. 목적

이 문서는 GraphRapping에 **Action / Intent Overlay Layer**를 추가하기 위한 아키텍처와 구현 계획을 정리한다.

전제:
- 기존 리뷰 추출 자산(`NER / BEE / REL`)은 고정
- 기존 Stable Profile 추출은 유지
- 새 모델은 현재 액션/의도만 추론
- 리뷰는 user-action 주 입력이 아니라 auxiliary / product outcome source


## 0-1. 이번 v3 보강에서 추가로 못 박는 원칙

이 문서에서 Overlay Layer는 아래 3가지를 전제로 한다.

### 원칙 A. chat에 대해 리뷰용 NER/BEE/REL 성능을 전제하지 않는다
Overlay Layer는 chat raw를 리뷰용 추출 모델에 직접 넣는 것을 기본 파이프라인 전제로 삼지 않는다.

### 원칙 B. 새 모델의 Target은 extraction이 아니라 grounding/selection이다
Overlay Layer의 모델은 새 엔티티를 다시 추출하지 않는다.  
기존 stable profile, dictionary/linker, session context, 행동 로그가 만든 candidate target들 중에서 현재 액션의 타깃을 판정한다.

### 원칙 C. Overlay는 promoted review signal과 운영 규칙이 다르다
Overlay signal은 GraphRapping의 Common Concept Plane과 Recommendation/Explanation 단계에는 통합되지만, 리뷰 코퍼스의 promoted-signal 파이프라인과 같은 승격 규칙을 따르지 않는다.  
Overlay는 **TTL / recency / source-weight / session persistence** 규칙으로 관리한다.

## 1. 왜 Overlay Layer가 필요한가

현재 GraphRapping은 Stable Profile을 잘 다룬다.

예:
- 선호 브랜드
- 선호/기피 성분
- concern
- goal
- texture preference
- owned / repurchase history

하지만 이것만으로는 아래가 약하다.

- 지금 탐색 중인지
- 지금 비교 중인지
- 지금 살 마음이 있는지
- 지금 회피하려는 것이 있는지
- 지금 어떤 family / 브랜드 / 제형에 관심이 쏠려 있는지

이걸 별도 Overlay Layer로 추가해야 recommendation policy가 더 정교해진다.

## 2. Overlay Layer의 역할

### 2.1 Stable Profile이 하는 일

- 장기 선호 / 반장기 선호
- 상태 정보
- 누적 구매/보유 이력

### 2.2 Overlay Layer가 하는 일

- 현재 세션의 intent stage
- 현재 액션 타입
- 현재 관심/비교/구매의사 대상
- 짧은 수명의 회피/선호 변화

### 2.3 Outcome Layer와의 차이

리뷰는 Overlay가 아니라 Outcome / Market Signal Layer에 가깝다.

- repurchase mentions
- advocacy mentions
- avoidance mentions
- concern resolution mentions

이건 product-side signal로 주로 쓴다.

## 3. 시스템 내 위치

### Layer 0
- `product_master`
- `user_master`
- `purchase_event_raw`

### Layer 1
신규 raw sources 추가
- `chat_turn_raw`
- `browse_event_raw`
- `cart_event_raw`
- existing `purchase_event_raw`

### Layer 2
신규 canonical family
- `canonical_user_action_fact`

예:
- `HAS_INTENT_STAGE(user, compare)`
- `SHOWS_INTEREST_IN_BRAND(user, brand_x)`
- `COMPARES_FAMILY(user, family_y)`
- `HAS_PURCHASE_INTENT_FOR_PRODUCT(user, product_z)`

### Layer 3
신규 aggregate
- `agg_user_action_signal`

### Layer 3 serving
- `serving_user_profile.current_intent_stage`
- `serving_user_profile.current_interest_targets`
- `serving_user_profile.current_compare_targets`
- `serving_user_profile.current_purchase_intent_targets`
- `serving_user_profile.current_avoid_targets`

### Layer 4 recommendation
- candidate generation
- rerank boost/penalty
- explanation
- next-best-question

## 4. Stable Profile과 절대 섞지 않는 규칙

### 4.1 predicate family 분리

Stable profile predicates 예:
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `PREFERS_INGREDIENT`
- `AVOIDS_INGREDIENT`
- `HAS_CONCERN`
- `WANTS_GOAL`
- `PREFERS_BEE_ATTR`
- `PREFERS_KEYWORD`

Overlay predicates 예:
- `HAS_INTENT_STAGE`
- `SHOWS_INTEREST_IN_BRAND`
- `SHOWS_INTEREST_IN_CATEGORY`
- `SHOWS_INTEREST_IN_PRODUCT_FAMILY`
- `COMPARES_PRODUCT`
- `COMPARES_FAMILY`
- `HAS_PURCHASE_INTENT_FOR_PRODUCT`
- `HAS_PURCHASE_INTENT_FOR_FAMILY`
- `SHOWS_AVOIDANCE_FOR_*`

### 4.2 TTL 분리

- stable profile: long TTL
- overlay: short TTL
- purchase outcome: medium/long TTL

예:
- intent stage: 1~3일
- compare target: 1일
- purchase intent: 1~7일
- repurchase intent: 7~30일

### 4.3 scorer 역할 분리

- stable = base prior
- overlay = dynamic boost/penalty
- purchase outcome = loyalty / novelty / reorder

## 5. candidate target 생성 전략

새 모델은 target을 새로 추출하지 않는다.
기존 시스템 또는 로그 메타에서 후보를 모아놓고, 그중 어떤 것이 현재 액션의 타깃인지 판정한다.

즉 Overlay Layer는 아래 두 단계로 본다.

- **Candidate Target Builder**: 기존 시스템과 로그 메타에서 후보를 만든다
- **Action / Intent Model**: 그 후보들 중 현재 액션/의도의 타깃을 판정한다

### 5.1 chat 입력일 때
후보 source:
- stable profile snapshot
- entity/concept linker
- session context (최근 본 상품/패밀리, 장바구니, 비교 대상)

### 5.2 browse/search/click/cart일 때
후보 source:
- 로그 메타 자체
- query text parsing 결과
- filter facet 값

### 5.3 purchase일 때
후보 source:
- purchased product_id
- family_id
- brand_id
- category_id

### 5.4 review일 때
리뷰는 overlay 주 입력 아님.
lexicon / market signal 보조용.


## 5-1. chat 입력에서 candidate target을 만드는 권장 순서

chat 입력은 리뷰용 `NER / BEE / REL` 모델을 그대로 적용하는 경로보다 아래 순서를 기본으로 한다.

1. stable profile snapshot에서 후보를 수집  
   - preferred/avoided brand, category, ingredient, concern, goal, BEEAttr, keyword
2. dictionary / entity linker로 chat surface를 후보에 매핑
3. session context에서 최근 viewed/clicked/compared/carted family/product를 후보에 추가
4. 필요 시 lightweight text matcher로 보강

이 과정을 거친 뒤 Action / Intent 모델은 candidate target별로 `is_targeted`, `intent_stage`, `action_type`, `strength`를 판정한다.

## 6. 아키텍처 컴포넌트 제안

### `src/action/` 패키지 신설

권장 모듈:

- `action_signal_schema.py`
  - dataclass / pydantic schemas
- `candidate_target_builder.py`
  - source별 candidate target 생성
- `action_weak_labeler.py`
  - 규칙 기반 silver label 생성
- `action_model.py`
  - baseline model wrapper
- `action_target_linker.py`
  - target_id grounding
- `action_to_canonical_facts.py`
  - 모델 출력 → canonical_user_action_fact 변환
- `aggregate_user_action_signals.py`
  - overlay aggregate

## 7. data contracts

### 7.1 raw tables
신규 테이블 제안:
- `chat_turn_raw`
- `browse_event_raw`
- `cart_event_raw`

### 7.2 canonical fact
신규 predicate families:
- `HAS_INTENT_STAGE`
- `SHOWS_INTEREST_IN_*`
- `COMPARES_*`
- `HAS_PURCHASE_INTENT_FOR_*`
- `SHOWS_AVOIDANCE_FOR_*`

### 7.3 aggregate
- `agg_user_action_signal`

### 7.4 serving profile fields
- `current_intent_stage`
- `current_interest_brand_ids`
- `current_interest_category_ids`
- `current_interest_family_ids`
- `current_compare_targets`
- `current_purchase_intent_targets`
- `current_avoid_targets`
- `intent_strength`
- `intent_horizon`

## 8. candidate/scorer/explainer 반영 전략

### 8.1 candidate generation
- stable prior로 broad candidate 생성
- overlay가 있으면 current targets / compare targets / purchase intent targets 우선

### 8.2 scoring
권장 추가 feature:
- `intent_stage_alignment`
- `current_interest_overlap`
- `current_compare_target_alignment`
- `purchase_intent_target_boost`
- `avoidance_penalty`

### 8.3 explanation
예:
- "최근 세션에서 헤라 블랙 쿠션과 클리오 킬커버를 직접 비교하고 있어 비교 설명에 유리한 후보를 올렸습니다."
- "지금은 구매 의사 단계로 보여 family-level 후보를 좁혀 제안했습니다."

## 9. 구현 순서

### Phase 1
- schema 고정
- candidate target builder 구현
- weak labeler 구현
- JSONL dataset 생성

### Phase 2
- baseline model or rule engine 구축
- output → canonical_user_action_fact 변환

### Phase 3
- aggregate / serving profile 연결
- candidate/scorer/explainer 연결

### Phase 4
- calibration / TTL / cleanup
- A/B or offline replay evaluation

## 10. 최소 acceptance criteria

1. Stable profile predicate와 overlay predicate가 섞이지 않는다.
2. Overlay는 user-specific current state만 표현한다.
3. chat/browse/purchase에서 생성된 overlay가 serving_user_profile에 반영된다.
4. 추천 결과가 동일한 stable profile이라도 overlay에 따라 달라진다.
5. review 입력이 없어도 action model은 동작한다.

## 11. 최종 정리

이 Overlay Layer는 기존 시스템을 부정하지 않는다.
오히려 GraphRapping의 현재 강점(Stable Profile + product truth + review signal)을 유지하면서, 그 위에 "지금 무엇을 하려는가"를 얹어 추천/탐색/설명 품질을 높이는 역할을 한다.
