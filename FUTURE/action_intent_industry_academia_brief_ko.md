# 산학협력 제안서 초안
## 주제: 개인화 추천을 위한 Action / Intent Overlay 모델 공동 연구

## 1. 한 문장 요약
저희는 이미 운영 중인 **상품 지식 신호 + 리뷰 코퍼스 신호 + 개인화 프로파일** 기반 추천 시스템 위에, **유저의 동적 액션/의도(Action / Intent) 신호를 추가로 추론하는 모델**을 얹고 싶습니다. 이 모델은 기존 모델을 대체하는 것이 아니라, 현재 시스템의 한계를 보완하는 **Overlay Layer**로 설계되어야 합니다.

---

## 2. 저희가 지금 하고 있는 것
저희는 현재 뷰티 도메인에서 다음 3종의 정보를 통합하는 추천/탐색 시스템을 구축 중입니다.

### 2-1. 상품 데이터
상품 정본(Product Master)을 보유하고 있으며, 대략 아래와 같은 정보를 활용합니다.
- 브랜드
- 카테고리
- 성분
- 가격
- 대표 효능(main benefits)
- 국가 정보
- family / variant 관계

### 2-2. 리뷰 데이터
리뷰 텍스트에 대해 이미 별도의 추출 파이프라인이 존재합니다.
현재는 리뷰에서 다음을 추출합니다.
- NER
- BEE(리뷰 속 제품 평가/감상 속성)
- REL(리뷰 타깃과 속성/개체 간 관계)

이 리뷰 추출 자산은 **이미 학습/운영 중인 자산**이며, 이번 과제의 목적은 이 모델을 다시 만드는 것이 아닙니다.

### 2-3. 유저 프로파일 데이터
유저 챗 및 일부 행동/이력 데이터를 바탕으로 현재도 다음과 같은 **Stable Profile**을 만들고 있습니다.
- 선호 브랜드
- 선호/기피 성분
- 선호 제형(Texture)
- 관심 카테고리
- concern / goal
- 구매/재구매 이력

즉, 현재 저희 시스템은 이미 **장기적/반장기적 개인화 프로파일**은 가지고 있습니다.

---

## 3. 현재 시스템의 한계
현재 시스템은 아래에는 강합니다.
- 이 유저가 원래 어떤 취향을 가졌는가
- 어떤 제품이 시장 전반에서 어떤 평가를 받는가
- 어떤 성분/효능/제형이 잘 맞는가

하지만 아래에는 상대적으로 약합니다.
- **지금 이 유저가 무엇을 하려는가**
- **현재 세션/최근 며칠 동안의 구매의사, 관심도, 비교 상태**
- **정적 선호와 별개로 발생하는 동적 intent 변화**

예를 들어 아래 같은 상황을 잘 잡고 싶습니다.
- 지금 탐색 중인지 / 비교 중인지 / 구매 직전인지
- 특정 브랜드/패밀리에 일시적 관심이 생겼는지
- 이미 보유한 제품과 같은 family의 다른 variant를 보고 있는지
- 재구매 의사가 생겼는지
- 무엇을 회피하려는지

현재 저희는 이 축을 별도의 모델로 보강하려고 합니다.

---

## 4. 이번 산학과제에서 하고 싶은 것
### 핵심 목표
기존 개인화 시스템 위에 **Action / Intent Overlay 모델**을 추가하고 싶습니다.

이 모델은 다음을 목표로 합니다.
1. **유저의 현재 액션/인텐트 상태를 추론**한다.
2. 그 액션/인텐트가 **무엇을 향하고 있는지(target grounding)** 판정한다.
3. 이 결과를 기존 추천 시스템에 **overlay 형태로 통합**한다.
4. Stable Profile과 충돌하지 않도록, **별도 레이어**로 운영 가능해야 한다.

즉 저희가 원하는 것은,
> "유저의 장기 취향을 아는 시스템" 위에
> "지금 이 순간 무엇을 하려는지 읽어내는 모델"을 하나 더 얹는 것
입니다.

---

## 5. 중요한 전제 조건
이 과제에서 특히 중요한 전제는 아래와 같습니다.

### 5-1. 기존 리뷰 추출 자산은 고정
- 리뷰용 NER / BEE / REL 모델은 이미 존재합니다.
- 이번 과제에서는 그 모델을 재학습하거나 대체하는 것이 아닙니다.
- 리뷰는 이번 모델의 **주 입력**이 아니라, 보조 자산 또는 제품 측 시장 신호로 쓰입니다.

### 5-2. 기존 Stable Profile도 유지
- 현재 유저의 선호 브랜드, 선호 제형, 기피 성분 등은 이미 추출/정규화하고 있습니다.
- 새 모델은 이를 덮어쓰는 것이 아니라, **동적 overlay**를 추가하는 역할이어야 합니다.

### 5-3. Chat에 대해 리뷰용 NER/BEE/REL 성능을 전제하지 않음
- 현재 리뷰용 추출 모델은 리뷰 문장에 최적화돼 있습니다.
- 유저 챗에 동일 모델을 바로 적용하는 것은 성능이 불확실합니다.
- 따라서 새 모델은 **chat에서 NER/BEE/REL을 다시 뽑는 모델**이 아니라,
  - stable profile snapshot
  - dictionary / entity linker
  - session / browse / purchase metadata
  를 바탕으로 **action / intent와 target을 판정하는 모델**로 설계되어야 합니다.

---

## 6. 연구실이 맡아주셨으면 하는 범위
저희가 연구실에 기대하는 범위는 아래 2개 축입니다.

### A. 모델 설계 / 학습
다음 질문에 대한 모델링을 제안해주셨으면 합니다.
- 현재 유저의 intent stage는 무엇인가?
- 어떤 action이 발생했는가?
- 그 action / intent가 어떤 target(브랜드, 카테고리, family, concern, goal, 제형 축, keyword 등)을 향하고 있는가?
- 그 강도와 시간축(horizon)은 어느 정도인가?

### B. 시스템 통합 설계
학습된 모델의 출력이 기존 시스템에 어떻게 들어가야 하는지,
- 어떤 테이블/스키마/레이어에 저장할지
- Stable Profile과 어떻게 분리할지
- 추천/탐색/설명에서 어떻게 사용할지
를 아키텍처 관점에서 설계해주셨으면 합니다.

---

## 7. 저희가 원하는 모델 출력 형태
저희는 생성형 자유서술 출력보다, **구조화된 출력**을 선호합니다.

예시:
```json
{
  "user_id": "u123",
  "source": "chat",
  "intent_stage": "COMPARE",
  "action_types": ["ASK_COMPARISON"],
  "targets": [
    {"target_type": "Brand", "target_id": "brand_hera"},
    {"target_type": "Brand", "target_id": "brand_clio"},
    {"target_type": "Category", "target_id": "cat_cushion"}
  ],
  "strength": 0.84,
  "horizon": "NOW",
  "explicitness": "EXPLICIT",
  "polarity": "POS"
}
```

즉,
- intent stage
- action type
- target grounding
- strength / horizon / explicitness / polarity
가 구조적으로 나와야 합니다.

---

## 8. 저희가 생각하는 타깃 클래스 방향
연구실에서 더 좋은 대안을 주셔도 좋지만, 현재 저희가 생각하는 기본 틀은 아래와 같습니다.

### 8-1. Intent Stage
- NONE
- DISCOVER
- INTEREST
- CONSIDER
- COMPARE
- PURCHASE_INTENT
- REPURCHASE_INTENT
- AVOID
- POST_PURCHASE_HELP

### 8-2. Action Type
- ASK_RECOMMENDATION
- ASK_COMPARISON
- ASK_INFO
- SEARCH
- VIEW_DETAIL
- FILTER
- SAVE
- ADD_TO_CART
- PURCHASE
- REORDER
- MENTION_LIKE
- MENTION_DISLIKE
- MENTION_BUY_INTENT
- MENTION_REPURCHASE_INTENT
- MENTION_AVOIDANCE

### 8-3. Target Grounding Space
(새로 엔티티를 추출한다기보다, 기존 시스템 또는 로그에서 확보한 candidate target 중 선택)
- Product
- ProductFamily
- Brand
- Category
- Concern
- Goal
- Ingredient
- BEEAttr
- Keyword
- Context
- Tool
- None

### 8-4. Strength / Horizon / Explicitness / Polarity
- strength: 0~1
- horizon: NOW / SOON / LATER
- explicitness: EXPLICIT / IMPLICIT
- polarity: POS / NEG / MIXED

---

## 9. 입력 데이터 소스에 대한 생각
저희는 현재 다음처럼 생각하고 있습니다.

### 9-1. Chat
**주 입력**
- 현재 intent / action / target을 가장 잘 드러냄
- 비교, 구매의사, 회피, 관심도 변화를 직접 표현함

### 9-2. Browse / Search / Click / Cart / Purchase Log
**주 입력**
- 행동 기반 신호를 제공
- purchase intent, compare state, repurchase intent를 잘 반영

### 9-3. Review
**보조 입력**
- 현재 유저의 실시간 intent 추론에는 직접 쓰지 않음
- 대신 아래 용도로 사용 가능
  - repurchase / recommend / avoid 표현 사전 구축
  - product-side outcome / market signal
  - weak label 보조 자산

즉,
> 리뷰는 유저별 동적 action 모델의 주 입력이 아니라,
> 제품 측 시장 신호 및 액션 표현 사전용 보조 데이터
라고 보고 있습니다.

---

## 10. 연구실에 기대하는 학습 방향
저희가 생각하는 현실적인 방향은 아래와 같습니다.

### 10-1. 기존 자산은 유지, 새 모델만 추가
- 리뷰 추출 모델 재학습 X
- personal-agent 프로파일 추출 유지
- 새 action/intention 모델만 추가

### 10-2. Weak supervision + 소형 PLM 중심
대규모 수작업 라벨링보다,
- 규칙 기반 weak label
- 로그 기반 silver label
- 필요시 sLLM teacher 보조
- small encoder / classifier 학습
이 현실적이라고 생각합니다.

### 10-3. Multi-head 구조 선호
한 모델이 아래를 동시에 예측하는 구조를 선호합니다.
- intent stage
- action type
- target grounding/selection
- strength / horizon / explicitness / polarity

---

## 11. 저희가 기대하는 산출물
연구실과의 산학 과제 결과물은 아래 수준을 기대합니다.

### 11-1. 모델링/학습 설계 산출물
- 타깃 클래스 정의서
- 데이터셋 스키마
- weak label 규칙표
- 학습/평가 방법론 제안
- baseline 모델 제안
- 오류 케이스 분류표

### 11-2. 시스템 통합 산출물
- 기존 GraphRapping에 overlay layer로 넣는 아키텍처 제안
- canonical fact / action fact / serving profile 확장안
- stable profile과 dynamic overlay 분리 원칙
- TTL / recency / source-weight 운영 규칙
- 추천/탐색/설명에 반영하는 방식

### 11-3. 실험 산출물
- baseline 실험 결과
- ablation 또는 rule-only vs model 비교
- precision / recall / calibration / usefulness 분석
- 실제 추천 품질에 미치는 영향 분석

### 11-4. 가능하면 기대하는 구현 수준
- 데이터 전처리 스크립트
- 학습 코드 또는 최소 재현 가능한 스켈레톤
- inference output 예시
- 시스템 통합용 JSON/테이블 스키마 예시

---

## 12. 성공 기준 (예시)
저희가 생각하는 성공 기준은 예를 들어 아래와 같습니다.

1. Stable Profile을 건드리지 않고 Action / Intent Overlay를 별도로 설계할 수 있다.
2. chat + log 중심으로 현재 intent / action / target을 구조적으로 추론할 수 있다.
3. 기존 GraphRapping에 큰 충돌 없이 integration 설계가 가능하다.
4. recommendation / exploration / explanation 품질 개선에 쓸 수 있다.
5. 실제 실험에서 rule-only baseline보다 일관되게 나아진다.

---

## 13. 연구실에 묻고 싶은 질문
저희는 아래를 특히 같이 논의하고 싶습니다.

1. 이 문제를 multi-head classification으로 보는 게 타당한지
2. target grounding을 pair classification / candidate-conditioned classification으로 푸는 게 적절한지
3. weak label을 어떤 구조로 설계하면 좋은지
4. review를 보조 입력으로만 쓰는 방향이 맞는지
5. 제품 추천 시스템에 실제로 쓸 수 있는 수준의 output schema는 무엇이 적절한지
6. 소형 한국어 PLM/encoder baseline으로 어느 정도까지 갈 수 있을지
7. 이 연구를 산학과제로 수행했을 때, 어느 수준의 산출물을 현실적으로 기대할 수 있을지

---

## 14. 저희가 전달 가능한 것
저희는 아래 자산을 제공할 수 있습니다.

- 상품 master 데이터
- 리뷰 추출 결과 자산 (NER / BEE / REL)
- 유저 stable profile 추출 결과
- mock / reference 데이터셋
- 현재 시스템 구조 문서
- GraphRapping 레포 및 integration 방향

즉, 연구실에서 **완전히 백지에서 시작하는 과제**가 아니라,
이미 존재하는 시스템 위에 **추가 모델 레이어를 올리는 과제**입니다.

---

## 15. 한 줄 요약
저희는
**“이미 있는 장기 개인화 프로파일과 리뷰 기반 제품 신호 위에,
유저의 현재 행동/의도를 읽는 동적 Overlay 모델을 추가하고 싶다”**
는 문제를 함께 풀고 싶습니다.

연구실에서 이 문제를
- 어떤 문제 정의로 잡을지
- 어떤 클래스/출력 구조가 적절할지
- 어떤 weak label / small model / teacher-student 구성이 현실적인지
- 기존 시스템에 어떻게 안전하게 통합할지
를 같이 설계해줄 수 있는지 논의하고 싶습니다.
