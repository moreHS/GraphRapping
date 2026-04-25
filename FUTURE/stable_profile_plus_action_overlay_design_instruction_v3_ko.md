# Stable Profile Layer + Action / Intent Overlay Layer 통합 설계 지시서 (v2)

## 0. 지시 목적

이 문서는 Claude Code / 구현 담당자가 혼동 없이 다음 원칙으로 시스템을 확장하도록 지시하기 위한 상위 설계 문서다.

핵심:
- 기존 Stable Profile 추출은 유지
- 새 Action / Intent 모델은 별도 Overlay Layer
- 리뷰는 user-action 주 입력이 아님
- Overlay는 기존 Profile을 overwrite하지 않음


## 0-1. 이번 v3 보강에서 명시하는 추가 원칙

### 추가 원칙 A. chat 입력에 리뷰용 NER/BEE/REL 추출을 기본 전제로 두지 않는다
Action / Intent Overlay는 chat raw를 리뷰용 추출 자산에 직접 넣는 구조를 기본으로 삼지 않는다.

### 추가 원칙 B. Overlay의 Target은 candidate grounding / selection이다
Overlay 모델은 새 엔티티를 다시 뽑지 않는다.  
Stable Profile, dictionary/linker, session context, 행동 로그가 만든 후보들 중에서 현재 액션의 타깃을 고른다.

### 추가 원칙 C. Overlay는 리뷰 코퍼스 promoted-signal과 다른 운영 규칙을 따른다
Overlay signal은 GraphRapping의 concept plane과 recommendation/explanation stack에는 통합되지만, corpus promotion이 아니라 **TTL / recency / source-weight** 규칙으로 관리한다.

## 1. 절대 원칙

### 원칙 1. Stable Profile은 덮어쓰지 않는다

`PREFERS_*`, `HAS_CONCERN`, `WANTS_GOAL`, `OWNS_*`, `REPURCHASES_*`는 유지한다.

### 원칙 2. Overlay는 별도 family만 사용한다

새 모델의 출력은 반드시 overlay predicate family로 들어간다.

### 원칙 3. 리뷰는 product-side market/outcome layer로만 주로 쓴다

리뷰에서 나온 액션/인텐트 표현은 보조 lexicon 또는 product-side aggregate로 사용한다.
user-specific current intent를 리뷰에 기반해 직접 만들지 않는다.

### 원칙 4. Overlay는 current state이고 TTL이 짧다

stable profile처럼 오래 남기지 않는다.

## 2. 통합 구조

### Stable Profile
예:
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `PREFERS_INGREDIENT`
- `AVOIDS_INGREDIENT`
- `HAS_CONCERN`
- `WANTS_GOAL`
- `PREFERS_BEE_ATTR`
- `PREFERS_KEYWORD`
- `OWNS_PRODUCT`
- `OWNS_FAMILY`
- `REPURCHASES_BRAND`
- `REPURCHASES_FAMILY`

### Overlay Layer
예:
- `HAS_INTENT_STAGE`
- `SHOWS_INTEREST_IN_BRAND`
- `SHOWS_INTEREST_IN_CATEGORY`
- `SHOWS_INTEREST_IN_PRODUCT_FAMILY`
- `COMPARES_PRODUCT`
- `COMPARES_FAMILY`
- `HAS_PURCHASE_INTENT_FOR_PRODUCT`
- `HAS_PURCHASE_INTENT_FOR_FAMILY`
- `SHOWS_AVOIDANCE_FOR_*`

### Product Outcome / Market Signal
예:
- `HIGH_REPURCHASE_MENTION`
- `HIGH_RECOMMEND_MENTION`
- `HIGH_COMPARE_MENTION`
- `HIGH_CONCERN_RESOLUTION`


## 2-1. Candidate Target Builder를 별도 개념으로 둬야 하는 이유

Overlay 모델이 기존 시스템과 충돌하지 않으려면, 아래 두 단계를 명확히 분리한다.

### Candidate Target Builder
역할:
- Stable Profile에서 관련 concept 후보 수집
- chat surface를 dictionary / entity linker로 candidate concept에 매핑
- session context에서 최근 viewed / compared / carted target 추가
- browse/search/click/cart/purchase 로그에서 직접 target 확보

### Action / Intent Model
역할:
- Candidate Target Builder가 만든 후보들에 대해
- 현재 액션/의도의 타깃 여부를 판정
- intent stage / action type / strength / horizon을 산출

즉 Overlay는 extraction layer가 아니라 **grounding / selection overlay**다.

## 3. serving profile 설계

### serving_user_profile
반드시 stable slot과 dynamic slot을 분리한다.

#### stable slot
- skin type/tone
- concern_ids
- goal_ids
- preferred_brand_ids
- preferred_category_ids
- preferred_ingredient_ids
- avoided_ingredient_ids
- preferred_bee_attr_ids
- preferred_keyword_ids
- owned_product_ids
- owned_family_ids
- repurchase_brand_ids
- repurchase_family_ids

#### dynamic slot
- current_intent_stage
- current_interest_brand_ids
- current_interest_category_ids
- current_interest_family_ids
- current_compare_targets
- current_purchase_intent_targets
- current_avoid_targets
- current_intent_strength
- current_intent_horizon

## 4. 추천 시 결합 규칙

### base prior
stable profile로 계산

### dynamic boost
overlay로 계산

### outcome adjustment
purchase/reorder/loyalty/novelty로 계산

즉 최종 점수는:
- stable prior
- overlay boost/penalty
- purchase outcome
세 층 합성으로 간다.

## 5. 입력별 처리 원칙

### Chat
- Overlay의 주 입력
- stable profile snapshot과 같이 해석
- candidate targets는 **stable profile + dictionary/entity linker + session context**로 생성
- 리뷰용 `NER / BEE / REL` 추출 모델 성능을 기본 전제로 삼지 않음

### Browse/Search/Click/Cart
- Overlay의 핵심 입력
- intent stage / action type / target을 가장 잘 줌

### Purchase
- Overlay + outcome 둘 다 가능
- repurchase / reorder / loyalty source

### Review
- Overlay의 주 입력 아님
- product-side outcome / market signal
- action lexicon 보조용
- user-specific current intent를 직접 생성하지 않음

## 6. 구현 가드레일

### 금지
- Overlay 결과를 stable predicate로 저장
- 리뷰를 기반으로 user-specific current intent를 직접 생성
- overlay가 stable preference를 overwrite

### 허용
- overlay가 stable prior를 boost/penalty
- purchase outcome이 overlay confidence calibration에 기여
- review에서 lexicon/market signal 보조

## 7. 실제 적용 예시

### 예시 A
유저 stable profile:
- `PREFERS_BEE_ATTR(Texture)`
- `PREFERS_KEYWORD(GelLike)`
- `WANTS_GOAL(goal_moisturizing)`

현재 overlay:
- `HAS_INTENT_STAGE(COMPARE)`
- `COMPARES_FAMILY(family_hera_black)`
- `COMPARES_FAMILY(family_clio_killcover)`

추천 결과:
- stable prior로 보습/젤 제형 좋은 후보 확보
- overlay로 헤라/클리오 family 비교용 shortlist 생성

### 예시 B
stable:
- `REPURCHASES_FAMILY(family_laneige_cream)`

overlay:
- `HAS_INTENT_STAGE(REPURCHASE_INTENT)`
- `HAS_PURCHASE_INTENT_FOR_FAMILY(family_laneige_cream)`

추천 결과:
- same family replenish / variant 제안
- novelty는 약하게

## 8. acceptance criteria

1. stable profile과 overlay가 동일 predicate를 공유하지 않는다.
2. overlay는 current state 중심이며 TTL이 짧다.
3. recommendation이 stable only와 stable+overlay에서 다르게 동작한다.
4. review가 없어도 overlay pipeline은 완전 동작한다.
5. review는 product-side signal로는 활용되지만 user-specific current intent 생성엔 쓰이지 않는다.

## 9. 최종 메시지

이번 확장은 "기존 프로필 추출을 바꾸는 작업"이 아니다.
"현재 세션/행동의 동적 상태를 추가하는 작업"이다.

구현 담당자는 반드시 기존 Stable Profile Layer를 유지하고, 별도 Action / Intent Overlay Layer를 추가하는 방향으로만 작업해야 한다.
