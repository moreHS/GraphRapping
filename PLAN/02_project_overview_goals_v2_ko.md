# Relation 프로젝트 개요 / 목적 / 범위 (최종 통합본)

## 1. 프로젝트 한 줄 정의

이 프로젝트는 **상품 DB를 정본으로 삼고**, 리뷰에서 추출된 **NER + BEE + REL** 데이터를 **상품 중심의 의미 신호 그래프**로 재구성한 뒤, 이를 **유저 그래프와 공통 개념층에서 연결**하여 추천, 개인화, 설명, 탐색, 이후 시뮬레이션까지 가능하게 만드는 시스템이다.

---

## 2. 왜 이 프로젝트를 하는가

현재 보유 자산은 이미 강력하다.

- 상품 리뷰 원문
- 리뷰에서 추출된 NER 10개 타입
- 리뷰 평가 표현(BEE) 39개 속성 타입
- 정규화된 relation 65개(+ 전처리/자동 생성 relation)
- 실제 상품 DB(브랜드, 카테고리, 가격, 제조국, 메인효능, 성분 등)
- 실제 회원 데이터(가입 정보, 구매 기반 요약, 채팅 기반 요약)

핵심 문제는 **이 풍부한 raw triple을 그대로 추천 시스템에 쓰면 너무 raw하고, 너무 sparse하며, 너무 noisy** 하다는 점이다.

따라서 목표는 다음 3개다.

1. raw triple을 그대로 버리지 않고 **정규화된 signal layer로 한 번 더 래핑**한다.
2. 상품측 의미 신호와 유저측 선호/회피 신호를 **공통 개념층에서 연결**한다.
3. 추천 결과를 **설명 가능한 형태**로 만들고, 이후에는 cohort simulation까지 연결한다.

---

## 3. 이 프로젝트의 최종 산출물

### 3-1. Product Semantic Graph
상품 DB의 정본 정보와 리뷰 기반 의미 신호를 합친 제품 그래프.

### 3-2. User Preference Graph
회원 가입 정보, 구매 기반 요약, 채팅 기반 요약을 공통 개념 노드에 연결한 유저 그래프.

### 3-3. Recommendation / Personalization Engine
두 그래프를 공통 개념층에서 연결하여:
- 개인화 추천
- 후킹 문구 생성
- 설명 가능한 추천
- next-best-question 생성
을 수행하는 엔진.

### 3-4. Simulation-ready Foundation
이후 대표집단(cohort) 시뮬레이션, 신제품/이벤트 반응 실험으로 확장 가능한 기반.

---

## 4. 현재 확정된 해석

### 4-1. 리뷰는 중심 노드가 아니다
리뷰는 **evidence layer**다.
즉 메인 serving graph의 중심 traversal 노드가 아니라:
- 원문 근거 보관
- 후처리 재실행 가능성 확보
- 설명 가능한 추천 근거 제공
을 위한 원천 데이터다.

### 4-2. 상품 DB가 정본이다
브랜드, 카테고리, 성분, 가격, 제조국, 메인효능 같은 정보는 상품 DB가 source of truth다.
리뷰 추출 relation은 이를 덮어쓰지 않고:
- validation
- enrichment 후보
- inconsistency detection
용으로 쓴다.

### 4-3. BEE_ATTR는 KEYWORD에 흡수되면 안 된다
BEE는 두 층으로 살아야 한다.
- `BEE_ATTR`: 발림성, 밀착력, 사용감, 향 등 속성 축
- `KEYWORD`: 얇게발림, 잘밀착됨, 무향, 촉촉함 같은 정규 키워드

즉 product → BEE_ATTR → KEYWORD 구조를 유지해야 한다.

### 4-4. Layer 2와 Layer 3의 canonical은 다르다
- **Layer 2 canonical**: 현재 보유한 canonical relation 65개를 그대로 유지하는 fact layer
- **Layer 3 canonical**: 추천/개인화에 필요한 projection만 집계한 serving layer

---

## 5. 범위

## 포함
- raw review / raw extraction 적재
- target_product_id 연결
- reviewer_proxy_id 생성
- NER/BEE/REL 정규화
- Product graph 구축
- User graph 구축
- Product/User 공통 개념층 연결
- 추천 후보 생성 / 랭킹 / 설명
- 향후 simulation 확장을 위한 인터페이스 설계

## 초기 범위에서 제외
- 리뷰 원문 phrase 전체를 메인 그래프 노드로 적재
- RDF/SPARQL 우선 도입
- 모든 회원에 대해 real-time conversational memory agent 구성
- MiroFish/OASIS 직접 통합 구현
- cohort simulator 본체 구현

---

## 6. 핵심 설계 원칙

1. **Product anchor first**: 리뷰 신호는 항상 canonical product에 귀속한다.
2. **Evidence is row, serving is graph**: raw phrase/relation은 row로, 집계 결과만 graph로.
3. **Keep Layer 2 faithful**: relation 65개는 Layer 2에서 그대로 보존한다.
4. **Compress only at Layer 3**: 추천에 필요한 edge만 projection한다.
5. **User/Product join through concepts**: 두 그래프는 공통 개념 노드에서 만난다.
6. **Postgres-first core**: 조직 맥락상 Postgres를 시스템 오브 레코드로 둔다.
7. **Graph projection is optional but valuable**: AGE 또는 Neo4j projection은 추천/탐색/설명 레이어로 쓴다.

---

## 7. 최종 추천 아키텍처

```text
Product DB (truth)
   +
Review Raw / NER Raw / BEE Raw / REL Raw
   +
User Raw / Purchase Events / Chat Summaries
   ↓
Normalization / Linking / Wrapping
   ↓
Canonical Fact Layer (Layer 2)
   ↓
Aggregate Serving Layer (Layer 3)
   ↓
Recommendation / Explanation / Hooking
   ↓
(Option) Simulation / Cohort Lab
```

---

## 8. 1차 인프라 권장안

### 기본안: Postgres-first Hybrid
- **PostgreSQL**: raw, normalized fact, aggregate mart, user summary, purchase events
- **pgvector**: 리뷰 근거 문장/구문 evidence retrieval
- **Apache AGE 또는 Neo4j projection**: graph traversal/explanation/analyst query
- **Application service**: 추천 점수화, hook generation, next-best-question

### 이후 확장안
- live conversational memory가 중요해지면 Graphiti/Zep 계층 추가
- simulation이 중요해지면 OASIS/MiroFish 계열 sandbox 추가

---

## 9. 기대 효과

- 리뷰 신호를 제품 의미 신호로 구조화
- 개인화 추천 정밀도 향상
- 설명 가능한 추천 제공
- 후킹/구매유도 angle 자동 생성 가능
- 신제품/캠페인 실험 기반 마련

---

## 10. 성공 기준

### 데이터 성공 기준
- review_raw의 높은 비율이 canonical product에 링크됨
- placeholder(`Review Target`, `Reviewer`)가 안정적으로 resolve됨
- BEE phrase가 BEE_ATTR + KEYWORD로 일관되게 정규화됨
- Layer 2 relation 65개가 손실 없이 보존됨

### 서비스 성공 기준
- Product → BEE_ATTR / KEYWORD / Context / Concern 질의 가능
- User → Brand / Category / Ingredient / BEE_ATTR / KEYWORD / Concern 연결 가능
- 설명 가능한 추천 path 반환 가능
- recent window 기준 집계 업데이트 가능

### 운영 성공 기준
- append-only raw
- idempotent normalization
- daily batch / incremental upsert 가능
- dictionary 확장과 manual review queue 운영 가능
