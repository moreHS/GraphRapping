# Layer 매핑 부록 / 상세 매핑표 (최종 통합본)

이 문서는 실제 보유 데이터가 Layer 1, Layer 2, Layer 3에서 어떻게 매핑되는지를 **정확히** 정리한 부록이다.

---

## 1. 레이어 정의

### Layer 0. Product Master / User Master
정본 마스터 데이터.
- 상품 DB
- 회원 DB
- 구매 이벤트 원장
- 채팅 분석 결과 원장

### Layer 1. Raw / Evidence Layer
추출 모델의 출력과 원문 evidence를 그대로 저장하는 레이어.

### Layer 2. Canonical Fact Layer
정규화된 entity IRI와 **현재 canonical relation 65개**를 그대로 보존하는 레이어.

### Layer 3. Serving / Aggregate Layer
추천/개인화/설명에 바로 쓰기 위한 집계/압축 projection 레이어.

---

## 2. 상품/리뷰 엔티티 매핑표

| 원천 항목 | Layer 1 | Layer 2 | Layer 3 | 비고 |
|---|---|---|---|---|
| 상품 DB product_id | `review_catalog_link.target_product_id` | `[NODE:Product]` | `[NODE:Product]` | 모든 리뷰 signal의 최종 anchor |
| 상품명(prod_nm) | `review_raw.prod_nm_raw` | `product_alias_map` | 사용 안 함 | catalog matching 재료 |
| 브랜드(brnd_nm) | `review_raw.brand_raw` | `[NODE:Brand]`, `HAS_BRAND/BELONGS_TO_BRAND` | `[NODE:Brand]` | 정본은 상품 DB |
| 카테고리 | 상품 DB에서 옴 | `[NODE:Category]` | `[NODE:Category]` | 리뷰 추출 CAT는 enrichment/validation |
| 성분 | 상품 DB에서 옴 + 리뷰 ING mention | `[NODE:Ingredient]` | `[NODE:Ingredient]` | 정본은 상품 DB |
| 제조국 | 상품 DB에서 옴 | Product property or Country node | Product property | 리뷰에서 직접 추출 안 해도 됨 |
| 가격 | 상품 DB / 구매이벤트 | Product property or PriceBand node | `PriceBand` optional | 리뷰 relation `price_of`는 validation signal |
| 리뷰 원문 | `review_raw.text` | 보통 row 유지 | evidence retrieval only | 메인 graph 노드 아님 |
| review_id | `review_raw.review_id` | evidence join key | optional evidence ref | raw에서 생성 필요 |
| reviewer_proxy_id | `review_raw.reviewer_proxy_id` | `[NODE:ReviewerProxy]` optional | 보통 graph 적재 안 함 | 실회원과 분리 |
| opinion/bee mention | `bee_raw` row | optional audit node / row join | graph 적재 안 함 | phrase는 row로 보관 |
| NER mention | `ner_raw` row | canonical entity resolve 재료 | graph 적재 안 함 | mention 자체는 row |
| REL row | `rel_raw` row | canonical fact row | projection 입력 | raw trace 보관 |

---

## 3. NER 타입별 매핑표

| NER 타입 | Layer 1 | Layer 2 정규화 | Layer 3 활용 |
|---|---|---|---|
| PRD | raw mention | target product / other product / co-used product | Product comparison / co-use signal |
| PER | raw mention | reviewer_proxy / person mention / segment hint | reviewer raw only, segment만 serving 반영 |
| CAT | raw mention | Category resolve | user/product category join |
| BRD | raw mention | Brand resolve | brand preference / brand affinity |
| DATE | raw mention | TemporalContext / Frequency / Duration 분해 | context/frequency/duration signal |
| COL | raw mention | Color / Shade concept | color preference, variant facet |
| AGE | raw mention | AgeBand / UserSegment hint | targeting/segment signal |
| VOL | raw mention | Volume / Size concept | variant facet / price-volume preference |
| EVN | raw mention | Event / Campaign / Season / Occasion 후보 | campaign or context signal |
| ING | raw mention | Ingredient resolve | ingredient preference/avoidance |

---

## 4. BEE의 정확한 매핑 구조

BEE는 절대 KEYWORD 하나로 흡수하면 안 된다.

### 4-1. 개념 구조

```text
BEE phrase(raw text)
  -> BEE_ATTR (속성 축)
  -> KEYWORD (정규화 표현)
```

### 4-2. 예시

원문 phrase:
`착붙하고 오후에도 안 떠요`

정규화:
- `BEE_ATTR = Adhesion(밀착력)`
- `KEYWORD = 밀착좋음`
- `KEYWORD = 들뜸없음`
- `TemporalContext = 오후`

### 4-3. 레이어별 저장

| 항목 | Layer 1 | Layer 2 | Layer 3 |
|---|---|---|---|
| BEE phrase | `bee_raw.phrase_text` | evidence ref | graph 적재 안 함 |
| BEE_ATTR | `bee_raw.aspect_raw` | `[NODE:BEEAttr]` + `HAS_ATTRIBUTE` fact | `[EDGE:HAS_BEE_ATTR_SIGNAL]` |
| KEYWORD | `keyword_extraction_raw` / dict match 결과 | `[NODE:Keyword]` + `HAS_KEYWORD` fact | `[EDGE:HAS_BEE_KEYWORD_SIGNAL]` |
| sentiment | `bee_raw.sentiment_raw` | fact polarity | aggregate pos/neg counts |

### 4-4. Layer 2 예시

```text
[NODE:Product {product_id:"p_834921"}]
  -[EDGE:HAS_ATTRIBUTE {review_id:"rv_1", polarity:"POS"}]->
[NODE:BEEAttr {attr_id:"bee_attr_adhesion", label:"밀착력"}]

[NODE:BEEAttr {attr_id:"bee_attr_adhesion"}]
  -[EDGE:HAS_KEYWORD {review_id:"rv_1"}]->
[NODE:Keyword {keyword_id:"kw_adhesion_good", label:"밀착좋음"}]
```

### 4-5. Layer 3 예시

```text
[NODE:Product]
  -[EDGE:HAS_BEE_ATTR_SIGNAL {
      review_cnt: 128,
      pos_cnt: 119,
      neg_cnt: 9,
      score: 0.89
   }]->
[NODE:BEEAttr]

[NODE:Product]
  -[EDGE:HAS_BEE_KEYWORD_SIGNAL {
      review_cnt: 97,
      pos_cnt: 92,
      neg_cnt: 5,
      score: 0.91
   }]->
[NODE:Keyword]
```

---

## 5. REL family별 정확한 매핑 원칙

### Layer 1
`rel_raw` row로 그대로 저장.

### Layer 2
현재 canonical relation 65개를 **그대로 predicate**로 사용.

### Layer 3
추천에 필요한 것만 projection/aggregation.

---

## 6. REL 그룹별 상세 매핑

## 6-1. Usage / Context

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `USED_BY` | 보통 raw 유지 / 필요 시 reviewer segment signal | reviewer proxy는 실유저 아님 |
| `USES` | raw 유지 / optional co-use profile | reviewer proxy 중심 |
| `APPLIED_TO` | `APPLIED_TO_SIGNAL` or context/use-target facet | 부위/표면/객체 정규화 필요 |
| `USED_FOR` | `USED_FOR_GOAL_SIGNAL` / `ADDRESSES_CONCERN_SIGNAL` | goal/concern 분기 |
| `USED_ON` | `USED_IN_CONTEXT_SIGNAL` | 날짜가 아니라 사용 맥락 |
| `USED_WITH` | `USED_WITH_TOOL_SIGNAL` or `USED_WITH_PRODUCT_SIGNAL` | Tool/Product 분기 |
| `APPLIED_BY` | raw 유지 | reviewer proxy 중심 |
| `TIME_OF_USE` | `USED_IN_CONTEXT_SIGNAL` | TemporalContext로 정규화 |
| `DURATION_OF_USE` | duration aggregate/property | 노드보다 stat 성격 강함 |
| `FREQUENCY_OF_USE` | frequency aggregate/property | 노드보다 stat 성격 강함 |
| `NOT_USED_BY` | negative usage signal optional | 기본은 raw 유지 |
| `EXPERIENCED_BY` | raw 유지 | reviewer proxy 중심 |
| `EXPERIENCES` | context/effect signal optional | 해석 난이도 높음 |

## 6-2. Effect / Causation

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `AFFECTS` | positive or negative concern/effect signal | polarity 해석 필요 |
| `AFFECTED_BY` | reverse 후 `AFFECTS` 해석 | 방향 뒤집기 |
| `BENEFITS` | `ADDRESSES_CONCERN_SIGNAL` or `BENEFITS_SEGMENT_SIGNAL` | object 타입에 따라 분기 |
| `BENEFITS_USER` | `BENEFITS_SEGMENT_SIGNAL` | segment/general user 혜택 |
| `CAUSES` | `MAY_CAUSE_CONCERN_SIGNAL` | 부정 concern 유발 |
| `CAUSED_BY` | reverse 후 `MAY_CAUSE_CONCERN_SIGNAL` | 방향 뒤집기 |
| `TREATS` | `ADDRESSES_CONCERN_SIGNAL` | 문제 완화/개선 |
| `ADDRESSED_BY_TREATMENT` | raw 유지 또는 method graph | 제품 추천엔 우선순위 낮음 |
| `ADDRESSES` | `ADDRESSES_CONCERN_SIGNAL` | concern/need 해결 |
| `REQUIRES` / `REQUIRED_BY` | requirement/fit signal optional | usage 패턴 설명에 활용 |

## 6-3. Attribute / Composition

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `HAS_ATTRIBUTE` | `HAS_BEE_ATTR_SIGNAL` | BEE_ATTR 유지 |
| `ATTRIBUTE_OF` | reverse 해석, 보통 Layer 3 직접 사용 안 함 | inverse |
| `HAS_INGREDIENT` | catalog validation / optional direct edge | 정본은 상품 DB |
| `INGREDIENT_OF` | reverse 해석 | inverse |
| `HAS_PART` | optional structural graph | 필요 시 |
| `PART_OF` | optional structural graph | 필요 시 |
| `HAS_INSTANCE` | optional type hierarchy | 필요 시 |
| `INSTANCE_OF` | optional type hierarchy | 필요 시 |
| `VARIANT_OF` | catalog/master graph | catalog가 정본 |
| `BELONGS_TO` | category/collection membership | catalog 정본 우선 |
| `PRICE_OF` | product property / price band | 리뷰에선 validation |
| `AVAILABLE_IN` | pack/format/channel facet | 필요 시 |
| `PRODUCES` / `PRODUCED_BY` | brand/manufacturer validation | catalog 정본 우선 |
| `BRAND_OF` | `HAS_BRAND` or `BELONGS_TO_BRAND` | catalog 정본 우선 |

## 6-4. Description / Perception

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `DESCRIBES` | alias/keyword normalization 힌트 | dictionary build에도 사용 |
| `DESCRIBED_BY` | inverse / alias 힌트 | 보통 raw 유지 |
| `PERCEIVES` | user-side preference signal 힌트 | reviewer proxy 기반이면 raw |
| `PERCEIVED_BY` | inverse / sentiment provenance | raw 유지 |
| `RELATED_TO` | `RELATED_PRODUCT_SIGNAL` or generic association | 의미가 넓어 해석 주의 |
| `INFORMATION_FROM` / `INFORMATION_TO` | provenance | 추천 직접 활용 낮음 |

## 6-5. Comparison / Recommendation / Targeting

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `COMPARISON_WITH` | `COMPARED_WITH_SIGNAL` | product-product 비교 |
| `RECOMMENDED_BY` | provenance / authority signal | reviewer proxy 기반이면 raw |
| `RECOMMENDED_TO` | `RECOMMENDED_TO_SEGMENT_SIGNAL` | PER를 segment로 승격해야 의미 있음 |
| `TARGETED_AT` | `TARGETED_AT_SEGMENT_SIGNAL` | 연령/피부타입/용도 세그먼트 |
| `TARGETED_BY` | reverse 후 `TARGETED_AT_SEGMENT_SIGNAL` | 방향 뒤집기 |
| `ADDRESSED_TO` | message target signal optional | 캠페인/카피 용도 |
| `AVAILABLE_TO` | segment/channel availability signal | 경우에 따라 segment node |

## 6-6. Commerce / Ownership / Family / Identity

| Layer 2 canonical | Layer 3 projection | 설명 |
|---|---|---|
| `PURCHASES` / `PURCHASED_BY` | commerce behavior / popularity feature | reviewer raw vs real user 구분 필수 |
| `SELLS` / `SOLD_BY` | seller/channel graph | 필요 시 |
| `PROVIDED_TO` / `PROVIDED_BY` | sampling/provision signal | 프로모션 분석용 |
| `GIFTED_BY` / `GIFTED_TO` | gifting signal | 시즌성 이벤트 유용 |
| `OWNS` / `OWNED_BY` | reviewer proxy raw, real user purchase graph에서는 사용 가능 | 구분 필수 |
| `SAME_ENTITY` | Layer 1/2 전처리에서 merge rule | graph edge로 저장 안 함 |
| `NO_RELATIONSHIP` | ignore | 저장 안 함 |
| `CHILD_OF` / `PARENT_OF` / `FAMILY_MEMBER_OF` | optional household segment | 리뷰 proxy graph에선 우선순위 낮음 |

---

## 7. 유저 데이터 매핑표

| 유저 원천 데이터 | Layer 1 | Layer 2 | Layer 3 |
|---|---|---|---|
| 회원 기본정보(나이/성별) | `user_profile_raw` | `[NODE:User]`, age_band/gender facts | user property + segment |
| 피부타입/피부톤 | `user_profile_raw` | `HAS_SKIN_TYPE`, `HAS_SKIN_TONE` | direct serving edge |
| 구매내역 | `purchase_event_raw` | `[NODE:PurchaseEvent]` or fact rows | summary preference edges |
| 선호브랜드(구매 기반) | `user_summary_raw` | `PREFERS_BRAND` fact | serving edge |
| 선호카테고리 | `user_summary_raw` | `PREFERS_CATEGORY` fact | serving edge |
| 재구매내역 | `repurchase_summary_raw` | `REPURCHASES_PRODUCT/FAMILY` fact | repeat affinity edge |
| 계절별 선호 브랜드/카테고리 | `seasonal_pref_raw` | `SEASONAL_PREFERS_*` facts | serving edge |
| 채팅 기반 선호성분 | `chat_profile_raw` | `PREFERS_INGREDIENT` | serving edge |
| 채팅 기반 기피성분/알러지 | `chat_profile_raw` | `AVOIDS_INGREDIENT` | serving edge |
| 카테고리별 고민 | `chat_profile_raw` | `HAS_CONCERN` | serving edge |
| 카테고리별 목표 | `chat_profile_raw` | `WANTS_GOAL` / `WANTS_EFFECT` | serving edge |
| 선호향 | `chat_profile_raw` | `PREFERS_KEYWORD` or fragrance concept | serving edge |
| 주사용 방법/루틴/시간 | `chat_profile_raw` | `PREFERS_CONTEXT` | serving edge |
| BEE_ATTR 선호/회피(유도 생성) | derived | `PREFERS_BEE_ATTR` / `AVOIDS_BEE_ATTR` | serving edge |
| KEYWORD 선호/회피(유도 생성) | derived | `PREFERS_KEYWORD` / `AVOIDS_KEYWORD` | serving edge |

---

## 8. Product/User join 경로 예시

### 예시 A. 발림성/밀착력 선호 사용자

```text
[NODE:User {user_id:"u_1001"}]
  -[EDGE:PREFERS_BEE_ATTR]->
[NODE:BEEAttr {label:"발림성"}]

[NODE:User {user_id:"u_1001"}]
  -[EDGE:PREFERS_KEYWORD]->
[NODE:Keyword {label:"얇게발림"}]

[NODE:Product {product_id:"p_834921"}]
  -[EDGE:HAS_BEE_ATTR_SIGNAL]->
[NODE:BEEAttr {label:"발림성"}]

[NODE:Product {product_id:"p_834921"}]
  -[EDGE:HAS_BEE_KEYWORD_SIGNAL]->
[NODE:Keyword {label:"얇게발림"}]
```

### 예시 B. 건조함 concern 사용자

```text
[NODE:User]-[EDGE:HAS_CONCERN]->[NODE:Concern {label:"건조함"}]
[NODE:Product]-[EDGE:ADDRESSES_CONCERN_SIGNAL]->[NODE:Concern {label:"건조함"}]
```

### 예시 C. 세안후/아침 루틴 선호 사용자

```text
[NODE:User]-[EDGE:PREFERS_CONTEXT]->[NODE:TemporalContext {label:"세안후"}]
[NODE:User]-[EDGE:PREFERS_CONTEXT]->[NODE:TemporalContext {label:"아침"}]
[NODE:Product]-[EDGE:USED_IN_CONTEXT_SIGNAL]->[NODE:TemporalContext {label:"세안후"}]
[NODE:Product]-[EDGE:USED_IN_CONTEXT_SIGNAL]->[NODE:TemporalContext {label:"아침"}]
```

---

## 9. End-to-end 예시

### 리뷰 원문
`아침에 세안 후 퍼프로 바르면 착붙하고 오후에도 안 떠요. 건조한 날에도 괜찮고 클리오 킬커버보다 얇게 발려요.`

### Layer 1
- `review_raw`
- `bee_raw`: Adhesion, Spreadability, Moisturizing Power
- `ner_raw`: PRD(target), PRD(other product), DATE(아침, 세안 후, 오후), PER(optional), Tool candidate(퍼프)
- `rel_raw`: used_on, used_with, comparison_with, addresses, has_attribute 등

### Layer 2
- Product `p_834921`
- BEE_ATTR `밀착력`, `발림성`, `보습력`
- KEYWORD `밀착좋음`, `들뜸없음`, `얇게발림`, `건조한날무난`
- Facts:
  - `HAS_ATTRIBUTE`
  - `HAS_KEYWORD`
  - `USED_ON`
  - `USED_WITH`
  - `COMPARISON_WITH`
  - `ADDRESSES`

### Layer 3
- `HAS_BEE_ATTR_SIGNAL(밀착력)`
- `HAS_BEE_KEYWORD_SIGNAL(밀착좋음)`
- `HAS_BEE_KEYWORD_SIGNAL(들뜸없음)`
- `USED_IN_CONTEXT_SIGNAL(아침)`
- `USED_IN_CONTEXT_SIGNAL(세안후)`
- `USED_WITH_TOOL_SIGNAL(퍼프)`
- `COMPARED_WITH_SIGNAL(클리오 킬커버)`
- `ADDRESSES_CONCERN_SIGNAL(건조함)`

