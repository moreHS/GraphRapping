# GraphRapping 프로젝트 이해 문서

이 문서는 GraphRapping 프로젝트의 입력 데이터, 전처리 목적, 레이어 구조, 최종 활용 방식을 쉽게 이해하기 위한 설명입니다.

---

## 0. 2026-04-25 현재 구현/검증 상태

현재 GraphRapping은 감사 후속 P0/P1/P2 안정화 항목을 모두 반영한 상태입니다.

완료된 핵심 안정화는 다음과 같습니다.

- 상품 매칭 및 mock 데이터 계약 복구
- batch/web quarantine 집계 정합성 복구
- DB migration 순서와 incremental persistence correctness 보강
- serving SQL DDL/repo 계약 동기화
- rs.jsonl relation-ready 계약 공식화
- canonical fact → wrapped signal → aggregate promotion metadata 관통
- 추천 scoring/config/UI/docs 정합성 정리
- repo-wide ruff lint baseline 0
- 실제 Postgres integration test scaffold 추가
- Docker-backed Postgres integration runner 추가
- `mypy` type stability baseline 0
- GitHub Actions CI quality gate 추가

현재 로컬 검증 기준은 다음 명령입니다.

```bash
python -m ruff check src
python -m mypy src
python -m pytest tests/ -q
bash scripts/run_postgres_integration.sh
```

마지막 확인 결과:

- `python -m ruff check src` → 통과
- `python -m mypy src` → `Success: no issues found in 86 source files`
- `python -m pytest tests/ -q` → `324 passed, 3 skipped`
- `bash scripts/run_postgres_integration.sh` → 실제 Docker Postgres에서 `3 passed`

관련 결정/실행 기록은 `DECISIONS/2026-04-25_*` 문서에 남겨져 있습니다.

---

## 1. 한눈에 보는 쉬운 설명

GraphRapping은 리뷰 문장을 바로 추천에 쓰지 않습니다.

리뷰에서 나온 표현을 먼저 "믿을 수 있는 상품 신호"로 정리하고, 그 신호를 유저 취향과 맞춰서 추천과 설명에 활용하는 시스템입니다.

예를 들어 리뷰에 다음과 같은 표현이 있다고 가정합니다.

- "이 세럼은 촉촉하고 산뜻해요"
- "건조한 피부에 괜찮았어요"
- "아침에 바르기 좋아요"

이 문장을 그대로 쓰는 것이 아니라, 아래처럼 정리합니다.

- 상품 A는 `보습력` 신호가 있다.
- 상품 A는 `촉촉함` 키워드가 있다.
- 상품 A는 `건조함` 고민에 대응할 가능성이 있다.
- 상품 A는 `아침 사용` 맥락과 관련이 있다.

그 다음 유저 프로필과 비교합니다.

- 유저는 `건조함` 고민이 있다.
- 유저는 `가벼운 제형`을 선호한다.
- 유저는 특정 브랜드나 카테고리를 선호한다.

이렇게 상품 신호와 유저 신호를 공통 개념으로 맞춰서 추천합니다.

---

## 2. 입력 데이터

프로젝트의 입력 데이터는 크게 4종류입니다.

### 2.1 상품 데이터

상품 데이터는 프로젝트에서 가장 중요한 source of truth입니다.

주요 정보는 다음과 같습니다.

- 상품 ID
- 상품명
- 브랜드
- 카테고리
- 성분
- 가격
- 제조국
- 주요 효능
- 대표 상품군 또는 패밀리 ID

리뷰에서 어떤 정보가 나오더라도 상품 DB의 정본 정보를 함부로 덮어쓰지 않습니다.

예를 들어 리뷰에 "성분이 좋아요"라는 말이 있어도, 실제 성분 목록은 상품 DB를 기준으로 합니다. 리뷰 신호는 정본을 보강하거나 검증하는 역할을 합니다.

### 2.2 리뷰 데이터

리뷰 데이터는 원문과 추출 결과로 구성됩니다.

리뷰 추출 결과에는 보통 다음이 포함됩니다.

- `NER`: 상품, 브랜드, 카테고리, 성분, 날짜, 색상 같은 entity mention
- `BEE`: 보습력, 발림성, 밀착력, 사용감, 향 같은 beauty evaluation expression
- `REL`: 상품과 entity 사이의 관계

리뷰 입력 경로는 두 가지입니다.

- Relation project JSON: 이미 NER/BEE/REL이 추출된 중간 포맷
- rs.jsonl: 운영 원본에 가까운 S3 형식

두 경로 모두 내부적으로는 `RawReviewRecord` 형태로 변환되어 같은 파이프라인을 탑니다.

### 2.3 유저 데이터

유저 데이터는 normalized 3-group 구조를 공식 입력으로 봅니다.

- `basic`: 성별, 연령대, 피부 타입, 피부 톤
- `purchase_analysis`: 선호 브랜드, 선호 카테고리, 재구매 경향
- `chat`: 피부 고민, 케어 목표, 선호 성분, 회피 성분, 선호 제형 등

유저 데이터는 상품 추천을 위한 `serving_user_profile`로 요약됩니다.

### 2.4 설정 사전

설정 사전은 raw text를 공통 개념으로 정규화하는 기준입니다.

대표적인 설정은 다음과 같습니다.

- BEE 속성 사전
- 키워드 surface map
- 피부 고민 사전
- 케어 목표 alias map
- 제형 texture map
- relation canonical map
- projection registry
- scoring weight

이 사전들이 프로젝트의 의미 체계를 결정합니다.

---

## 3. 전처리 단계의 목적

전처리의 핵심 목적은 raw 데이터를 추천에 바로 쓰기 어려운 상태에서, 추천 가능한 공통 신호로 바꾸는 것입니다.

리뷰 raw는 풍부하지만 다음 문제가 있습니다.

- 상품명이 정확히 일치하지 않을 수 있습니다.
- "이 제품", "나", "Reviewer" 같은 placeholder가 많습니다.
- 표현이 다양합니다. 예: "촉촉함", "수분감", "보습감"
- 리뷰 하나만 보면 신뢰하기 어려운 신호가 많습니다.
- BEE phrase가 실제 리뷰 대상 상품에 대한 말인지 불확실할 수 있습니다.

그래서 전처리에서 아래 작업을 합니다.

### 3.1 상품 매칭

리뷰의 브랜드명과 상품명을 실제 `product_id`에 연결합니다.

이 단계가 실패하면 해당 리뷰에서 나온 신호는 상품에 귀속될 수 없기 때문에 추천 신호로 올라가지 못합니다.

### 3.2 리뷰어 proxy 생성

리뷰 작성자는 실제 회원과 직접 섞지 않습니다.

리뷰 작성자는 `reviewer_proxy`로 분리하고, 실제 서비스 유저는 별도의 `user_id`로 관리합니다. 이렇게 해야 익명 리뷰 evidence와 실제 개인화 유저 그래프가 섞이지 않습니다.

### 3.3 Placeholder 해소

리뷰에는 다음과 같은 표현이 자주 나옵니다.

- Review Target
- Reviewer
- 이 제품
- 이거
- 나

이런 표현을 review-local scope에서 실제 대상 상품 또는 리뷰어 proxy로 바꿉니다.

### 3.4 BEE attribution

BEE phrase가 진짜 리뷰 대상 상품에 대한 표현인지 판단합니다.

예를 들어 비교 리뷰에서 다음 문장이 있을 수 있습니다.

> A 제품은 촉촉한데, B 제품은 끈적해요.

이때 `촉촉함`과 `끈적함`을 모두 현재 리뷰 대상 상품에 붙이면 안 됩니다. 그래서 BEE phrase가 target product에 연결되어 있는지 확인합니다.

### 3.5 BEE 정규화

BEE는 두 층으로 나누어 보존합니다.

- `BEE_ATTR`: 보습력, 발림성, 밀착력, 사용감 같은 평가 축
- `KEYWORD`: 촉촉함, 산뜻함, 끈적임 없음 같은 구체 표현

즉 BEE_ATTR를 KEYWORD에 흡수하지 않습니다.

이 구조 덕분에 "사용감 축은 맞는데, 구체 키워드는 다르다" 같은 세밀한 추천이 가능합니다.

### 3.6 Relation canonicalization

raw relation을 프로젝트가 관리하는 canonical predicate로 맞춥니다.

Layer 2에서는 가능한 한 원래 relation 의미를 보존합니다. 추천용으로 압축하는 것은 Layer 2.5와 Layer 3에서 수행합니다.

### 3.7 Quarantine

확실하지 않은 데이터는 억지로 추천에 쓰지 않고 격리합니다.

예시는 다음과 같습니다.

- 상품 매칭 실패
- 미등록 키워드
- 모호한 placeholder
- projection registry에 없는 relation
- 타입이 불명확한 entity

### 3.8 Corpus promotion

리뷰 하나에서 나온 신호를 바로 추천에 쓰지 않습니다.

여러 리뷰에서 반복되고, 신뢰도가 충분하고, synthetic 비율이 낮은 신호만 추천용 serving layer로 승격합니다.

---

## 4. 레이어 구조

GraphRapping은 5-layer pipeline으로 이해하면 됩니다.

```text
Layer 0   Product/User Master Truth
Layer 1   Raw Evidence
Layer 2   Canonical Fact
Layer 2.5 Wrapped Signal
Layer 3   Aggregate/Serving
Layer 4   Recommendation
```

---

## 5. Layer 0: Product/User Master Truth

Layer 0은 정본 데이터입니다.

### Product Master

상품의 source of truth입니다.

포함 정보:

- product_id
- product_name
- brand_id / brand_name
- category_id / category_name
- ingredients
- main_benefits
- price
- country_of_origin
- variant_family_id

상품 DB 정보는 리뷰 신호보다 우선합니다.

### User Master

실제 유저의 기본 정보입니다.

포함 정보:

- user_id
- age_band
- gender
- skin_type
- skin_tone

---

## 6. Layer 1: Raw Evidence

Layer 1은 원천 evidence layer입니다.

대표 테이블 또는 구조는 다음과 같습니다.

- `review_raw`
- `ner_raw`
- `bee_raw`
- `rel_raw`

이 레이어의 목적은 추천이 아니라 보존과 재처리입니다.

나중에 사전이 바뀌거나, relation mapping이 바뀌거나, 상품 매칭 로직이 개선되면 Layer 1을 다시 처리해서 Layer 2 이후를 재생성할 수 있습니다.

---

## 7. Layer 2: Canonical Fact

Layer 2는 raw evidence를 canonical fact로 바꾼 레이어입니다.

형태는 기본적으로 다음과 같습니다.

```text
subject - predicate - object
```

예시는 다음과 같습니다.

```text
product:P002 - has_attribute - concept:BEEAttr:bee_attr_moisture
concept:BEEAttr:bee_attr_moisture - HAS_KEYWORD - concept:Keyword:촉촉함
product:P002 - used_on - concept:TemporalContext:아침
product:P002 - addresses - concept:Concern:concern_dryness
```

Layer 2의 핵심 원칙은 의미 보존입니다.

이 단계에서는 추천에 필요한 것만 남기는 것이 아니라, 원래 relation 의미를 최대한 살립니다. 그래서 audit, debug, analyst query에 유리합니다.

---

## 8. Layer 2.5: Wrapped Signal

Layer 2.5는 canonical fact를 추천에 쓸 수 있는 signal 형태로 바꾸는 레이어입니다.

이 변환은 `projection_registry.csv`가 결정합니다.

예를 들어 다음 canonical fact가 있다고 하면:

```text
Product - has_attribute - BEEAttr
```

이것은 wrapped signal에서 다음처럼 바뀝니다.

```text
HAS_BEE_ATTR_SIGNAL
```

또 다른 예:

```text
BEEAttr - HAS_KEYWORD - Keyword
```

이것은 상품에 연결된 keyword signal로 wrapping됩니다.

```text
HAS_BEE_KEYWORD_SIGNAL
```

Layer 2.5의 목적은 Layer 2의 풍부한 relation 중 추천과 탐색에 쓸 수 있는 것만 명시적으로 고르는 것입니다.

---

## 9. Layer 3: Aggregate/Serving

Layer 3는 여러 리뷰의 signal을 상품별로 집계하고, 최종 serving profile을 만드는 레이어입니다.

### Product Serving Profile

`serving_product_profile`은 추천에 바로 사용할 수 있는 상품 요약입니다.

포함 정보:

- 상품 truth: 브랜드, 카테고리, 성분, 가격, 효능
- concept join key: brand_concept_ids, category_concept_ids, ingredient_concept_ids
- 리뷰 기반 promoted signal
  - top_bee_attr_ids
  - top_keyword_ids
  - top_context_ids
  - top_concern_pos_ids
  - top_concern_neg_ids
  - top_tool_ids
  - top_comparison_product_ids
  - top_coused_product_ids
- freshness 정보
  - review_count_30d
  - review_count_90d
  - review_count_all

쉽게 말해, 이 상품이 어떤 성격의 상품인지 추천 엔진이 바로 볼 수 있게 압축한 형태입니다.

### User Serving Profile

`serving_user_profile`은 추천에 바로 사용할 수 있는 유저 요약입니다.

포함 정보:

- 피부 타입
- 피부 톤
- 선호 브랜드
- 선호 카테고리
- 선호 성분
- 회피 성분
- 피부 고민
- 케어 목표
- 선호 BEE_ATTR
- 선호 keyword
- 선호 context
- 구매/보유 기반 신호

쉽게 말해, 이 유저가 무엇을 좋아하고 무엇을 피해야 하는지 추천 엔진이 바로 볼 수 있는 형태입니다.

---

## 10. Layer 4: Recommendation

Layer 4는 실제 추천 로직입니다.

주요 단계는 다음과 같습니다.

### 10.1 Candidate Generation

먼저 추천 후보를 만듭니다.

이 단계에서는 유저와 상품의 공통 concept overlap을 봅니다.

예:

- 유저 선호 브랜드와 상품 브랜드가 겹치는가?
- 유저 고민과 상품 concern signal이 겹치는가?
- 유저 선호 키워드와 상품 top keyword가 겹치는가?
- 유저 선호 성분이 상품 성분에 포함되는가?
- 유저가 회피하는 성분이 상품에 들어 있는가?

회피 성분 충돌이나 strict mode의 카테고리 불일치 같은 경우는 후보에서 제외될 수 있습니다.

### 10.2 Scoring

후보 상품에 점수를 부여합니다.

주요 feature는 다음과 같습니다.

- keyword_match
- residual_bee_attr_match
- context_match
- concern_fit
- ingredient_match
- brand_match_conf_weighted
- goal_fit_master
- category_affinity
- freshness_boost
- skin_type_fit
- purchase_loyalty_score
- novelty_bonus
- family-level personalization
- tool/co-used product alignment

점수는 단순 overlap만 보는 것이 아니라 review support count에 따라 shrinkage를 적용합니다.

리뷰 근거가 적은 상품은 과하게 높은 점수를 받지 않도록 줄입니다.

### 10.3 Reranking

점수만으로 정렬하면 같은 브랜드나 같은 카테고리가 상위에 몰릴 수 있습니다.

그래서 reranker가 브랜드/카테고리 다양성을 일부 반영합니다.

### 10.4 Explanation

추천 설명은 LLM이 임의로 꾸미는 것이 아니라, 실제 score에 기여한 overlap concept을 기반으로 만듭니다.

예:

- 선호 키워드 `촉촉함`과 일치
- `건조함` 고민 대응 신호 보유
- 제품 truth의 `보습` 효능과 유저 목표가 부합
- 선호 브랜드와 일치

### 10.5 Hook / Next Question

추천 결과를 기반으로 후킹 문구와 다음 질문도 만들 수 있습니다.

예:

- "촉촉한 사용감에 가까운 제품이에요"
- "건조함 고민에 비교적 잘 맞는 편이에요"
- "가벼운 텍스처가 더 중요하세요, 촉촉함이 더 중요하세요?"

---

## 11. 최종 레이어 활용법

최종적으로 서비스에서 주로 사용하는 것은 Layer 3와 Layer 4입니다.

### 11.1 추천

`serving_user_profile`과 `serving_product_profile`을 비교해서 추천합니다.

핵심은 공통 concept plane입니다.

예:

```text
User concern: concern_dryness
Product signal: ADDRESSES_CONCERN_SIGNAL concern_dryness
```

이 둘이 같은 concern concept으로 연결되면 추천 점수에 반영됩니다.

### 11.2 설명 가능한 추천

추천 결과는 단순히 "이 상품이 좋아요"가 아니라, 왜 추천됐는지 설명할 수 있어야 합니다.

예:

```text
이 상품은 유저의 건조함 고민과 맞고,
리뷰에서 보습력과 촉촉함 신호가 반복적으로 관찰되었으며,
제품 마스터의 주요 효능도 보습으로 등록되어 있습니다.
```

### 11.3 상품 탐색

상품별로 어떤 semantic signal이 있는지 볼 수 있습니다.

예:

- 이 상품의 top BEE attribute는 무엇인가?
- 이 상품은 어떤 keyword로 많이 언급되는가?
- 어떤 사용 맥락에서 많이 쓰이는가?
- 어떤 피부 고민과 연결되는가?

### 11.4 Evidence Debugging

Layer 1과 Layer 2는 운영자가 신호의 원천을 추적할 때 씁니다.

예:

- 왜 이 상품에 `촉촉함` 신호가 붙었는가?
- 어떤 리뷰에서 나온 신호인가?
- 어떤 canonical fact를 통해 signal이 생성되었는가?
- 이 신호가 promoted된 이유는 무엇인가?

### 11.5 Graph View

Graph API/UI는 두 관점을 가질 수 있습니다.

#### Corpus View

promoted serving signal 중심입니다.

추천과 탐색에서 쓰는 안정적인 상품 그래프입니다.

#### Evidence View

per-review signal 중심입니다.

raw evidence와 디버깅에 유리합니다.

---

## 12. 핵심 설계 의도

이 프로젝트의 핵심은 리뷰 전체를 무작정 큰 그래프로 합치는 것이 아닙니다.

리뷰는 evidence로 보존하고, 상품 DB는 정본으로 유지하며, 여러 리뷰에서 반복되고 신뢰할 수 있는 신호만 serving layer로 승격합니다.

그 결과 최종 목표는 다음입니다.

- 리뷰 신호를 상품 의미 신호로 구조화
- 유저 취향과 상품 신호를 공통 concept plane에서 연결
- 개인화 추천 품질 향상
- 추천 이유를 설명 가능하게 제공
- 이후 cohort simulation, 신제품 반응 실험, 캠페인 기획으로 확장

---

## 13. 요약

GraphRapping은 아래 흐름으로 이해하면 됩니다.

```text
상품 DB + 리뷰 추출물 + 유저 프로필
        ↓
정규화 / 매칭 / placeholder 해소 / attribution
        ↓
Canonical Fact Layer
        ↓
Projection Registry 기반 Wrapped Signal
        ↓
Corpus Aggregation + Serving Profile
        ↓
추천 / 설명 / 후킹 / 탐색
```

짧게 말하면:

> GraphRapping은 noisy한 리뷰 raw를 상품 중심 semantic signal로 정리하고, 유저 preference graph와 연결해 설명 가능한 개인화 추천을 만드는 프로젝트입니다.
