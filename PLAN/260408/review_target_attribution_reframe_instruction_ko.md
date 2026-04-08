# Review Target Attribution 재정립 지시서

## 목적

현재 리뷰 기반 KG/추천 파이프라인에서 가장 중요한 전제를 다시 고정한다.

핵심 전제:
- BEE는 먼저 `리뷰 타깃 제품에 대한 평가/감상인가`를 판정하는 대상이다.
- NER-BEE / ReviewTarget-BEE / Product-BEE relation은 의미 확장보다 **타깃 귀속(attribution)** 판정이 우선이다.
- relation이 연결되지 않은 BEE는 기본적으로 타깃 제품에 대한 언급이 아니라고 본다.
- 따라서 연결되지 않은 BEE에서 파생한 attr/keyword/concern/context signal을 상품 추천용 KG에 승격시키면 안 된다.

이 지시서는 기존의 “BEE로부터 concern을 파생 생성하자” 같은 방향을 수정한다.

---

## 왜 이 재정립이 필요한가

현재 프로젝트의 큰 방향은 evidence graph(per-review)와 serving graph(corpus-promoted)를 분리하는 것이다. 하지만 리뷰 처리 내부에서 BEE를 너무 빨리 의미 신호로 승격하면, 실제로는 타제품/비타깃 언급까지 현재 상품 신호로 들어갈 위험이 있다.

이 프로젝트에서 BEE relation 학습 목표는 **true/false attribution filter**의 성격이 강하다.
즉,
- `이 BEE phrase가 리뷰 타깃 제품에 대한 말인가?`
- `아니면 다른 제품/비교 제품/일반론/부수 언급인가?`
를 가르는 것이 우선이다.

따라서 연결되지 않은 BEE는 "아직 타깃 귀속이 확인되지 않은 평가 문구"로 봐야지,
"타깃 제품의 속성 signal"로 취급하면 안 된다.

---

## 방향성 재정의

### 기존에 피해야 할 방향
- BEE phrase가 있으면 일단 상품 signal로 올림
- relation이 없어도 BEEAttr / Keyword를 타깃 제품에 귀속
- BEE phrase만 보고 Concern/Context를 파생 생성

### 새 방향
- 먼저 attribution gate를 통과한 BEE만 target-linked BEE로 인정
- target-linked BEE만 Layer 2 canonical fact / Layer 2.5 wrapped signal로 승격
- unlinked BEE는 evidence/debug/analyzer 레이어에만 남김
- concern/context는 explicit relation 또는 explicit typed mention 중심으로만 생성
- BEE는 concern을 직접 생성하지 않고, 이미 생성된 concern signal의 설명/보강 evidence로만 쓸 수 있음

즉:

`리뷰 문구 -> (target attribution 판정) -> target-linked BEE만 signal 승격`

이 순서가 강제되어야 한다.

---

## 구조적 원칙

### 원칙 1. Relation의 1차 역할은 semantic이 아니라 attribution일 수 있다
특히 NER-BEE, ReviewTarget-BEE, Product-BEE relation은
- `has_attribute`
- `has_effect`
- `attribute_of`
같이 단순해 보여도,
실제로는 “이 BEE가 누구에 대한 말인가”를 확정하는 근거다.

따라서 이 relation들을 단순 semantic edge로만 보면 안 된다.

### 원칙 2. BEE signal 승격은 relation-gated 이어야 한다
다음 중 하나를 만족할 때만 target-linked로 본다.
- Review Target / target Product mention과 직접 relation 연결됨
- same_entity / placeholder resolution 후 target product cluster에 귀속됨
- 명시적 비교 구조에서 current target side로 판정됨

그 외는 target signal로 올리지 않는다.

### 원칙 3. Unlinked BEE는 폐기보다 evidence-only로 격하
연결 안 된 BEE를 무조건 삭제하면 나중에
- extractor recall 점검
- 리뷰 내부 비교 구조 분석
- dictionary growth
- 모델 개선
에 쓸 근거가 사라진다.

따라서 unlinked BEE는
- product serving signal에서는 제외
- raw evidence / quarantine / analyst debug에서만 유지
가 맞다.

### 원칙 4. BEE_ATTR와 KEYWORD는 둘 다 유지하되, 둘 다 target-linked일 때만 승격
BEE의 계층 구조는 다음이 맞다.
- 상위 축: BEE_ATTR
- 하위 표현: KEYWORD

하지만 둘 다 귀속 판정 이후에만 승격해야 한다.
즉 unlinked BEE에서 keyword만 뽑아 serving으로 올리는 것도 금지한다.

### 원칙 5. Concern/Context는 explicit 우선
Concern/Context는 우선 아래에서만 생성한다.
- explicit concern entity
- explicit DATE/context mention
- explicit semantic relation (`addresses`, `used_on`, `benefits`, `causes` 등)

BEE phrase에서 concern/context를 자동 파생하는 규칙은 MVP 범위에서 넣지 않는다.
필요하면 later-stage analyst tooling에서만 "가능성 후보"로 계산한다.

---

## 레이어별 역할 재정의

## Layer 1 Raw / Evidence
여기서는 원문과 추출 결과를 최대한 손실 없이 저장한다.

보존 대상:
- review raw text
- ner mentions
- bee phrases
- raw relations
- placeholder / same_entity 정보
- relation confidence / source_type

여기서는 BEE가 target-linked인지 아직 확정되지 않아도 저장한다.

## Layer 2 Canonical Fact
여기서는 **타깃 귀속이 확인된 사실만 canonical fact**로 올린다.

올릴 수 있는 것:
- Product HAS_ATTRIBUTE BEEAttr
- BEEAttr HAS_KEYWORD Keyword
- Product USED_ON TemporalContext
- Product ADDRESSES Concern
- Product USED_WITH Tool/Product

전제:
- 모두 attribution gate 통과 필요

주의:
- unlinked BEE는 canonical fact로 올리지 않음
- derived concern from BEE는 넣지 않음

## Layer 2.5 Wrapped Signal
여기서는 canonical fact 중 추천에 필요한 것만 projection한다.

규칙:
- target-linked BEEAttr/Keyword만 signal 생성
- unlinked BEE 기반 signal 금지
- context/concern도 explicit canonical fact 기반만 허용

## Layer 3 Aggregate / Serving
여기서는 코퍼스 승격을 거친 signal만 serving profile에 넣는다.

즉 serving에서는
- target-linked
- corpus-promoted
두 조건을 모두 만족해야 한다.

---

## 코드/구현 방향 지시

## A. attribution gate를 명시적 개념으로 승격
현재 코드에서 relation이 semantic edge처럼만 보이는 부분을 바꿔야 한다.

필요한 개념:
- `target_linked: bool`
- `attribution_source: direct_rel | placeholder_resolved | same_entity_resolved | comparison_resolved`
- `attribution_confidence`

이 값은 최소 BEE mention 또는 canonical fact 생성 전에 결정되어야 한다.

### 기대 효과
- BEE signal 생성 전 filter 가능
- debug/explanation에서 "왜 이 BEE를 target에 귀속했는지" 설명 가능

## B. unlinked BEE 전용 보관 경로 만들기
연결되지 않은 BEE는 serving에서 제외하되, 아래 용도로 남긴다.
- analyzer/debug UI
- dictionary growth
- 추후 model retraining set
- false negative audit

즉 `quarantine_unlinked_bee` 또는 `bee_unlinked_evidence` 같은 별도 저장/분류 경로가 필요하다.

## C. BEE_ATTR/KEYWORD 승격 조건 통일
다음 규칙을 명시적으로 적용한다.
- target-linked BEE -> BEE_ATTR signal 생성 가능
- target-linked BEE && keyword normalized -> KEYWORD signal 생성 가능
- unlinked BEE -> 둘 다 생성 금지

## D. concern/context 자동 파생 금지
이건 이번 수정에서 명확히 막아야 한다.

금지:
- `안 건조해요` 같은 phrase 하나만으로 `addresses(product, dryness)` 생성
- `촉촉해요`만으로 `Concern(dryness)` positive signal 생성

허용:
- explicit concern entity가 relation으로 연결된 경우
- explicit DATE/context mention이 relation으로 연결된 경우
- explicit semantic predicate가 존재하는 경우

## E. review KG viewer 역할 재정의
프론트에서 보는 리뷰 그래프는 serving graph가 아니라 evidence graph임을 명확히 한다.

즉 그래프 뷰어는
- 어떤 BEE가 target-linked였는지
- 어떤 BEE가 unlinked였는지
- 어떤 relation이 attribution 근거였는지
를 보여주는 쪽이 더 맞다.

---

## 구체 작업 묶음

## 작업 1. BEE attribution 상태 모델 추가
### 목적
BEE phrase를 target-linked / unlinked로 명시적으로 관리하기 위해

### 방향
- mention/entity/fact 어디에 둘지 결정하되, 최소 bundle이나 intermediate artifact에는 남겨라
- relation 기반 귀속 판정 결과를 후속 레이어에서 재사용 가능하게 하라

### 구현 포인트
- `mention_extractor` / `placeholder_resolver` / `run_daily_pipeline` 사이에서 attribution result를 전달
- debug용으로 attribution source를 남김

### acceptance
- BEE phrase마다 `target_linked` 여부가 판정됨
- 같은 리뷰 내 타제품 언급 BEE는 unlinked로 남음
- target-linked 비율을 통계로 볼 수 있음

## 작업 2. signal emission gate 수정
### 목적
BEE signal이 relation-gated 되도록

### 방향
- BEE_ATTR / KEYWORD signal 생성 전에 target-linked 판정 확인
- unlinked BEE는 signal emission 차단

### acceptance
- unlinked BEE에서는 `HAS_BEE_ATTR_SIGNAL`, `HAS_BEE_KEYWORD_SIGNAL`이 생성되지 않음
- linked BEE에서는 둘 다 정상 생성됨

## 작업 3. unlinked BEE evidence 보관/QA 경로
### 목적
삭제 대신 분석 가능 상태 유지

### 방향
- 별도 quarantine/evidence 저장 경로 마련
- 향후 dictionary growth와 model 개선용으로 활용

### acceptance
- unlinked BEE 건수가 저장됨
- review_id / phrase / bee_attr_raw / relation context가 남음
- serving 신호와는 분리됨

## 작업 4. concern/context 생성 규칙 축소
### 목적
잘못된 semantic over-generation 방지

### 방향
- explicit relation / explicit typed mention 중심으로만 concern/context 생성
- BEE-only derived concern/context 규칙 제거 또는 비활성화

### acceptance
- concern/context signal은 explicit evidence 기반만 생성됨
- BEE phrase 단독으로 concern signal이 생기지 않음

## 작업 5. texture를 포함한 BEE hierarchy 유지
### 목적
BEE_ATTR와 KEYWORD의 계층 구조를 살리되, attribution gate를 적용하기 위해

### 방향
- `Texture`는 상위 BEE_ATTR 축
- `GelLike`, `LightLotionLike` 등은 하위 KEYWORD
- 하지만 둘 다 target-linked일 때만 승격

### acceptance
- linked texture phrase → attr + keyword 둘 다 생성
- unlinked texture phrase → 둘 다 serving에서 제외

---

## Claude Code에게 줄 구현 가드레일

아래는 이번 수정에서 반드시 지킬 원칙이다.

1. 리뷰 원문에 BEE가 있다고 해서 자동으로 상품 signal로 승격하지 말 것
2. relation이 없는 BEE를 concern/context로 파생 생성하지 말 것
3. BEE relation은 semantic보다 attribution 역할이 우선일 수 있음을 전제로 설계할 것
4. unlinked BEE를 삭제하지 말고 evidence/debug용으로 보관할 것
5. BEE_ATTR와 KEYWORD는 계층 구조를 유지할 것
6. serving graph에는 target-linked + promoted signal만 올릴 것

---

## 테스트/검증 시나리오

### 시나리오 1. target-linked BEE
리뷰:
- 타깃 제품 A에 대해 "촉촉하고 흡수가 빨라요"
- Product A ↔ BEE relation 존재

기대:
- BEE target-linked = true
- BEE_ATTR signal 생성
- KEYWORD signal 생성

### 시나리오 2. 타제품 언급 BEE
리뷰:
- 타깃 제품 A 리뷰인데, 비교로 제품 B의 발림성 언급
- BEE는 제품 B와 relation 연결

기대:
- 제품 A 기준 BEE target-linked = false
- 제품 A signal로 승격되지 않음
- evidence-only 보관

### 시나리오 3. relation 없는 BEE
리뷰:
- BEE phrase 존재하지만 target/product relation 없음

기대:
- unlinked 처리
- attr/keyword signal 생성 안 됨
- quarantine/evidence 저장만 됨

### 시나리오 4. explicit concern relation
리뷰:
- Product A addresses dryness

기대:
- concern signal 생성
- explicit evidence 기반이므로 serving 가능

### 시나리오 5. texture hierarchy
리뷰:
- Product A에 대해 "젤 타입이라 가볍다"
- target relation 존재

기대:
- BEE_ATTR(Texture) signal 생성
- KEYWORD(GelLike) signal 생성
- explanation에서 상위 축+하위 표현 함께 노출 가능

---

## 최종 메시지

이번 수정의 핵심은 "BEE를 더 많이 파생해서 신호를 늘리는 것"이 아니다.
핵심은

- **무엇이 정말 리뷰 타깃 제품에 대한 말인지 더 정확히 가르고**
- **그렇게 귀속이 확인된 BEE만 구조 신호로 승격**하며
- **타제품/비타깃 언급은 evidence-only로 남기는 것**

이다.

즉 이번 단계는 recall을 무작정 늘리는 단계가 아니라,
**타깃 귀속 정확도를 높여 전역 KG와 추천 신호의 정밀도를 올리는 단계**다.
