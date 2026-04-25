# Action / Intent 모델 학습 설계 보고서 (재구성본 v2)

## 0. 전제 재정의

이 보고서는 아래 전제를 고정한다.

1. 기존 리뷰 추출 자산(`NER / BEE / REL`)은 **재학습하지 않는다**.
2. 기존 personal-agent 기반 Stable Profile 추출(`basic / purchase_analysis / chat`)은 **유지**한다.
3. 새로 추가하는 것은 **Action / Intent Overlay 전용 모델**이다.
4. 리뷰는 **유저별 실시간 액션/인텐트 모델의 주 입력이 아니다**.
5. 주 입력은 **chat / search / browse / click / cart / purchase log**다.
6. 리뷰는 **product-side outcome / market signal**, 그리고 action lexicon 보조 자산으로만 쓴다.



## 0-1. 이번 v3 보강에서 명시적으로 고정하는 해석

이 문서의 핵심 오해 방지 규칙은 아래 4개다.

1. **새 Action / Intent 모델은 chat 입력에 대해 리뷰용 `NER / BEE / REL` 추출 모델 성능을 전제하지 않는다.**
2. 새 모델의 `Target`은 **새로 추출(extraction)** 하는 것이 아니라, 기존 시스템/사전/로그 메타/세션 컨텍스트로 만든 **candidate target** 중에서 **grounding / selection** 하는 문제다.
3. 리뷰는 user-specific current intent 추론의 주 입력이 아니다.
4. Overlay Layer는 GraphRapping의 Common Concept Plane 및 Recommendation/Explanation 단계에는 통합되지만, 리뷰 코퍼스의 promoted-signal 파이프라인과는 **다른 TTL / recency / source-weight 정책**으로 운영한다.

## 0-2. chat 입력에 대한 전제 명시

현재 시스템에서 `NER / BEE / REL` 추출 자산은 **리뷰 문장**에 대해 검증된 자산이다.  
따라서 chat에 대해서는 아래를 기본 원칙으로 삼는다.

- chat raw를 리뷰용 `NER / BEE / REL` 모델에 직접 넣는 것을 **기본 파이프라인 전제로 삼지 않는다**
- chat에서는
  - stable profile snapshot
  - dictionary / entity linker
  - session context
  - browse / search / click / cart / purchase metadata
  로 **candidate target space**를 먼저 만들고,
- Action / Intent 모델은 이 후보들에 대해 현재 액션/의도의 타깃 여부를 판정한다.

즉 chat 측 Action / Intent 모델은 **candidate-conditioned grounding model**이다.

## 1. 왜 문서를 재구성하는가

초기 설계에서는 Action / Intent 모델의 `Target Object`가 마치 새 모델이 `Brand / Category / Ingredient / Concern / Goal / BEEAttr / Keyword`를 다시 **추출**하는 것처럼 읽힐 수 있었다.

하지만 현재 GraphRapping의 공식 입력 계약과 시스템 구조를 기준으로 보면, 이건 맞지 않다.

- 제품 truth는 `product_catalog_es.json` → `load_products_from_json()` 경로로 들어온다.
- 유저 프로필 truth는 `user_profiles_normalized.json` → `load_users_from_profiles()` 경로로 들어온다.
- 리뷰는 `review_triples_raw.json` 또는 `review_rs_samples.json` 기반으로 별도 evidence 경로를 탄다.
- raw user 7-column은 reference-only이며, 현재 loader 직접 입력은 normalized user profile이다.

즉 새 모델은 기존 추출기를 대체하면 안 되고, **기존 추출/링킹 결과 위에 현재 세션/행동의 동적 의도와 타깃을 판정하는 overlay** 여야 한다.

## 2. 새 모델의 목적

새 모델의 목적은 아래 하나로 요약된다.

> "이 유저가 지금 무엇을 하려는가, 그리고 그 액션/의도가 현재 무엇을 향하고 있는가"를 추론한다.

이 모델이 잘하면 다음이 가능해진다.

- 탐색 단계(`DISCOVER`)와 구매 직전(`PURCHASE_INTENT`)을 구분한 추천 정책
- 같은 stable profile을 가진 유저라도 현재 세션 상태에 맞는 다른 결과 제공
- compare mode, repurchase mode, avoidance mode 같은 동적 정책 분리
- 더 자연스럽고 현재 상황을 반영한 explanation / next-best-question 생성

## 3. Stable Profile vs Action Overlay의 역할 분리

### 3.1 Stable Profile Layer (기존 유지)

이 레이어는 장기적 또는 반장기적 선호와 상태를 담는다.

예:

- `HAS_SKIN_TYPE`
- `HAS_SKIN_TONE`
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

### 3.2 Action / Intent Overlay Layer (신규)

이 레이어는 짧은 수명의 현재 상태를 담는다.

예:

- `HAS_INTENT_STAGE(COMPARE)`
- `HAS_INTENT_STAGE(PURCHASE_INTENT)`
- `SHOWS_INTEREST_IN_BRAND`
- `SHOWS_INTEREST_IN_CATEGORY`
- `SHOWS_INTEREST_IN_PRODUCT_FAMILY`
- `COMPARES_PRODUCT`
- `COMPARES_FAMILY`
- `HAS_PURCHASE_INTENT_FOR_PRODUCT`
- `HAS_PURCHASE_INTENT_FOR_FAMILY`
- `SHOWS_AVOIDANCE_FOR_BRAND`
- `SHOWS_AVOIDANCE_FOR_INGREDIENT`

### 3.3 두 레이어를 절대 섞지 말아야 하는 이유

예를 들어,

- `preferred_texture = [젤, 가벼운 로션]` 은 stable profile이다.
- `지금 헤라 vs 클리오 비교 중` 은 action overlay다.
- `이번 주 안에 하나 사고 싶음` 은 purchase intent overlay다.

이걸 한 레이어에 섞으면

- 일시적 관심이 영구 선호처럼 남고
- 현재 비교 대상이 stable preference로 오염되고
- explanation에서 "원래 선호"와 "지금 관심"을 구분할 수 없게 된다.

따라서 새 모델의 출력은 반드시 별도 predicate family로 들어가야 한다.

## 4. 새 모델의 문제 정의

### 4.1 새 모델이 하지 않는 일

새 모델은 다음을 **하지 않는다**.

- Product/Brand/Category/Concern/Goal/BEEAttr/Keyword를 새로 NER처럼 추출
- 기존 리뷰 NER/BEE/REL 자산 재학습
- 기존 stable profile overwrite

### 4.2 새 모델이 하는 일

새 모델은 다음을 **한다**.

- 현재 입력(chat/log)에 대해 Intent Stage를 분류
- Action Type을 분류
- 기존 candidate target들 중 어떤 것이 현재 액션의 타깃인지 선택/정렬
- Strength / Horizon / Explicitness / Polarity를 산출

즉 **candidate-conditioned action model**로 보는 게 가장 정확하다.

## 5. 타깃 클래스 설계

### 5.1 Head A: Intent Stage

권장 클래스:

- `NONE`
- `DISCOVER`
- `INTEREST`
- `CONSIDER`
- `COMPARE`
- `PURCHASE_INTENT`
- `REPURCHASE_INTENT`
- `AVOID`
- `POST_PURCHASE_HELP`

설명:

- `DISCOVER`: 아직 넓게 둘러보는 단계
- `INTEREST`: 특정 축에 관심이 생긴 단계
- `CONSIDER`: shortlist를 만들기 시작한 단계
- `COMPARE`: 2개 이상 비교 중인 단계
- `PURCHASE_INTENT`: 곧 살 가능성이 높은 단계
- `REPURCHASE_INTENT`: 이미 써봤고 다시 살 마음이 있는 단계
- `AVOID`: 피하고 싶은 상태
- `POST_PURCHASE_HELP`: 산 뒤 사용법/문제 해결 단계

### 5.2 Head B: Action Type

권장 클래스:

- `ASK_RECOMMENDATION`
- `ASK_COMPARISON`
- `ASK_INFO`
- `SEARCH`
- `VIEW_DETAIL`
- `CLICK`
- `FILTER`
- `SAVE`
- `ADD_TO_CART`
- `PURCHASE`
- `REORDER`
- `MENTION_LIKE`
- `MENTION_DISLIKE`
- `MENTION_BUY_INTENT`
- `MENTION_REPURCHASE_INTENT`
- `MENTION_AVOIDANCE`

### 5.3 Head C: Target Grounding / Selection

이 Head는 **새 객체 추출**이 아니다.

정확한 해석은 다음과 같다.

- 기존 시스템(Stable Profile / entity-concept linker / session context / log metadata)이 만든 candidate target들을 입력으로 받는다.
- 모델은 그 후보들 중 **어떤 것이 현재 액션/의도의 타깃인지**를 판정한다.
- 따라서 이 Head는 extraction head가 아니라 **candidate-target resolution head**다.

출력은 다음으로 정의한다.

- `is_targeted`: bool
- `target_type`: 하나의 namespace (`Product`, `ProductFamily`, `Brand`, `Category`, `Concern`, `Goal`, `Ingredient`, `BEEAttr`, `Keyword`, `Context`, `Tool`)
- `target_id`: 기존 시스템이 이미 알고 있는 ID / concept_id / entity ref
- optional `target_role`: `primary`, `secondary`, `compare_target`, `filter_target`

즉 **기존 추출/링킹 결과 또는 로그 메타로 얻은 candidate target들에 대해 현재 액션이 무엇을 향하는지 판정**하는 문제다.

### 5.4 Head D: Strength / Horizon / Explicitness / Polarity

권장 출력:

- `strength`: 0.0 ~ 1.0
- `horizon`: `NOW | SOON | LATER`
- `explicitness`: `EXPLICIT | IMPLICIT`
- `polarity`: `POS | NEG | MIXED`


## 5-1. candidate target 생성 경로를 분리해 생각해야 하는 이유

새 Action / Intent 모델이 기존 시스템과 충돌하지 않으려면, 아래 두 단계를 분리해야 한다.

### 단계 A. Candidate Target Builder
역할:
- stable profile에서 관련 concept 후보를 가져온다
- chat text에서 브랜드/카테고리/성분/goal/concern/BEEAttr/keyword를 dictionary / linker로 찾는다
- 세션 컨텍스트에서 최근 본 family / cart / compare 대상을 가져온다
- browse/search/click/cart/purchase 로그 메타에서 직접 target을 가져온다

### 단계 B. Action / Intent Model
역할:
- 단계 A가 만든 후보들을 입력으로 받아
- 현재 액션/의도와 타깃 여부를 판정한다

즉 이 모델은 **“텍스트에서 엔티티를 처음부터 뽑는 모델”이 아니라, “후보 대상 중 어떤 것이 지금 중요한가를 고르는 모델”**이다.

## 6. 입력 소스별 역할

### 6.1 Chat (주 입력)

가장 잘 잡히는 것:

- intent stage
- 비교 의도
- 구매 의도
- 회피/제약
- 관심 대상

예:

입력:

> "쿠션 하나 사려고 하는데 헤라랑 클리오 중 뭐가 더 나아?"

라벨:

- `intent_stage = COMPARE`
- `action_type = ASK_COMPARISON`
- `targets = [Brand:헤라, Brand:클리오, Category:쿠션]`
- `strength ≈ 0.8`
- `horizon = NOW`

### 6.2 Browse / Search / Click / Cart 로그 (강한 행동 입력)

가장 잘 잡히는 것:

- interest
- consider
- compare
- add-to-cart
- purchase intent
- reorder intent

예:

- 같은 family PDP 3회 조회
- compare widget click
- cart 추가

→ `COMPARE`, `PURCHASE_INTENT` 강한 silver label 생성 가능

### 6.3 Purchase log (강한 outcome / loyalty 입력)

가장 잘 잡히는 것:

- purchase
- reorder
- family/brand loyalty
- category affinity

예:

- 동일 family 2회 재구매
n→ `REPURCHASE_INTENT`의 강한 supervision source

### 6.4 Review (주 입력 아님)

리뷰는 user-action 모델의 주 입력으로 쓰지 않는다.

리뷰의 역할은:

- `repurchase / advocate / avoid` 표현 사전 보강
- product-side outcome / market signal
- weak rule lexicon 구축

즉 리뷰는 **auxiliary source**다.

## 7. 학습 데이터셋 스키마

### 7.1 권장 JSONL 스키마

```json
{
  "example_id": "chat:u123:2025-04-08:0001",
  "user_id": "u123",
  "session_id": "s456",
  "source": "chat",
  "event_time": "2025-04-08T10:20:00Z",
  "input_text": "쿠션 하나 사려고 하는데 헤라랑 클리오 중 뭐가 더 나아?",
  "candidate_targets": [
    {"target_type": "Brand", "target_id": "brand_hera", "surface": "헤라"},
    {"target_type": "Brand", "target_id": "brand_clio", "surface": "클리오"},
    {"target_type": "Category", "target_id": "cat_cushion", "surface": "쿠션"}
  ],
  "context": {
    "recent_turns": [
      "요즘 건조해서 쿠션 바르면 뜨더라",
      "보습력 좋은 쿠션 찾고 있어"
    ],
    "stable_profile_snapshot": {
      "concern_ids": ["concern_dryness"],
      "goal_ids": ["goal_moisturizing"],
      "preferred_bee_attr_ids": ["bee_attr_moisturizing_power"],
      "preferred_keyword_ids": ["kw_gellike"]
    },
    "recent_behavior_snapshot": {
      "viewed_family_ids": ["family_hera_black", "family_clio_killcover"],
      "cart_product_ids": []
    }
  },
  "labels": {
    "intent_stage": "COMPARE",
    "action_types": ["ASK_COMPARISON"],
    "target_annotations": [
      {"target_id": "brand_hera", "is_targeted": true, "role": "compare_target"},
      {"target_id": "brand_clio", "is_targeted": true, "role": "compare_target"},
      {"target_id": "cat_cushion", "is_targeted": true, "role": "filter_target"}
    ],
    "strength": 0.84,
    "horizon": "NOW",
    "explicitness": "EXPLICIT",
    "polarity": "POS"
  },
  "label_source": "silver_rule_v1"
}
```

### 7.2 왜 candidate target을 따로 넣나

이게 핵심이다.

새 모델은 Product/Brand/Concern/BEEAttr를 **추출**하는 게 아니라,
이미 다른 경로로 얻은 target 후보들 중 **현재 액션의 타깃을 고르는 것**이 목적이기 때문이다.

## 8. 전처리 단계

### Step 1. source 표준화

chat, browse/search/click/cart, purchase를 공통 JSONL schema로 변환한다.

### Step 2. candidate target 생성

입력 source에 따라 candidate를 만든다.

- chat: stable profile + dictionary/entity linker + session context
- browse/log: 로그 메타 자체가 target 후보
- purchase: product/family/brand 자체가 target 후보
- review: auxiliary only

### Step 3. target canonicalization

candidate target의 `target_id`는 GraphRapping 내부에서 바로 쓸 수 있는 ID 또는 concept ref로 정규화한다.

예:
- `brand_hera`
- `family_hera_black`
- `cat_cushion`
- `goal_moisturizing`
- `bee_attr_moisturizing_power`
- `kw_gellike`

### Step 4. stable profile snapshot attach

입력 해석 보조를 위해 현재 stable profile snapshot을 같이 넣는다.

이유:
같은 문장도 stable profile에 따라 의미가 달라질 수 있기 때문이다.

### Step 5. weak label 생성

규칙과 로그로 silver label을 만든다.

## 9. weak label 기본 설계

### 9.1 chat rule examples

- "추천해줘", "뭐가 좋아?" → `DISCOVER + ASK_RECOMMENDATION`
- "A랑 B 뭐가 더 나아?" → `COMPARE + ASK_COMPARISON`
- "사려고", "살까 고민" → `PURCHASE_INTENT`
- "다시 살까", "재구매" → `REPURCHASE_INTENT`
- "싫어", "빼고", "안 맞아" → `AVOID`

### 9.2 log rule examples

- PDP view count >= 3 → `INTEREST/CONSIDER`
- compare widget click → `COMPARE`
- add to cart → `PURCHASE_INTENT`
- reorder purchase pattern → `REPURCHASE_INTENT`

### 9.3 review rule examples (auxiliary only)

- "재구매 의사 있음" → `REPURCHASE_INTENT` lexicon
- "추천함" → `ADVOCATE`
- "다시 안 삼" → `AVOID`

## 10. 모델 형태 제안

### 10.1 권장 baseline

**candidate-conditioned multi-head encoder model**

입력:
- `input_text`
- `candidate_target`
- `context summary`

출력:
- `intent_stage`
- `action_types`
- `is_targeted`
- `target_role`
- `strength`
- `horizon`
- `explicitness`
- `polarity`

### 10.2 pair classification이 좋은 이유

현재 시스템과 가장 잘 맞는다.

- 기존 추출/링킹 자산 재사용 가능
- stable profile과 overlay 역할 분리 가능
- target grounding을 action 판정 문제로 자연스럽게 분리 가능
- 디버깅이 쉽다

### 10.3 sLLM의 역할

sLLM은 online serving 주 모델이 아니라 **offline teacher / labeler**로 쓴다.

용도:
- hard case silver label 생성
- rule로 애매한 샘플 보강
- annotation guideline refinement

## 11. GraphRapping 반영 방식

### 11.1 Layer 1
추가 raw 입력

- `chat_turn_raw`
- `browse_event_raw`
- `cart_event_raw`
- `purchase_event_raw` (기존 활용 강화)

### 11.2 Layer 2
추가 canonical fact family

- `canonical_user_action_fact`

예:
- `HAS_INTENT_STAGE`
- `SHOWS_INTEREST_IN_*`
- `COMPARES_*`
- `HAS_PURCHASE_INTENT_FOR_*`

### 11.3 Layer 3
추가 aggregate

- `agg_user_action_signal`

### 11.4 serving_user_profile
추가 overlay slot

- `current_intent_stage`
- `current_interest_brand_ids`
- `current_interest_family_ids`
- `current_compare_targets`
- `current_purchase_intent_targets`
- `current_avoid_targets`

### 11.5 Layer 4
추천 사용 방식

- Stable profile = base prior
- Action overlay = current boost / penalty
- Purchase outcome = loyalty / novelty / reorder signal

## 12. 왜 리뷰를 주 입력에서 빼야 하는가

리뷰는 reviewer identity와 real user identity가 매칭되지 않는 경우가 많고, 현재 시스템도 reviewer proxy와 real user를 분리하는 원칙을 가진다. 따라서 리뷰에서 나오는 액션/의도는 **사용자별 실시간 상태**로 보기 어렵다.

대신 리뷰는 다음에 쓰는 게 맞다.

- product-side market signal
- repurchase / advocacy / avoidance expression lexicon
- weak label rule 보강

## 13. 구현 순서 권장

### Phase 1
- target class schema 확정
- JSONL schema 확정
- weak label rule 초안 작성
- 100~300개 샘플 수동 검토

### Phase 2
- candidate target generator 구현
- baseline multi-head classifier 구현
- target linker 연결
- confidence calibration

### Phase 3
- GraphRapping `canonical_user_action_fact` 반영
- `agg_user_action_signal` / `serving_user_profile` overlay slot 추가
- candidate/scorer/explainer 연결

### Phase 4
- sLLM teacher로 hard case 라벨 보강
- rule refinement
- 재학습

## 14. 최종 정리

이번 재구성에서 가장 중요한 포인트는 다음 세 가지다.

1. 새 모델은 기존 NER/BEE/REL 자산을 **대체하지 않는다**.
2. 새 모델은 기존 stable profile을 **대체하지 않는다**.
3. 새 모델은 **현재 액션/의도를 기존 candidate target에 grounding하는 overlay**다.

즉 최종 구조는:

- Stable Profile Layer (유지)
- Action / Intent Overlay Layer (신규)
- Review Outcome / Market Signal Layer (보조)

의 3축으로 이해하는 것이 맞다.
