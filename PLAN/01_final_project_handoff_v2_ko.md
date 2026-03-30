# Relation 프로젝트 최종 통합 Handoff / Claude Code 전달본 (상세)

## 0. 문서 목적

이 문서는 다음 두 소스를 함께 반영해, 현재 프로젝트의 **최종 구현 방향**을 다시 고정한 handoff 문서다.

1. `data_schema_for_gpt_pro.md`
2. `session_visible_transcript.md`

이전 문서에서 어긋났던 부분, 특히 아래 4가지를 이번 문서에서 확정적으로 해소한다.

- Layer 2 canonical과 Layer 3 serving edge를 분리하지 못했던 문제
- BEE_ATTR와 KEYWORD를 한 층으로 뭉뚱그렸던 문제
- Review / Opinion / Phrase를 메인 그래프 노드처럼 과도하게 다뤘던 문제
- Neo4j-first / Postgres-first / Zep/Graphiti 활용 범위가 시점에 따라 흔들렸던 문제

이 문서는 Claude Code가 바로 구현 작업으로 쪼갤 수 있도록:
- 용어 정의
- 충돌 정리
- 레이어 정의
- 테이블 / 노드 / 엣지 설계
- 후처리 로직
- 추천 플로우
- 인프라 선택
- 구현 우선순위
를 모두 포함한다.

---

## 1. 이번 통합본에서 확정된 사실

### 1-1. 실제 raw 구조 관련
업로드한 스키마 문서 기준으로 현재 raw에는 NER 10개 타입, BEE 39개 속성 타입, canonical relation 65개(+ 특수/자동 생성 관계)가 존재한다. 또 raw 레코드에는 별도 `review_id`, `reviewer_proxy_id`, `target_product_id`, relation confidence가 없고, `Review Target`, `Reviewer` placeholder가 존재한다. Neo4j 중간 산출 기준으로는 29,063개 엔티티, 294,549개 엣지가 적재돼 있다. 이건 “이미 그래프처럼 저장된 중간 산출물”은 있지만, 서비스용 설계가 아직 분리되지 않았다는 뜻이다. 참고 소스: `data_schema_for_gpt_pro.md`.

### 1-2. transcript에서 확정된 설계 원칙
세션 기록에서 가장 중요한 합의는 다음 셋이다.

1. **Layer 2에서는 relation 65개를 그대로 보존한다.**
2. **Layer 3에서만 추천용 serving projection으로 압축한다.**
3. **BEE_ATTR는 KEYWORD에 흡수되면 안 된다.**

즉, 이전에 내가 너무 빨리 canonical edge를 압축해서 설명했던 부분은 철회하고, 이번 최종안에서는 이 합의를 설계의 중심 원칙으로 둔다.

### 1-3. 상품 DB 존재에 따른 해석
초기 스키마 문서만 보면 raw 레코드에 별도 `product_id`가 없지만, 현재 프로젝트 전제는 **상품 DB를 연결할 수 있고, 그 DB에서 브랜드/카테고리/가격/제조국/메인효능/성분 같은 정본 데이터를 가져올 수 있다**는 것이다. 따라서 최종 구현에서는 `target_product_id`를 후처리에서 반드시 resolve해야 하며, 리뷰 추출 relation은 catalog truth를 대체하지 않는다.

### 1-4. 조직 맥락
세션 기록에서 조직이 PostgreSQL을 이미 쓰고 있다는 점이 확인되었고, 이 때문에 product graph 저장소 우선순위가 달라졌다. 초기엔 Neo4j-first 얘기가 있었지만, 최종적으로는 **Postgres-first hybrid**가 MVP 1순위로 올라왔고, graph projection은 AGE 또는 Neo4j를 선택적으로 두는 방향이 현실적이다.

---

## 2. 충돌 정리와 최종 해소 방침

## 2-1. `Review`를 메인 그래프 노드로 볼 것인가?
### 이전 설명
Review / OpinionMention을 graph 중심 노드처럼 예시로 둔 적이 있었다.

### 최종 결정
**아니다.** Review / Opinion / Phrase는 기본적으로 **Layer 1 evidence row**다.
- raw trace 보관
- 설명 근거 회수
- dictionary 보강
- QA / audit
용도로 남긴다.

Serving graph에 올리는 것은 집계 결과뿐이다.

---

## 2-2. `OpinionMention`은 노드인가, 엣지인가?
### 개념적으로
리뷰 안의 평가 구문 1건을 가리키는 evidence 객체다.

### 구현적으로
MVP에서는 **row**로 두는 것이 맞다. Neo4j/AGE 메인 그래프에 전부 적재하지 않는다.

---

## 2-3. BEE를 KEYWORD만으로 다룰 수 있는가?
### 이전 오해
KEYWORD만 두고 전부 `HAS_KEYWORD`로 연결하는 설계처럼 들릴 수 있었다.

### 최종 결정
그렇게 하면 안 된다.

정확한 구조는:

```text
BEE phrase(raw)
  -> BEE_ATTR(속성 축)
  -> KEYWORD(정규 표현)
```

즉,
- `발림성`, `밀착력`, `보습력`, `향`, `사용감` 같은 축은 독립적으로 유지
- `얇게발림`, `잘밀착됨`, `무향`, `건조감적음` 같은 정규 키워드도 유지
- serving layer에서는 `Product -> BEE_ATTR`, `Product -> KEYWORD` 둘 다 살아야 한다.

---

## 2-4. canonical relation은 몇 개인가?
### data schema 기준
business canonical relation은 65개이고, `same_entity`, `no_relationship` 같은 전처리용 관계 및 `OFFICIAL_BRAND`, `HAS_KEYWORD` 같은 자동 생성 관계가 별도로 존재한다.

### 최종 결정
- **Layer 2 canonical**: business relation 65개를 그대로 predicate로 보존
- `same_entity`는 저장 relation이 아니라 merge rule
- `no_relationship`는 drop
- `OFFICIAL_BRAND`, `HAS_KEYWORD`는 generated helper relation으로 관리

---

## 2-5. 제품 그래프는 Neo4j-first인가, Postgres-first인가?
### 세션 내 충돌
- 초반엔 product graph에 Neo4j가 1순위처럼 논의됨
- 이후 조직이 Postgres를 이미 쓴다는 조건이 붙으면서 Postgres-first hybrid가 1순위로 올라옴

### 최종 결정
**System of Record는 Postgres-first**로 간다.

정확히는:
- Postgres = raw / normalized / aggregate / user summary / purchase event
- AGE = Postgres 안에서 graph query를 해보고 싶을 때의 1차 선택지
- Neo4j CE = graph-heavy exploration, explainable path, analyst tooling이 중요해질 때의 serving projection

즉 **Postgres가 필수, AGE/Neo4j는 projection**이다.

---

## 2-6. Zep/Graphiti는 이번 구현의 필수 요소인가?
### transcript에서의 맥락
Zep/Graphiti는 live user temporal memory와 context graph에 적합하다는 논의가 있었다.

### 최종 결정
이번 프로젝트의 MVP에는 **필수 아님**.

왜냐하면 현재 user 데이터는 대부분:
- 가입 정보
- 구매 기반 요약
- 채팅 분석 결과
처럼 이미 structured summary에 가깝기 때문이다.

따라서 MVP는 Postgres/AGE(또는 Neo4j projection)만으로 충분하고,
**Graphiti/Zep는 나중에 “실시간 대화 메모리 / invalidation / long-term memory”가 진짜 필요해질 때** 붙인다.

---

## 3. 최종 아키텍처

```text
Layer 0. Product/User Master (truth)
  - product DB
  - user master
  - purchase events
  - chat/profile summaries

Layer 1. Raw / Evidence Layer
  - review_raw
  - ner_raw
  - bee_raw
  - rel_raw
  - raw dictionary build candidates

Layer 2. Canonical Fact Layer
  - normalized entities (IRI)
  - canonical 65 relations
  - generated helper facts
  - user canonical facts

Layer 3. Serving / Aggregate Layer
  - Product -> BEE_ATTR signal
  - Product -> KEYWORD signal
  - Product -> Context signal
  - Product -> Concern signal
  - Product -> Comparison signal
  - User -> Preference / Avoid / Concern / Goal edges

Layer 4. Recommendation / Explanation / Simulation-ready Interface
  - candidate generation
  - reranking
  - explanation path
  - hook generation
  - next-best-question
  - optional future simulator input mart
```

---

## 4. Layer별 상세 설계

## 4-1. Layer 0. Master

### Product Master (truth)
반드시 확보할 컬럼:
- `product_id`
- `product_name`
- `brand_id`, `brand_name`
- `category_id`, `category_name`
- `country_of_origin`
- `main_benefits`
- `price`
- `ingredients`
- optional: `volume`, `shade`, `variant family`

### User Master / Summary
반드시 확보할 컬럼:
- `user_id`
- `age`, `age_band`, `gender`
- `skin_type`, `skin_tone`
- purchase-based preference summary
- repurchase summary
- seasonal preference summary
- chat-based preference/avoidance/concern/goal summary

---

## 4-2. Layer 1. Raw / Evidence Layer

### 저장해야 하는 raw 테이블
1. `review_raw`
2. `ner_raw`
3. `bee_raw`
4. `rel_raw`
5. `review_catalog_link`
6. `dictionary_candidate_queue`

### 왜 raw를 따로 보관해야 하나
- dictionary update 시 재실행 가능
- QA / audit 가능
- 추천 explanation의 문장 근거 회수 가능
- model version 교체 시 재처리 가능

### 절대 하면 안 되는 것
- raw phrase를 바로 serving edge로 연결
- raw placeholder를 그대로 graph node로 사용

---

## 4-3. Layer 2. Canonical Fact Layer

이 레이어의 목적은 **실제 보유 relation 65개를 손실 없이 보존하면서**, entity만 canonical IRI로 정규화하는 것이다.

### Layer 2 핵심 특징
- predicate는 65개 business canonical relation을 그대로 사용
- subject/object는 canonical IRI로 resolve
- provenance는 raw table key로 연결
- helper relation(`HAS_KEYWORD`, `OFFICIAL_BRAND`)은 별도 관리

### Layer 2에서 만드는 canonical entity types
- `Product`
- `Brand`
- `Category`
- `Ingredient`
- `BEEAttr`
- `Keyword`
- `TemporalContext`
- `Frequency`
- `Duration`
- `Concern`
- `Goal`
- `Tool`
- `User`
- `SkinType`
- `SkinTone`
- `Fragrance`
- optional: `Seller`, `Event`, `AgeBand`, `Color`, `Volume`

### DATE 정규화 규칙
`DATE`는 실제로 다음 셋 중 하나다.
- `TemporalContext`: 아침, 밤, 세안 후, 여름, 출근 전
- `Frequency`: 매일, 하루 1번, 주 2회
- `Duration`: 2주째, 한 달 동안

---

## 4-4. Layer 3. Serving / Aggregate Layer

이 레이어의 목적은 추천/개인화/탐색에 직접 쓰기 위한 graph projection을 만드는 것이다.

### Product-side serving edges
- `HAS_BEE_ATTR_SIGNAL`
- `HAS_BEE_KEYWORD_SIGNAL`
- `USED_IN_CONTEXT_SIGNAL`
- `USED_WITH_TOOL_SIGNAL`
- `USED_WITH_PRODUCT_SIGNAL`
- `ADDRESSES_CONCERN_SIGNAL`
- `MAY_CAUSE_CONCERN_SIGNAL`
- `COMPARED_WITH_SIGNAL`
- `TARGETED_AT_SEGMENT_SIGNAL`
- `RECOMMENDED_TO_SEGMENT_SIGNAL`
- optional: `HAS_PURCHASE_SIGNAL`, `HAS_SAMPLE_SIGNAL`, `HAS_GIFT_SIGNAL`

### User-side serving edges
- `HAS_SKIN_TYPE`
- `HAS_SKIN_TONE`
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `PREFERS_INGREDIENT`
- `AVOIDS_INGREDIENT`
- `HAS_CONCERN`
- `WANTS_GOAL`
- `WANTS_EFFECT`
- `PREFERS_CONTEXT`
- `PREFERS_BEE_ATTR`
- `AVOIDS_BEE_ATTR`
- `PREFERS_KEYWORD`
- `AVOIDS_KEYWORD`
- `SEASONAL_PREFERS_BRAND`
- `SEASONAL_PREFERS_CATEGORY`
- `REPURCHASES_PRODUCT_OR_FAMILY`

### 왜 Layer 3를 따로 만드는가
- raw relation 65개 전체를 온라인 추천에서 직접 쓰면 noisy하고 복잡하다.
- 반대로 너무 빨리 압축하면 의미 손실이 크다.
- 그래서 Layer 2를 보존하고, Layer 3만 목적별로 projection한다.

---

## 5. Raw -> Wrapped Signal -> Serving Graph

## 5-1. Wrapped Signal 표준 구조

```json
{
  "signal_id": "sig_xxx",
  "review_id": "rv_xxx",
  "target_product_id": "prd_000123",
  "source_modality": "NER|BEE|REL|FUSED",
  "signal_family": "BEE_ATTR|BEE_KEYWORD|CONTEXT|TOOL|CONCERN_POS|CONCERN_NEG|COMPARISON|SEGMENT|CATALOG_VALIDATION",
  "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL|HAS_BEE_KEYWORD_SIGNAL|USED_IN_CONTEXT_SIGNAL|USED_WITH_TOOL_SIGNAL|ADDRESSES_CONCERN_SIGNAL|MAY_CAUSE_CONCERN_SIGNAL|COMPARED_WITH_SIGNAL|RECOMMENDED_TO_SEGMENT_SIGNAL",
  "dst_node_type": "BEEAttr|Keyword|TemporalContext|Tool|Concern|Product|UserSegment|Brand|Ingredient",
  "dst_node_id": "...",
  "polarity": "POS|NEG|NEU|null",
  "weight": 1.0,
  "evidence": {
    "bee_id": 12,
    "rel_id": 91,
    "text": "착붙하고 안 떠요",
    "source_type": "NER-BeE"
  }
}
```

### 왜 이 구조가 필요한가
지금 raw triple은 relation 자체는 풍부하지만, 서비스 로직에서 바로 쓰기엔 품질/일관성이 떨어진다. Wrapped Signal은 raw를 버리지 않으면서도 추천 feature의 표준 단위가 된다.

---

## 6. 실제 예시 1: schema 문서의 Laneige 샘플 리뷰

### raw 예시(요약)
- brand: LANEIGE
- product: Lip Sleeping Mask Intense Hydration with Vitamin C
- bee:
  - `Scent / Love the smell / 긍정`
  - `Effect / Great on my lips / 긍정`
  - `Portability / Easley portable / 긍정`
  - `Expressiveness / if it was edible I would eat it / 긍정`
- ner:
  - `I(PER)`, `my(PER)`, `anyone(PER)`, `it(PRD)`, `Reviewer(PER)`, `Review Target(PRD)`
- rel:
  - `same_entity(I, my)`
  - `uses(I, it)`
  - `has_attribute(Review Target, Love the smell)`

### Layer 1 처리
- `review_id` 생성
- `reviewer_proxy_id` 생성
- `Review Target` -> target_product_id resolve
- `Reviewer`, `I`, `my` -> reviewer_proxy resolve

### Layer 2 처리
- Product `prd_laneige_sleeping_mask`
- BEEAttr `Scent`, `Effect`, `Portability`, `Expressiveness`
- Keyword `향좋음`, `입술에좋음`, `휴대편함` 등으로 정규화
- Facts:
  - `USES(reviewer_proxy, product)`
  - `HAS_ATTRIBUTE(product, BEEAttr:Scent)`
  - `HAS_KEYWORD(BEEAttr:Scent, Keyword:향좋음)`

### Layer 3 처리
- `Product -[:HAS_BEE_ATTR_SIGNAL]-> BEEAttr(Scent)`
- `Product -[:HAS_BEE_KEYWORD_SIGNAL]-> Keyword(향좋음)`
- `Product -[:HAS_BEE_KEYWORD_SIGNAL]-> Keyword(입술에좋음)`
- `Product -[:HAS_BEE_KEYWORD_SIGNAL]-> Keyword(휴대편함)`

---

## 7. 실제 예시 2: 한국어 뷰티 리뷰 + 유저 추천 연결

### 리뷰
`아침에 세안 후 퍼프로 바르면 착붙하고 오후에도 안 떠요. 건조한 날에도 괜찮고 클리오 킬커버보다 얇게 발려요.`

### Layer 1
- BEE phrase: `착붙하고 오후에도 안 떠요`, `건조한 날에도 괜찮고`, `얇게 발려요`
- NER: `아침`, `세안 후`, `오후`, `클리오 킬커버`
- REL: `used_on`, `used_with`, `comparison_with`, `addresses`, `has_attribute`

### Layer 2
- BEE_ATTR: `밀착력`, `보습력`, `발림성`
- KEYWORD: `밀착좋음`, `들뜸없음`, `건조한날무난`, `얇게발림`
- Context: `아침`, `세안후`, `오후`
- Tool: `퍼프`
- Product: `클리오 킬커버`
- Facts:
  - `HAS_ATTRIBUTE(target, 밀착력)`
  - `HAS_KEYWORD(밀착력, 밀착좋음)`
  - `HAS_KEYWORD(밀착력, 들뜸없음)`
  - `USED_ON(target, 아침)`
  - `USED_ON(target, 세안후)`
  - `USED_WITH(target, 퍼프)`
  - `COMPARISON_WITH(target, 클리오 킬커버)`
  - `ADDRESSES(target, 건조함)`

### Layer 3
- `HAS_BEE_ATTR_SIGNAL(밀착력)`
- `HAS_BEE_KEYWORD_SIGNAL(밀착좋음)`
- `HAS_BEE_KEYWORD_SIGNAL(들뜸없음)`
- `HAS_BEE_KEYWORD_SIGNAL(얇게발림)`
- `USED_IN_CONTEXT_SIGNAL(아침)`
- `USED_IN_CONTEXT_SIGNAL(세안후)`
- `USED_WITH_TOOL_SIGNAL(퍼프)`
- `COMPARED_WITH_SIGNAL(클리오 킬커버)`
- `ADDRESSES_CONCERN_SIGNAL(건조함)`

### 유저 예시
유저 `u_1001`
- `HAS_SKIN_TYPE(건성)`
- `HAS_CONCERN(건조함)`
- `PREFERS_CATEGORY(쿠션)`
- `PREFERS_BEE_ATTR(발림성)`
- `PREFERS_KEYWORD(얇게발림)`
- `PREFERS_CONTEXT(아침)`
- `PREFERS_CONTEXT(세안후)`

### join path
```text
User(u_1001)
  -> PREFERS_KEYWORD(얇게발림)
  <- HAS_BEE_KEYWORD_SIGNAL
Product(p_834921)

User(u_1001)
  -> HAS_CONCERN(건조함)
  <- ADDRESSES_CONCERN_SIGNAL
Product(p_834921)
```

### 추천 결과 예시
- 추천 상품: `p_834921`
- 추천 이유:
  - 얇게발림 키워드 선호와 일치
  - 건조함 concern 대응 신호 보유
  - 아침/세안후 사용 맥락과 일치
- hook copy:
  - `아침 루틴에 가볍게 얹히고 건조한 날에도 부담이 적은 쿠션이에요.`
- next-best-question:
  - `촉촉함이 더 중요하세요, 아니면 커버력이 더 중요하세요?`

---

## 8. User Graph 최종 설계

## 8-1. 유저 raw sources
- 회원 기본정보
- 구매 이벤트
- 재구매 요약
- 구매 기반 선호 요약
- 계절별 선호 요약
- 채팅 기반 선호/회피/고민/목표/루틴 요약

## 8-2. User Layer 2 canonical facts
- `HAS_SKIN_TYPE`
- `HAS_SKIN_TONE`
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `PREFERS_INGREDIENT`
- `AVOIDS_INGREDIENT`
- `HAS_CONCERN`
- `WANTS_GOAL`
- `WANTS_EFFECT`
- `PREFERS_CONTEXT`
- `PREFERS_BEE_ATTR`
- `AVOIDS_BEE_ATTR`
- `PREFERS_KEYWORD`
- `AVOIDS_KEYWORD`
- `SEASONAL_PREFERS_BRAND`
- `SEASONAL_PREFERS_CATEGORY`
- `REPURCHASES_PRODUCT_OR_FAMILY`

## 8-3. 유저 그래프를 Zep/Graphiti로 바로 가야 하나?
아니다. 현재 user 데이터는 이미 구조화돼 있으므로 MVP는 Postgres canonical fact로 충분하다.
Zep/Graphiti는 나중에 real-time chat memory와 temporal invalidation이 중요해질 때 추가한다.

---

## 9. 인프라 최종 권장안

## 9-1. 1순위: Postgres-first Hybrid
### 필수
- PostgreSQL
- pgvector
- batch/orchestration
- app server

### 선택
- Apache AGE (같은 Postgres 안에서 graph projection)
- Neo4j CE (serving graph mirror)

### 권장 운영 역할
- Postgres: SoR, normalized facts, aggregate marts, user graph facts
- AGE: 빠른 graph query 실험, openCypher 활용
- Neo4j CE: graph-heavy explainability와 analyst tooling 필요 시 선택
- pgvector: evidence retrieval

## 9-2. 왜 이게 1순위인가
- 조직에 Postgres 역량이 이미 있음
- raw/normalized/aggregate 구조가 관계형에 매우 잘 맞음
- user structured profile도 같이 담기 쉬움
- graph projection을 나중에 선택적으로 올릴 수 있음

## 9-3. Neo4j-only를 1순위로 두지 않는 이유
- raw row / aggregate mart / user summary를 그래프만으로 관리하는 건 과함
- 현재 프로젝트는 graph보다 normalization mart가 더 핵심
- 단, analyst 탐색/설명 경로/복합 traversal이 중요해지면 Neo4j는 여전히 강력한 2단계 선택지

## 9-4. Zep/Graphiti를 MVP에서 제외하는 이유
- 지금 user data는 long-running chat memory보다 structured summary 비중이 큼
- ontology cap / 별도 memory runtime을 지금 넣으면 복잡성 증가
- simulation/agent memory는 phase 3 이후가 자연스러움

---

## 10. 관계 매핑의 최종 원칙

### 원칙 1. Layer 1은 raw 보존
추출 결과는 append-only.

### 원칙 2. Layer 2는 65 canonical relation 보존
`used_on`, `comparison_with`, `has_attribute`, `causes` 같은 현재 canonical predicate를 그대로 보존.

### 원칙 3. Layer 3는 서비스 projection만 사용
- `HAS_BEE_ATTR_SIGNAL`
- `HAS_BEE_KEYWORD_SIGNAL`
- `USED_IN_CONTEXT_SIGNAL`
- `USED_WITH_TOOL_SIGNAL`
- `ADDRESSES_CONCERN_SIGNAL`
- `MAY_CAUSE_CONCERN_SIGNAL`
- `COMPARED_WITH_SIGNAL`
- `RECOMMENDED_TO_SEGMENT_SIGNAL`
등만 노출.

### 원칙 4. same_entity는 merge rule
relation이 아니라 normalization 전처리다.

### 원칙 5. reviewer proxy와 real user를 절대 섞지 않는다
리뷰 속 `Reviewer`, `I`, `my`는 reviewer proxy고, 회원 DB `user_id`는 실유저다.

---

## 11. 추천 / 개인화 / 후킹 / 질문 생성 플로우

```text
[실제 회원 행동/대화/프로필]
  -> user fact update
  -> user serving preferences
  -> product candidate retrieval
  -> graph match scoring
  -> reranking
  -> evidence retrieval
  -> explanation generation
  -> hook generation
  -> next-best-question generation
```

### 입력
- user summary graph
- product serving graph
- evidence retrieval store
- business constraints (stock, price, campaign)

### 출력
- ranked products
- explanation path
- hook_copy
- next_best_question

---

## 12. Simulation은 어디에 두나

세션 기록에서 합의된 대로, simulation은 운영 추천 엔진의 본체가 아니라 **정책 비교 실험실**이다.
따라서 phase 1 범위는 아니다.

### phase 1
- product graph
- user graph
- recommendation/explanation

### phase 2
- cohort builder
- scenario composer
- offline simulation mart

### phase 3
- agentic interview / policy lab
- Graphiti/Zep / OASIS 확장 검토

---

## 13. Claude Code에 바로 넘길 구현 순서

1. raw ingest DDL/loader 작성
2. `review_id`, `reviewer_proxy_id`, `target_product_id` 생성/resolve
3. placeholder resolver 구현
4. DATE splitter 구현 (`TemporalContext/Frequency/Duration`)
5. BEE_ATTR / KEYWORD dictionary normalizer 구현
6. relation 65 canonical loader + layer3 projection mapper 구현
7. Wrapped Signal emitter 구현
8. aggregate mart SQL 작성
9. AGE 또는 Neo4j projection loader 작성
10. candidate generator / reranker / explanation skeleton 작성

---

## 14. 절대 하지 말아야 할 것

- raw review phrase 전체를 메인 graph 노드로 적재
- BEE_ATTR를 제거하고 KEYWORD만 남기기
- relation 65개를 Layer 2에서 잃어버리기
- reviewer proxy를 real user와 join하기
- 리뷰 derived catalog signal로 상품 DB truth를 덮어쓰기
- Zep/Graphiti를 MVP 필수 요소로 넣기

---

## 15. 최종 한 문장

**이 프로젝트의 정답은 “상품 DB를 정본으로 둔 Postgres-first core 위에, 리뷰 raw를 Layer 1에 보존하고, relation 65개를 Layer 2에서 그대로 유지하며, Layer 3에서만 BEE_ATTR/KEYWORD/Context/Concern 중심의 serving graph로 투영한 뒤, 이를 유저 그래프와 공통 개념층에서 연결해 추천/개인화/설명에 쓰는 구조”다.**
