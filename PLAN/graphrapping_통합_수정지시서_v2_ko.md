# GraphRapping — 통합 수정/업데이트 작업 지시서 (Merged Work Order)

작성 목적: 아래 3개 문서를 **중복 제거 + 상충 해소 + 계약 통일**하여, Claude Code에 바로 전달 가능한 **단일 실행 문서**로 통합한다.

통합 대상:
1. 전체 프로젝트 오류 포인트 수정 지시사항 파일
2. `review_kg_집중_수정_지시서.md`
3. 추가 지시: **리뷰 의미 손실 복구 + 유저 레이어 강화 + 증분 파이프라인 안정화**

본 문서는 원래 의도를 최대한 보존하되, **중복 문장을 접고**, **상충되는 규칙은 하나의 최종 계약으로 통일**한 버전이다.

---

## 0. 통합 시 상충/중복 해소 규칙

### 0-1. 아키텍처 표현 통일
- 전역 아키텍처는 기존 **5-Layer + Common Concept Layer + QA Sidecar**를 유지한다.
- 단, 리뷰 파이프라인에 한해 아래 3개 “그래프 관점”을 추가로 구분한다.
  - **Evidence Graph**: 리뷰 단위 mention/phrase/debug graph
  - **Corpus KG**: 전체 리뷰 코퍼스에서 support/weight 기반으로 승격된 전역 KG
  - **Serving Graph / Recommendation Projection**: 추천/설명용 projection
- 즉, `Evidence Graph -> Corpus KG -> Serving Graph`는 **리뷰 파이프라인 내부 관점**이고, 5-Layer 아키텍처를 대체하지 않는다.

### 0-2. Provenance 정본 위치 통일
- **정본 provenance 연결은 `signal_evidence`**로 통일한다.
- `wrapped_signal.source_fact_ids`는 남기더라도 **캐시/편의 컬럼**으로만 유지한다.
- explainer / provenance query는 항상 `wrapped_signal -> signal_evidence -> canonical_fact -> fact_provenance -> raw` 체인을 정본으로 사용한다.

### 0-3. `recommended_to` 계약 통일
- 최종 채택 규칙은 **권장안 A**다.
- object가 이미 `UserSegment`로 정규화된 경우:
  - `qualifier_required = N`
  - `dst_type = UserSegment`
  - `RECOMMENDED_TO_SEGMENT_SIGNAL` 직접 생성
- qualifier는 추가 맥락이 있을 때만 optional로 사용한다.
- 즉, “object direct 승격”과 “segment qualifier 필수”를 동시에 요구하지 않는다.

### 0-4. BEE-only synthetic / auto keyword 처리 통일
- **BEE-only synthetic relation은 evidence-only**로 격하한다.
- **auto-generated keyword는 canonical keyword / serving join key로 승격 금지**한다.
- synthetic/auto-generated 요소는 Layer 2에서 바로 전역 KG edge가 아니라, **promotion candidate 또는 quarantine**로만 간다.

### 0-5. Catalog validation 사용 범위 통일
- `CATALOG_VALIDATION_SIGNAL`은
  - candidate generation: 제외
  - scoring: 제외
  - standard explanation: 제외
  - QA/debug/analyst path: 허용
- product master truth를 절대 overwrite하지 않는다.

### 0-6. 리뷰 의미 보존 계약 통일
- `polarity`, `negated`, `intensity`, `qualifier`는 Layer 2 또는 Layer 2.5까지 반드시 보존한다.
- 같은 `dst_id`라도 의미가 다르면 merge 금지다.
- `signal_id` / dedup key에는 최소 아래를 반영한다.
  - `review_id`
  - `target_product_id`
  - `edge_type`
  - `dst_id`
  - `polarity`
  - `negated`
  - `qualifier_fingerprint`
  - `registry_version`

### 0-7. 증분 처리 계약 통일
- 증분 재처리 시 `review_raw`만 읽고 `process_review()`를 호출하면 안 된다.
- 반드시 `(review_id, review_version)` 기준으로 `ner_raw`, `bee_raw`, `rel_raw` child rows를 다시 로드한 **완전한 raw snapshot**으로 재연산한다.

---

## 1. 작업 전 공통 원칙

1. Layer 2의 canonical fact semantics는 깨지면 안 된다.
2. Layer 2의 relation 65개는 절대 잃어버리면 안 된다.
3. Layer 3 signal은 projection registry 외 경로로 생성하면 안 된다.
4. Product master truth는 review-derived signal이 절대 overwrite 하면 안 된다.
5. reviewer proxy와 real user는 어떤 테이블/조인 경로로도 merge하면 안 된다.
6. signal/fact/evidence provenance는 항상 역추적 가능해야 한다.
7. 모든 변경은 **idempotent upsert**, **late-arrival**, **tombstone**까지 고려해야 한다.
8. 새 기능 추가보다 **기존 계약을 더 엄격히 구현**하는 것이 우선이다.
9. 리뷰 단위 그래프를 그대로 전역 KG로 union하지 않는다.
10. synthetic/auto-generated evidence는 support/weight threshold 전까지 전역 KG edge로 승격하지 않는다.

---

## 2. 범위

이번 수정은 **기존 아키텍처를 유지한 상태에서**, 아래 세 축을 강화하는 작업이다.

1. **리뷰 의미 손실 복구**
2. **유저 레이어 강화**
3. **증분 파이프라인 안정화**

대규모 재설계는 하지 않는다.
현재 구조인

- Layer 0: Product/User Master
- Layer 1: Raw / Evidence
- Layer 2: Canonical Fact
- Layer 2.5: Wrapped Signal
- Layer 3: Aggregate / Serving
- Layer 4: Recommendation / Explanation

을 유지한다.

---

## 3. 최종 목표

1. 리뷰 1건에서 추출된 의미가 **Layer 2 fact와 Layer 2.5 signal에 손실 적게 반영**된다.
2. 유저 데이터가 단순 preference edge 묶음이 아니라 **상태/맥락/금기/목표를 가진 serving profile**로 올라온다.
3. 증분 재처리 시 raw snapshot 기준으로 **정확히 재연산**된다.
4. explanation이 실제로 **signal -> fact -> raw evidence**를 따라가며 의미를 잃지 않는다.
5. 추천 점수가 **하위 레이어의 풍부한 정보**를 더 많이 먹게 된다.
6. 프론트의 product graph viewer는 evidence graph와 corpus graph를 명확히 구분해서 제공할 수 있다.
7. 전체 리뷰 코퍼스에서 반복/지지/상충을 집계한 **전역 Corpus KG**가 실제로 생성된다.

---

## 4. 리뷰 KG 목표 재정의

현재 `src/kg`의 역할은 “리뷰 1건을 그래프 모양으로 정리하는 evidence graph 생성기”에 더 가깝다. 내가 원하는 것은 그 자체가 아니라, **전체 리뷰 코퍼스 기반 전역 KG**다.

따라서 리뷰 파이프라인은 다음 구조를 따른다.

### A. Evidence Graph (리뷰 단위)
- 목적: 디버깅, provenance, analyst viewer
- 단위: review-local mention / phrase / placeholder / raw relation
- synthetic edge 허용 가능
- auto keyword 허용 가능
- 전역 join key로 직접 사용 금지

### B. Corpus KG (전역 리뷰 KG)
- 목적: 전체 리뷰 코퍼스에서 반복/지지/충돌을 집계한 전역 concept graph
- 단위: concept-level fact / promoted signal
- 예:
  - Product -> BEEAttr
  - Product -> Keyword
  - Product -> Context
  - Product -> Concern_POS / Concern_NEG
  - Product -> ComparisonProduct
  - Product -> CoUsedProduct
  - Product -> Segment
- support_count / distinct_review_count / confidence / recency / weight 포함
- 이 레이어가 진짜 활용 대상 KG다.

### C. Serving Graph / Recommendation Projection
- 목적: 추천/개인화/설명
- Corpus KG에서 목적별로 projection한 레이어
- hard filter / candidate / score / explain에 맞게 feature화

현재 프로젝트는 A와 C는 일부 있지만, **B(Corpus KG)**가 약하다. 이번 수정의 핵심은 **A를 바로 C로 쓰지 말고 A -> B -> C 흐름으로 바꾸는 것**이다.

---

## 5. 리뷰 의미 손실 복구 (A 트랙)

### 5-1. 핵심 문제 정의
현재 구현은 BEE phrase를 `BEE_ATTR + KEYWORD`로 정규화하는 데는 성공했지만, 다음 정보가 Layer 2/2.5에서 충분히 살아 있지 않다.

- negation
- intensity
- 복합 polarity
- qualifier 기반 의미
- 동일 review 내 다중 evidence의 의미 차이
- synthetic/generated evidence의 승격 여부

즉 지금은 “리뷰 표현을 개념으로 옮기는 것”은 되지만, **표현의 세기/부정/맥락 차이**가 너무 빨리 평평해진다.

### 5-2. 수정 대상 파일
- `src/kg/mention_extractor.py`
- `src/kg/canonicalizer.py`
- `src/kg/adapter.py`
- `src/normalize/bee_normalizer.py`
- `src/canonical/canonical_fact_builder.py`
- `src/wrap/projection_registry.py`
- `configs/projection_registry.csv`
- `src/wrap/signal_emitter.py`
- `src/wrap/relation_projection.py`
- `src/common/ids.py`
- `sql/ddl_canonical.sql`
- `sql/ddl_signal.sql`
- `tests/test_bee_normalizer.py`
- `tests/test_signal_emitter.py`
- `tests/test_idempotency.py`
- `tests/test_provenance_fidelity.py`

### 5-3. `src/kg/mention_extractor.py`

#### 문제
- BEE-only mention에 대해 `Review Target -> has_attribute -> BEE_ATTR` synthetic relation을 자동 생성
- phrase 기반 auto keyword 생성
- NER-BeE를 무조건 `has_attribute`로 flatten

#### 수정 지시
1. **BEE-only synthetic relation 생성은 유지하되, evidence-only로 격하**
   - `relation_type="has_attribute"`는 유지 가능
   - 대신 새 필드 추가:
     - `is_synthetic=True`
     - `evidence_kind="BEE_SYNTHETIC"`
     - `promotion_eligible=False`
   - 이 relation은 canonical fact로 바로 승격하지 않음

2. **auto keyword 생성 금지 또는 quarantine**
   - 현재 `normalize_text(phrase)[:30]` 형태의 auto keyword 생성 로직 제거
   - 대체:
     - `obj_keywords`가 없으면 `unknown_keyword_queue`에 보냄
     - 또는 `KeywordCandidate`로만 저장하고 canonical keyword 승격 금지

3. **NER-BeE flatten 제거**
   - `NER-BeE`면 `has_attribute`로 강제하지 말고
   - `anchor_relation=True`, `anchor_target=subj_mention_id`, `anchor_opinion=obj_mention_id` 형태로 evidence metadata만 기록
   - canonical 단계에서 이 anchor 정보를 사용해 Product ↔ BEEAttr/Keyword fact를 만들 것

4. **mention-level confidence/source 명확화**
   - relation/source_type 외에 mention에도
     - `mention_source`
     - `mention_confidence`
     - `is_generated`
     를 저장

#### Acceptance Criteria
- BEE-only mention이 있어도 바로 전역 edge로 승격되지 않는다.
- keyword 없는 phrase는 auto keyword node가 아니라 quarantine/후보 큐로 간다.
- NER-BeE relation은 anchor evidence로 남고, flattened semantic relation이 아니다.

### 5-4. `src/kg/canonicalizer.py`

#### 문제
- BEE_ATTR를 sentiment-split entity로 canonicalize (`밀착력_POS`, `밀착력_NEG`) 하는 흔적이 남아 있다.
- KEYWORD entity 생성도 surface 중심이다.

#### 수정 지시
1. **BEE_ATTR sentiment split 제거**
   - canonical BEEAttr entity는 항상 하나만:
     - `concept:BEEAttr:adhesion`
   - polarity는 entity가 아니라 edge/fact 속성으로 이동

2. **KEYWORD entity는 canonical keyword만 생성**
   - `normalize_text(surface)`로 바로 KEYWORD entity 생성 금지
   - 반드시 `keyword_normalizer`를 거친 canonical keyword id만 entity 생성 허용
   - unknown surface는 quarantine

3. **placeholder 기반 entity는 review-local scope 명시**
   - reviewer/review_target placeholder entity는 절대 전역 concept/entity처럼 보이지 않게
   - 명시적 namespace:
     - `evidence:review_target:{review_id}`
     - `evidence:reviewer_proxy:{review_id}`

4. **canonicalizer output은 Evidence KG 전용임을 명시**
   - 이 파일 산출물은 global KG 정본이 아니라 `KGResult`용 evidence graph라고 주석/문서화

#### Acceptance Criteria
- 동일 BEEAttr가 polarity별로 다른 entity로 찢어지지 않는다.
- KEYWORD entity는 canonical dictionary 통과 keyword만 생성된다.
- placeholder entity가 전역 entity처럼 취급되지 않는다.

### 5-5. `src/kg/adapter.py`

#### 문제
- Evidence KG를 GraphRapping canonical fact로 옮길 때, upstream graphification에서 생긴 synthetic/flatten/auto-generated 흔적을 충분히 제어하지 못한다.
- negation/intensity 같은 opinion metadata도 충분히 carry하지 못한다.

#### 수정 지시
1. **adapter를 단순 shape converter가 아니라 promotion gate로 바꿔라**
   - input edge/entity마다 아래를 판정:
     - `PROMOTE`
     - `KEEP_EVIDENCE_ONLY`
     - `DROP`
     - `QUARANTINE`
   - 예:
     - synthetic has_attribute -> `KEEP_EVIDENCE_ONLY`
     - canonical keyword 없는 auto keyword -> `QUARANTINE`

2. **BEE opinion metadata carry**
   - adapter input/출력 모델에
     - `polarity`
     - `negated`
     - `intensity`
     - `evidence_kind`
     를 추가
   - downstream canonical fact builder가 그대로 받을 수 있게 한다

3. **Evidence fact vs Canonical fact 분리**
   - 가능하면 두 종류로 구분:
     - `EvidenceFact` (리뷰 내부 구조)
     - `CanonicalFactInput` (전역 KG 승격 후보)
   - adapter는 둘을 모두 만들 수 있어야 한다

4. **source_type 별 base confidence 부여**
   - 예시:
     - raw REL = 1.0
     - explicit NER-BeE anchor = 0.8
     - BEE-synthetic = 0.4
     - auto-generated keyword candidate = 0.1

#### Acceptance Criteria
- adapter가 synthetic/auto-generated 요소를 별도 처리 경로로 분기한다.
- negation/intensity가 adapter 출력에 남는다.
- 전역 KG 승격 후보와 evidence 전용 fact가 구분된다.

### 5-6. `src/normalize/bee_normalizer.py`

#### 문제
- 이 모듈 자체는 좋아졌지만, downstream이 충분히 못 쓰고 있다.
- 또 auto keyword를 downstream이 쉽게 믿어버릴 수 있다.

#### 수정 지시
1. **출력 명세를 stronger contract로 고정**

```python
@dataclass
class NormalizedBee:
    bee_attr_id: str
    keyword_ids: list[str]
    keyword_source: str | None        # DICT | RULE | CANDIDATE
    polarity: str | None              # POS | NEG | NEU | MIXED
    negated: bool | None
    intensity: float | None           # 0.0 ~ 1.0
    confidence: float | None
    evidence_text: str
    evidence_span: tuple[int, int] | None
    derived_qualifiers: list[dict]
```

2. **keyword_source가 CANDIDATE면 전역 승격 금지**
   - serving join 금지
   - quarantine 또는 dictionary growth loop로만 이동

3. **double negation / contrast phrase 강화**
   - `안 건조한 건 아닌데` 같은 복합 negation rule 보강
   - `처음엔 촉촉한데 오후엔 건조`처럼 polarity split phrase 지원

4. **negation을 keyword surface에만 흡수하지 마라**
   - `negated`는 반드시 별도 필드 유지

#### Acceptance Criteria
- downstream이 canonical keyword와 candidate keyword를 구분 가능하다.
- negation/intensity가 contract로 강제된다.
- 복합 phrase가 단일 polarity로 과도 단순화되지 않는다.

### 5-7. `src/jobs/run_daily_pipeline.py`

#### 문제
- legacy BEE 경로에서 `bee_normalizer`가 뽑은 `negated`, `intensity`를 `builder.add_bee_facts()`에 전달하지 않는다.
- `kg_mode` legacy/shadow/on이 섞여 있어 코어 파이프라인과 실험 경로가 혼재한다.
- `recommended_to` / segment derivation 로직도 qualifier 계약과 어긋날 수 있다.
- review KG output을 바로 serving signal처럼 쓰기 쉽다.

#### 수정 지시
1. **BEE metadata 전부 전달**
   - `builder.add_bee_facts()` 호출 시 추가:
     - `negated=bee_result.negated`
     - `intensity=bee_result.intensity`
     - 필요시 `raw_phrase`, `surface_forms`, `keyword_source`

2. **`kg_mode` 분리**
   - `process_review()`는 canonical pipeline 정본 경로만 유지
   - `kg_mode="shadow"`는 별도 wrapper / entrypoint로 분리
   - `src/kg`는 evidence/debug 전용으로 명확히 분리

3. **`recommended_to` 처리 규칙 정리**
   - object가 이미 `UserSegment`면 qualifier_required 불필요
   - 또는 실제 `FactQualifier(segment=...)`를 생성
   - 둘 중 하나만 선택하고 일관되게 유지
   - 최종 채택안은 **object가 이미 UserSegment면 qualifier 불필요**

4. **review KG output을 바로 signal로 쓰지 말고 corpus aggregation 전 단계로 보낼 것**
   - review 1건 결과는 바로 serving signal이 아니라
   - 최소한 `CorpusEdgeCandidate` 또는 `wrapped_signal_raw`로 취급

#### Acceptance Criteria
- BEE negated/intensity가 canonical fact까지 전달된다.
- `kg_mode` 분기 없이 코어 경로 하나가 명확하다.
- `recommended_to`가 qualifier mismatch로 quarantine 되지 않는다.

### 5-8. `src/canonical/canonical_fact_builder.py`

#### 문제
- `add_bee_facts()`가 polarity만 받고 negated/intensity를 받지 않는다.
- canonical fact가 리뷰 의미를 충분히 보존하지 못한다.
- object가 concept인지 entity인지 더 명확히 다뤄야 한다.

#### 수정 지시
1. **`add_bee_facts()` 시그니처 확장**
   - 추가 인자:
     - `negated: bool | None = None`
     - `intensity: float | None = None`
     - `evidence_kind: str | None = None`
     - `base_confidence: float | None = None`

2. **BEE-derived fact를 두 층으로 생성**
   - `Product -> BEEAttr`
   - `BEEAttr -> Keyword`
   - 둘 다 `polarity`, `negated`, `intensity`, `source_modalities` 유지
   - synthetic source면 qualifier 또는 attrs에 명시

3. **qualifier 적극 활용**
   - `fact_qualifier`에 다음 저장:
     - `negated`
     - `intensity`
     - `context`
     - `frequency`
     - `duration`
     - `segment`
     - `tool`
     - `reason`
   - BEE 의미가 fact body에서 사라지지 않게 함

4. **promotion confidence 추가**
   - `confidence = extraction_confidence * source_kind_weight`
   - synthetic / auto / inferred는 낮은 confidence를 갖게 함

5. **canonical fact builder 전용 모듈 책임 명시**
   - resolved mention -> `canonical_entity` upsert
   - normalized triple/value -> `canonical_fact` upsert
   - raw row link -> `fact_provenance` insert
   - time/context/segment/tool 등 -> `fact_qualifier` insert

6. **범용 provenance 확장 고려**
   - `fact_provenance`에 필요 시 다음 추가:
     - `source_domain` (`review|user|product|manual|system`)
     - `source_kind` (`raw|summary|master|derived`)
     - `source_table`
     - `source_row_id`

#### Acceptance Criteria
- BEE-derived canonical fact가 polarity뿐 아니라 negation/intensity까지 보존한다.
- synthetic/inferred fact가 confidence로 구분된다.
- qualifier를 통해 맥락 정보가 fact에 남는다.

### 5-9. `src/wrap/projection_registry.py` + `configs/projection_registry.csv`

#### 문제
- 현재 registry는 어떤 canonical fact를 serving signal로 내릴지는 정의하지만, synthetic/generated evidence의 승격 기준까지는 충분히 명시하지 않는다.
- `recommended_to` 계약과 qualifier 규칙이 object direct 승격 방식과 충돌할 수 있었다.

#### 수정 지시
1. **registry에 source/evidence gate 추가**
   - 새 컬럼 권장:
     - `allowed_evidence_kind`
     - `min_confidence`
     - `min_support_for_promotion`
     - `promotion_mode` (`IMMEDIATE|CORPUS_THRESHOLD|NEVER`)

2. **Corpus KG 승격 규칙 추가**
   - 예:
     - `BEE-synthetic` -> `CORPUS_THRESHOLD`
     - raw REL explicit comparison -> `IMMEDIATE`
     - auto keyword -> `NEVER`

3. **USED_WITH_PRODUCT 명시 강화**
   - `used_with, Product, Product`는 반드시
     - `COUSED_PRODUCT`
     - `USED_WITH_PRODUCT_SIGNAL`
     로 살린다

4. **catalog validation은 serving 제외를 더 강하게 명시**
   - candidate generation / scoring / ranking 제외
   - standard explanation 제외
   - QA/debug only

5. **`recommended_to` 최종 규칙 반영**
   - object가 이미 `UserSegment`면 `qualifier_required = N`
   - qualifier는 optional 보조 정보로만 사용

6. **1 input 조합 -> 1 deterministic action 유지**
   - 매핑 불가 조합은 명시적으로 `DROP / QUARANTINE / KEEP_CANONICAL_ONLY`

#### Acceptance Criteria
- synthetic/generated fact가 registry만으로 승격 여부가 결정된다.
- `used_with`의 Product case가 명확히 살아 있다.
- catalog validation이 추천 feature로 유입되지 않는다.
- `recommended_to`가 object direct 승격 방식과 충돌하지 않는다.

### 5-10. `src/wrap/signal_emitter.py`

#### 문제
- signal ID / merge 정책이 전역 KG용으로는 아직 너무 납작할 수 있다.
- reverse transform에서 `dst_ref_kind="ENTITY"` 하드코딩 문제가 있다.
- signal이 너무 빨리 flatten될 수 있다.
- provenance 정본 위치가 중복될 수 있다.

#### 수정 지시
1. **signal dedup key 강화**
   - dedup key / `signal_id`에 반영:
     - `review_id`
     - `target_product_id`
     - `edge_type`
     - `dst_id`
     - `polarity`
     - `negated`
     - `qualifier_fingerprint`
     - `registry_version`

2. **signal merge 정책 명시적 구현**

```python
def merge_signal_rows(existing: WrappedSignal, incoming: WrappedSignal) -> WrappedSignal:
    ...
```

   - `weight = max(existing.weight, incoming.weight)`
   - `intensity = max(existing.intensity, incoming.intensity)` 또는 configured weighted average
   - `negated`가 다르면 merge 금지
   - `polarity`가 다르면 merge 금지
   - provenance는 `signal_evidence`에 top-k 유지
   - `source_modalities`는 union

3. **reverse transform ref kind 수정**
   - `dst_id = fact.subject_iri`
   - `dst_ref_kind = fact.subject_ref_kind`
   - reverse mapping으로 concept가 나오는 경우 `ENTITY`로 잘못 찍히지 않게 함

4. **`signal_evidence`를 provenance 정본으로 고정**
   - `source_fact_ids`는 캐시 용도로만 쓰거나 제거
   - explanation path는 무조건 `signal_evidence` 기준

5. **corpus aggregation 전용 raw signal 분리**
   - `wrapped_signal`을 바로 serving signal처럼 쓰지 말고
   - `signal_status = RAW|PROMOTED|REJECTED`
   - 또는 `corpus_signal_candidate` intermediate 도입

#### Acceptance Criteria
- 다른 polarity/negation/qualifier 의미가 한 signal로 잘못 합쳐지지 않는다.
- reverse transform에서 concept/entity ref kind가 정확하다.
- provenance 정본이 `signal_evidence`로 일관된다.

### 5-11. `src/common/ids.py`

#### 문제
- 현재 signal key는 polarity / negation / qualifier 차이를 충분히 반영하지 않을 수 있다.

#### 수정 지시
`make_signal_id()`를 다음 계약으로 강화한다.

```python
signal_id = md5(
    f"{review_id}|{target_product_id}|{edge_type}|{dst_id}|"
    f"{polarity}|{negated}|{qualifier_fingerprint}|{registry_version}"
)
```

#### Acceptance Criteria
- 같은 review에서 같은 dst_id라도 polarity/negation/qualifier가 다르면 별도 signal이 되거나 merge 정책상 명확히 구분된다.
- 재처리 시 중복 signal이 생기지 않는다.

---

## 6. 유저 레이어 강화 (B 트랙)

### 6-1. 핵심 문제 정의
현재 user 쪽은 canonical fact까지는 가지만, 실제 추천에 먹는 구조는 상대적으로 얇다.

- `skin_type`, `skin_tone`, `seasonal`, `purchase_summary`, `chat_summary`
- context 선호
- concern/goal 강도
- 금기/회피 신호
- 장단기 preference drift

이런 축이 저장은 되어도 추천과 설명에 충분히 녹아들지 않는다.

### 6-2. 수정 대상 파일
- `src/user/canonicalize_user_facts.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/mart/aggregate_user_preferences.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/ingest/purchase_ingest.py`
- `tests/test_recommendation.py`
- `tests/test_concept_link_integrity.py`
- `tests/test_reviewer_isolation.py`
- `tests/test_purchase_signal_usage.py` (신규)
- `tests/test_goal_fit_feature.py` (신규)

### 6-3. user canonical fact를 더 세분화
`canonicalize_user_facts.py`에 fact family별 builder를 분리한다.

#### user fact family
- `HAS_STATE`
  - skin_type
  - skin_tone
  - age_band
- `HAS_CONCERN`
- `WANTS_GOAL`
- `PREFERS_CONTEXT`
- `PREFERS_BEE_ATTR`
- `AVOIDS_BEE_ATTR`
- `PREFERS_KEYWORD`
- `AVOIDS_KEYWORD`
- `PREFERS_INGREDIENT`
- `AVOIDS_INGREDIENT`
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `REPURCHASES_PRODUCT_OR_FAMILY`
- `RECENTLY_PURCHASED`
- `SEASONAL_PREFERS_*`

권장 함수:
- `build_state_facts(...)`
- `build_concern_facts(...)`
- `build_goal_facts(...)`
- `build_context_facts(...)`
- `build_behavior_facts(...)`

### 6-4. purchase 이벤트를 실제 추천 feature로 반영
최소 추가 feature:
- `owned_product_ids`
- `owned_family_ids`
- `recently_purchased_brand_ids`
- `repurchased_brand_ids`
- `repurchased_category_ids`

`candidate_generator`에 추가:
- 이미 보유 제품 suppress
- 최근 구매 브랜드 boost/cooldown
- repurchase family affinity

`scorer`에 추가:
- `purchase_loyalty_score`
- `novelty_penalty` 또는 `novelty_bonus`
- `brand_confidence_weighted`에 purchase history 반영

### 6-5. `skin_type`, `skin_tone`를 실제 feature로 사용
둘 중 하나 이상 구현:

#### 방법 A
candidate filtering:
- 특정 제품/속성이 특정 skin_type에만 명백히 어긋나면 penalty

#### 방법 B
scoring feature:
- `skin_type_fit`
- `skin_tone_fit`

예:
- 건성 + `ADDRESSES_CONCERN(dryness)` -> boost
- 지성 + `MAY_CAUSE_CONCERN(heavy/oily)` -> penalty
- tone/shade match 가능할 때 variant layer에서 boost

### 6-6. 유저의 context 선호를 더 직접적으로 반영
`aggregate_user_preferences.py`가 `preferred_context_ids`를 만들 때
- source별 confidence
- recency
- frequency
를 반영하게 한다.

예:
- signup-derived context weight < purchase-derived context weight < chat explicit context weight

### 6-7. Goal과 main_benefit 연결을 더 직접화
`goal_fit`을 2개로 나눈다.
- `goal_fit_master`
- `goal_fit_review_signal`

즉,
- product master benefit에 goal concept가 있으면 `goal_fit_master`
- review-derived `ADDRESSES_CONCERN` / effect signal이 goal과 맞으면 `goal_fit_review_signal`

### 6-8. user serving profile을 정규 contract로 고정
`serving_user_profile`은 아래 필드를 명시적으로 가져야 한다.

#### truth-like
- `skin_type`
- `skin_tone`
- `age_band`
- `demographic_flags`

#### preferences
- `preferred_brand_ids`
- `preferred_category_ids`
- `preferred_ingredient_ids`
- `avoided_ingredient_ids`
- `preferred_context_ids`
- `preferred_bee_attr_ids`
- `preferred_keyword_ids`
- `concern_ids`
- `goal_ids`

#### behavior
- `recent_purchase_brand_ids`
- `repurchase_brand_ids`
- `owned_product_ids`
- `owned_family_ids`

### 6-9. Acceptance Criteria
- user profile이 `state + preference + concern + goal + behavior`를 모두 포함한다.
- purchase-derived signal이 실제 candidate/scoring에 반영된다.
- `skin_type`/`skin_tone`가 최소 한 가지 추천 feature로 실제 사용된다.
- `main_benefits`와 `goal_ids`가 실제 추천 feature로 연결된다.

---

## 7. 증분 파이프라인 안정화 (C 트랙)

### 7-1. 핵심 문제 정의
현재 증분 재처리 경로는 raw child rows(`ner_raw`, `bee_raw`, `rel_raw`)를 확실하게 재구성하지 못할 가능성이 있다. 이것은 correctness P0다.

### 7-2. 수정 대상 파일
- `src/jobs/run_incremental_pipeline.py`
- `src/jobs/run_daily_pipeline.py`
- `src/db/repos/review_repo.py`
- `src/db/repos/canonical_repo.py`
- `src/db/repos/signal_repo.py`
- `src/db/repos/mart_repo.py`
- `src/db/persist.py`
- `tests/test_idempotency.py`
- `tests/test_end_to_end.py`
- `tests/test_window_backfill.py` (신규)
- `tests/test_incremental_child_reload.py` (신규)
- `tests/test_tombstone_recompute.py` (신규)
- `tests/test_incremental_reload_snapshot.py` (신규, 가능하면 `test_incremental_child_reload.py`와 통합)

### 7-3. `src/db/repos/review_repo.py`
권장 함수 추가/강화:

```python
async def load_review_snapshot(
    conn,
    review_id: str,
    review_version: int | None = None
) -> RawReviewRecord:
    ...
```

동작:
- `review_raw` current row 읽기
- `review_version` 지정 시 해당 버전 child rows 읽기
- 미지정 시 latest version child rows 읽기
- raw placeholder / extraction rows까지 완전 복원

### 7-4. `src/jobs/run_incremental_pipeline.py`

#### 수정 지시
1. **child raw snapshot reload 구현**
   - `(review_id, review_version)` 기준으로
     - `ner_raw`
     - `bee_raw`
     - `rel_raw`
     를 다시 읽어서 `RawReviewRecord` 재구성

2. **빈 child row로 `process_review()` 재호출 금지**
   - `ner=[], bee=[], relation=[]` 상태의 임시 record로 재처리 금지

3. **Evidence Graph 재생성과 Canonical 재생성을 분리**
   - review snapshot 로드
   - evidence graph rebuild
   - canonical fact diff
   - signal full-replace
   - dirty product re-aggregate

4. **late-arrival / tombstone에서도 동일 규칙 적용**
   - window recompute는 `event_time_utc` 기준
   - child raw snapshot 없으면 quarantine

### 7-5. tombstone 시 dirty product 집합 계산 강화
`signal_repo.py` 또는 repo helper에 아래 추가:

```python
async def get_dirty_product_ids_for_review(conn, review_id: str) -> set[str]:
    ...
```

반드시 dirty 처리해야 하는 대상:
- review가 기여하던 `target_product_id`
- 비교 제품
- co-used product
- segment signal
- concern signal
- context/tool signal에 연결된 aggregate 대상

### 7-6. late-arrival review를 event_time 기준으로 backfill
규칙:
- aggregate window는 `event_time_utc` 기준
- review가 늦게 들어와도 해당 window에 포함
- `run_incremental_pipeline.py`는 dirty product 기준으로
  - `30d`
  - `90d`
  - `all`
  를 다시 계산

### 7-7. `signal_repo.py` full-replace는 유지, audit 강화
추가 권장 지표:
- old signal count
- new signal count
- removed signal count
- unchanged semantic count
- registry version

### 7-8. `aggregate_product_signals.py` / `mart_repo.py`
- 현재 구조를 유지하되, 아래를 비교 구현 또는 최소 TODO로 남긴다.
  - Python recompute baseline
  - SQL group-by recompute path
- batch dirty product set에 대해 SQL group-by aggregate 지원
- per-product full scan은 fallback/debug 모드로만 유지

### 7-9. Acceptance Criteria
- incremental pipeline이 raw child rows를 실제 reload한다.
- 빈 `ner/bee/relation`으로 재처리되는 경로가 제거된다.
- late-arrival review가 event_time 기준 aggregate에 반영된다.
- tombstone 후 aggregate와 explanation path가 모두 갱신된다.
- 동일 review 재처리 시 semantic drift 없이 idempotent하다.

---

## 8. Corpus KG 집계 / 승격 강화

### 8-1. 핵심 목표
review-level signal을 바로 serving edge로 쓰지 않고, **전체 데이터에서 자주/강하게/일관되게 나오는 fact만 전역 Corpus KG edge로 승격**한다.

### 8-2. 수정 대상 파일
- `src/mart/aggregate_product_signals.py`
- `src/mart/build_serving_views.py`
- `src/rec/*`
- 필요 시: 신규 `corpus_promoted_signal` 테이블

### 8-3. `aggregate_product_signals.py`
각 `(product_id, edge_type, dst_id, polarity, qualifier_fingerprint)` 단위로 다음 계산:
- `support_count`
- `distinct_review_count`
- `distinct_reviewer_proxy_count`
- `distinct_source_count`
- `avg_confidence`
- `recency_boost`
- `net_polarity`
- `synthetic_ratio`
- `weight`

### 8-4. promotion threshold 추가
예:
- `distinct_review_count >= 3`
- `avg_confidence >= 0.6`
- `synthetic_ratio <= 0.5`
를 만족 시에만 Corpus KG edge로 승격

### 8-5. conflict-aware aggregation
같은 product/dst에 긍정/부정이 공존하면
- 양쪽 weight 모두 저장
- 또는 net polarity score 산출
- 단일 truth로 덮지 않음

### 8-6. single-review edge와 corpus-promoted edge 분리
- `review_signal`
- `corpus_promoted_signal`
를 구분
- front/serving에는 기본적으로 corpus-promoted edge만 사용

### 8-7. `build_serving_views.py`
1. **Usage Pattern mart 추가**
   - 최소 다음 조합 지원:
     - `(product, context, tool)`
     - `(product, context, bee_attr)`
     - `(product, tool, bee_attr)`
     - `(product, concern, bee_attr)`
   - threshold 넘는 pattern만 저장

2. **front graph viewer용 corpus/evidence view 분리**
   - `serving_product_profile`
   - `corpus_product_graph_view`
   - `evidence_graph_view`
   를 분리

3. **top_xxx는 반드시 corpus-promoted edge 기준**
   - raw synthetic signal이 직접 노출되지 않게 함

### 8-8. Acceptance Criteria
- 단발성 리뷰 signal이 곧바로 전역 KG edge가 되지 않는다.
- support/weight 기반 승격이 적용된다.
- 상충 polarity도 정보 손실 없이 집계된다.
- serving layer가 반복 조합 패턴을 제공한다.
- front에서 evidence graph와 corpus graph를 분리할 수 있다.

---

## 9. 추천 레이어 보강

### 9-1. 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- `src/mart/build_serving_views.py`
- 필요 시: `sql/views_serving.sql`, `sql/candidate_queries.sql`

### 9-2. Candidate generator
추가할 것:
- `owned_product_ids` / `owned_family_ids` 활용
- `USED_WITH_PRODUCT_SIGNAL` 활용
- `context` 가중치 강화
- recommendation mode별 penalty 차등
- SQL prefilter 도입:
  - category / price band / availability / excluded ingredient / active 여부
- Python에서는 prefiltered 후보만 overlap + rerank 수행
- `catalog_validation_signal`은 candidate overlap 계산에서 제외

### 9-3. Scorer
추가/유지할 것:
- `goal_fit_master`
- `goal_fit_review_signal`
- `skin_type_fit`
- `skin_tone_fit` (가능하면)
- `purchase_loyalty_score`
- `novelty_penalty/bonus`
- `used_with_product_bonus` (루틴형 추천일 경우)
- `catalog_validation_signal` scoring 제외 유지
- BEE_ATTR / KEYWORD double-count 방지 유지

### 9-4. Explainer
설명 시 아래를 반영:
- keyword 단독이 아니라 상위 BEE_ATTR 함께 설명
- concern signal은 positive/negative 구분
- context/tool/co-used product를 설명 축으로 포함
- `signal -> fact -> provenance -> snippet` chain 유지
- `signal_evidence` 기준 설명 evidence 정렬

예:
- “이 제품은 **밀착력 축에서** ‘들뜸없음’ 신호가 강합니다.”
- “특히 **세안 후 / 퍼프 사용 맥락**에서 자주 언급됐습니다.”
- “건조함 concern에 대해 positive signal이 있습니다.”

### 9-5. Acceptance Criteria
- 추천 점수가 corpus-level weighted signal을 사용한다.
- co-use / comparison / goal / skin-type / purchase 기반 feature가 실제 점수축으로 쓰인다.
- `catalog_validation_signal`이 candidate/scoring/standard explanation에서 완전히 배제된다.

---

## 10. 기타 구조/운영성 보강

### 10-1. Concept key 타입 통일
- 저장/조인 키는 **`concept_id`**로 통일하는 것을 권장한다.
- serving profile 필드명은 `*_concept_ids` 유지
- IRI는 canonical/entity layer에서만 사용하거나 derived field로 둔다.

### 10-2. Goal / main_benefit 연결
- `product_ingest.py`에서 `main_benefits -> concept_registry seed -> entity_concept_link`를 보장
- `serving_product_profile`에 `main_benefit_concept_ids` 유지
- `candidate_generator` overlap에 `goal:*` feature 유지
- `scorer.py`에 `goal_fit_master`, `goal_fit_review_signal`를 명시적 feature로 유지

### 10-3. Purchase 활용 범위 명시
MVP에서 purchase 사용 범위는 아래로 한정:
- `PREFERS_BRAND`
- `PREFERS_CATEGORY`
- `REPURCHASES_PRODUCT_OR_FAMILY`
- optional `OWNS_PRODUCT`

아직 defer하는 것:
- cooldown
- basket bundling
- full owned suppression (필요 시 단계적 도입)

### 10-4. Provenance 일반화
`fact_provenance`는 review-derived facts뿐 아니라 user/product/manual/system facts까지 담을 수 있게 일반화한다.

### 10-5. SQL-first 방향 보강
- candidate prefilter는 SQL
- aggregate는 batch SQL/group-by 지원
- Python은 최종 scoring/explanation thin layer로 유지

### 10-6. `kg_mode` / legacy-shadow 분리
- core pipeline: GraphRapping canonical path only
- kg shadow / evidence debug path: 별도 entrypoint 또는 feature-gated wrapper
- 운영 경로와 실험 경로를 한 함수에서 섞지 않는다.

### 10-7. Repo hygiene / 문서화
- `README.md`
- `ARCHITECTURE.md`
- `CHANGELOG.md`
- 가능하면 decision log index
- `pyproject.toml`에 검토:
  - `ruff`
  - `mypy`
  - `pytest-cov`
  - Postgres integration test harness

---

## 11. 스키마 보강 권장

### 11-1. 추가 테이블 / 컬럼

#### `canonical_fact.fact_status` 또는 별도 `evidence_fact`
- `EVIDENCE_ONLY`
- `PROMOTION_CANDIDATE`
- `CANONICAL_PROMOTED`
- `REJECTED`

#### `corpus_promoted_signal`
- `product_id`
- `edge_type`
- `dst_id`
- `polarity`
- `qualifier_fingerprint`
- `support_count`
- `distinct_review_count`
- `distinct_reviewer_proxy_count`
- `avg_confidence`
- `synthetic_ratio`
- `weight`
- `promoted_at`

#### `keyword_candidate`
- `surface_text`
- `normalized_surface`
- `source_review_id`
- `source_phrase`
- `candidate_count`
- `approval_status`

---

## 12. 테스트 추가/수정 목록

### 기존 테스트 보강
- `tests/test_bee_normalizer.py`
- `tests/test_signal_emitter.py`
- `tests/test_idempotency.py`
- `tests/test_provenance_fidelity.py`
- `tests/test_recommendation.py`
- `tests/test_concept_link_integrity.py`
- `tests/test_reviewer_isolation.py`
- 기존 `test_truth_override_protection.py` 확장

### 신규 테스트
- `tests/test_incremental_reload_snapshot.py`
- `tests/test_recommended_to_projection.py`
- `tests/test_reverse_transform_ref_kind.py`
- `tests/test_signal_evidence_source_of_truth.py`
- `tests/test_signal_dedup_polarity.py`
- `tests/test_candidate_prefilter_sql_logic.py`
- `tests/test_batch_aggregate_consistency.py`
- `tests/test_generic_provenance_model.py`
- `tests/test_concept_key_consistency.py`
- `tests/test_goal_fit_feature.py`
- `tests/test_purchase_signal_usage.py`
- `tests/test_window_backfill.py`
- `tests/test_incremental_child_reload.py`
- `tests/test_tombstone_recompute.py`
- `tests/test_shadow_mode_isolation.py`

---

## 13. 권장 작업 순서

### Phase 1 — 리뷰 의미 손실 복구 + KG 정리 (우선)
1. `mention_extractor.py`: synthetic/auto keyword/NER-BeE flatten 수정
2. `canonicalizer.py`: sentiment-split BEEAttr 제거, canonical keyword만 허용
3. `adapter.py`: promotion gate 추가
4. `bee_normalizer.py`: stronger contract + candidate keyword 구분
5. `run_daily_pipeline.py`: BEE negated/intensity 전달, `recommended_to` 계약 정리, `kg_mode` 분리
6. `canonical_fact_builder.py`: BEE qualifiers 보존, 범용 provenance 준비
7. `projection_registry.csv` / `projection_registry.py`: evidence gate + promotion rules 추가
8. `signal_emitter.py`: reverse ref_kind, signal merge 정책, `signal_evidence` 정본화
9. `ids.py`: signal key 강화

### Phase 2 — 유저 레이어 강화
10. `canonicalize_user_facts.py`: fact family 분리
11. `personal_agent_adapter.py`: 필요 시 richer context/goal mapping 보강
12. `purchase_ingest.py` / purchase-derived features 연결
13. `aggregate_user_preferences.py`: confidence/recency 강화
14. `build_serving_views.py`: user profile contract 확장
15. `candidate_generator.py` / `scorer.py`: user feature 확장

### Phase 3 — 증분 안정화
16. `review_repo.py`: `load_review_snapshot()` 추가
17. `run_incremental_pipeline.py`: child row reload 구현
18. tombstone dirty product 계산 강화
19. event_time 기반 backfill 구현
20. aggregate recompute audit 추가
21. `aggregate_product_signals.py` batch SQL/group-by path 보강

### Phase 4 — Corpus KG / Serving 고도화
22. `aggregate_product_signals.py`: corpus weight + promotion threshold
23. `build_serving_views.py`: usage pattern mart + evidence/corpus graph 분리
24. 추천 feature에 corpus-level weight 반영
25. candidate SQL prefilter 도입
26. docs/repo hygiene 정리

---

## 14. 최종 완료 기준 (통합 Acceptance Criteria)

### 리뷰 의미 손실 복구
- BEE phrase의 `negated`, `intensity`가 Layer 2 또는 Layer 2.5까지 보존된다.
- 동일 review 내 polarity/qualifier가 다른 signal이 잘못 merge되지 않는다.
- explanation에서 BEE_ATTR + KEYWORD + context가 함께 복원된다.

### 유저 레이어 강화
- user profile이 `state + preference + concern + goal + behavior`를 모두 포함한다.
- purchase-derived signal이 실제 candidate/scoring에 반영된다.
- `skin_type`/`skin_tone`가 최소 한 가지 추천 feature로 실제 사용된다.

### 증분 파이프라인 안정화
- incremental pipeline이 raw child rows를 실제 reload한다.
- late-arrival review가 event_time 기준 aggregate에 반영된다.
- tombstone 후 aggregate와 explanation path가 모두 갱신된다.
- 동일 review 재처리 시 semantic drift 없이 idempotent하다.

### 리뷰 KG / Corpus KG 목표
- 리뷰 1건의 BEE-only phrase가 자동으로 전역 KG edge가 되지 않는다.
- canonical keyword 없는 auto phrase는 전역 keyword node가 되지 않는다.
- `used_with(Product, Product)`가 전역 co-use signal로 살아 있다.
- 전체 코퍼스 집계 후 support/weight threshold를 넘은 fact만 corpus-promoted edge가 된다.
- front graph viewer가 evidence graph와 corpus graph를 구분해서 보여줄 수 있다.
- 여러 상품을 합쳐도 placeholder/synthetic/auto-generated 노이즈가 전역 KG를 오염시키지 않는다.

### 추천 / 설명 / 정합성
- `recommended_to`가 object/qualifier 계약과 충돌 없이 deterministic하게 동작한다.
- reverse transform이 concept/entity ref kind를 정확히 유지한다.
- `signal_evidence`가 signal provenance의 정본이 된다.
- `catalog_validation_signal`이 candidate/scoring/standard explanation에서 완전히 분리된다.
- product main benefits와 user goals가 실제 추천 feature로 연결된다.
- purchase 이벤트가 쓸 범위와 안 쓸 범위가 명확해진다.

---

## 15. Claude Code에게 전달할 핵심 요약

> 이번 수정은 구조를 갈아엎는 작업이 아니다.
> 목표는 **리뷰 의미 손실 복구**, **유저 레이어 강화**, **증분 재처리의 정확성 보장**, 그리고 **Evidence Graph를 전체 리뷰 코퍼스 기반 Corpus KG로 승격하는 흐름 확립**이다.
> Layer 2/2.5/3 철학은 유지하되, synthetic/auto-generated 요소는 evidence-only로 낮추고, BEE 의미 보존·user feature 확장·raw snapshot 기반 incremental recompute·support/weight 기반 corpus promotion을 우선 구현하라.
