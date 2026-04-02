# GraphRapping vNext 최종 수정 지시서 (최신 main + mockdata 반영)

작성 목적:
- 최신 `main` 레포와 `mockdata/` 스키마를 기준으로, 아직 남아 있는 구조적 문제를 **파일별 수정 포인트 / 왜 필요한지 / Acceptance Criteria / 테스트 항목** 형식으로 정리한다.
- 이번 지시서는 **제품 truth + 리뷰 코퍼스 기반 KG + 유저 프로필**을 통합해 개인화/추천/탐색을 수행한다는 큰 방향에 맞춘다.
- 특히 이번 문서는 `BEE_ATTR vs KEYWORD` 중 `제형(Texture)` 축을 명확히 반영한다.

---

## 0. 이번 수정의 최상위 방향 (절대 흔들리지 말 것)

### 0-1. Review graph의 역할
`src/kg`는 **per-review evidence graph**로 유지한다.
이 레이어는 noisy / placeholder / synthetic relation / auto keyword candidate를 허용하는 **증거 정리 계층**이다.
이 레이어를 전역 KG 정본으로 직접 union하지 않는다.

### 0-2. 진짜 전역 KG의 본체
전역적으로 쓰는 그래프 의미는 아래에서 형성된다.
- Layer 2: canonical_fact
- Layer 2.5: wrapped_signal
- Layer 3: corpus-promoted aggregate / serving profiles

즉 **evidence graph → corpus KG → serving graph** 3단 구조를 유지한다.

### 0-3. Product truth / Review signal / User profile 역할 구분
- Product master truth:
  - brand / category / ingredients / country / price / main_benefits / family
- Review-derived signal:
  - bee_attr / keyword / context / concern / comparison / co-use / segment targeting
- User profile:
  - state (skin_type/tone/age_band)
  - concern / goal / context preference
  - ingredient / brand / category preference
  - purchase-derived ownership / repurchase / recency

### 0-4. 제형(Texture) 축에 대한 최종 해석
이 문서는 아래를 **표준 규칙**으로 채택한다.

- `제형(Texture)` = **BEE_ATTR (상위 속성 축)**
- `젤`, `가벼운 로션`, `워터리`, `리치 크림` = **KEYWORD / descriptor (하위 구체 표현)**

즉 user/product 양쪽 모두에서:
- `BEE_ATTR(Texture)`와
- `KEYWORD(GelLike / LightLotionLike / Watery / Creamy)`
를 **함께 유지**한다.

추천 계산에서는:
- KEYWORD match를 더 강하게 사용
- BEE_ATTR는 residual / backoff feature로 사용
- explanation에서는 “제형 축 + 구체 표현”을 함께 노출

금지:
- texture 표현을 전부 BEE_ATTR로만 올리는 것
- texture 표현을 전부 KEYWORD로만 올리는 것

---

## 1. P0 — 즉시 수정해야 하는 구조 문제

---

## P0-1. `product_loader.py`가 mock product truth를 버리고 있음

### 수정 대상 파일
- `src/loaders/product_loader.py`
- 필요 시: `src/ingest/product_ingest.py`
- 테스트: `tests/test_product_loader_mock_schema.py` (신규)

### 현재 문제
최신 mock schema(`mockdata/product_catalog_es.json`, `mockdata/README.md`)는 아래 필드를 제공한다.
- `SALE_PRICE`
- `MAIN_EFFECT`
- `MAIN_INGREDIENT`
- `REPRESENTATIVE_PROD_CODE`
- `REPRESENTATIVE_PROD_NAME`
- `REVIEW_COUNT`
- `REVIEW_SCORE`

그런데 현재 `src/loaders/product_loader.py`는 주석/구현 모두에서
- `price=None`
- `ingredients=[]`
- `main_benefits=[]`
- `country_of_origin=None`
를 MVP default로 넣고 있다.

즉 mock product truth가 실제 ingest 전에 대부분 버려진다.
이 상태에선 local/mock 검증에서 product truth 기반 추천 품질을 제대로 볼 수 없다.

### 왜 필요한가
현재 core ingest(`src/ingest/product_ingest.py`)는 richer truth를 받을 준비가 되어 있다.
문제는 loader가 이 truth를 넘기지 않는다는 것이다.
즉 product truth richness가 pipeline 앞단에서 사라지고 있다.

### 수정 내용
`src/loaders/product_loader.py`에서 최소 아래를 매핑하라.

#### 필수 매핑
- `ONLINE_PROD_SERIAL_NUMBER -> product_id`
- `prd_nm -> product_name`
- `BRAND_NAME -> brand_name`
- `CTGR_SS_NAME -> category_name`
- `SALE_PRICE -> price`
- `MAIN_EFFECT -> main_benefits`
- `MAIN_INGREDIENT -> ingredients`
- `COUNTRY_OF_ORIGIN` 또는 대응 필드 -> country_of_origin (있으면)
- `REPRESENTATIVE_PROD_CODE -> variant_family_id`

#### 보조 매핑 (attrs 또는 debug/meta로 보존)
- `REPRESENTATIVE_PROD_NAME`
- `REVIEW_COUNT`
- `REVIEW_SCORE`
- `SAP_CODE`, `ONLINE_PROD_CODE` 등 외부 식별자

#### 정규화 규칙
- `MAIN_EFFECT`
  - string이면 `[value]`
  - delimiter 포함 시 split + trim
- `MAIN_INGREDIENT`
  - string이면 delimiter split
  - 빈 문자열이면 `[]`
- `SALE_PRICE`
  - 숫자 변환 실패 시 `None`

### Acceptance Criteria
- `load_products_from_json("mockdata/product_catalog_es.json")` 결과에서
  - `price`가 채워진다.
  - `main_benefits`가 채워진다.
  - `ingredients`가 채워진다.
  - `variant_family_id`가 채워진다.
- `ingest_product()` 후
  - `main_benefit_concept_ids`가 concept_registry로 seed된다.
  - ingredient concept link가 생성된다.
- product loader가 mock schema를 “MVP defaults”로 날리지 않는다.

### 테스트 항목
신규 `tests/test_product_loader_mock_schema.py`
- case 1: `SALE_PRICE` -> `price`
- case 2: `MAIN_EFFECT` -> `main_benefits`
- case 3: `MAIN_INGREDIENT` -> `ingredients`
- case 4: `REPRESENTATIVE_PROD_CODE` -> `variant_family_id`
- case 5: 판매중 필터 유지

---

## P0-2. user adapter의 concept 매핑 오류 정정

### 수정 대상 파일
- `src/user/adapters/personal_agent_adapter.py`
- `src/user/canonicalize_user_facts.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 테스트:
  - `tests/test_user_adapter_semantics.py` (신규)
  - `tests/test_concept_link_integrity.py` (확장)

### 현재 문제
현재 adapter는 아래 문제를 가진다.

#### 문제 A. `OWNS_PRODUCT`가 Product identity가 아니라 concept처럼 처리됨
현재 `purchase_features.owned_product_ids`를 `OWNS_PRODUCT`로 올리면서 `ConceptType.BRAND`를 사용한다.
그런데 serving profile / candidate generator / scorer는 이를 실제 `product_id`와 비교해 already-owned suppression / novelty 계산에 쓰고 있다.
즉 concept와 product identity가 섞여 있다.

#### 문제 B. `preferred_texture`가 BEE_ATTR로만 들어감
현재 `preferred_texture`는 `PREFERS_BEE_ATTR` + `ConceptType.BEE_ATTR`로만 올라간다.
하지만 `젤`, `가벼운 로션`은 **Texture라는 BEE_ATTR 아래의 KEYWORD**다.
즉 제형 축과 구체 texture preference를 동시에 표현해야 하는데, 현재는 상위 축만 남고 하위 표현이 사라진다.

#### 문제 C. `REPURCHASES_PRODUCT_OR_FAMILY`에 category와 brand가 섞임
mock normalized profile의 `preferred_repurchase_category`는 category인데, purchase-derived `repurchased_brand_ids`도 같은 predicate로 들어간다.
그 결과 serving profile에서 `repurchase_brand_ids` 같은 필드에 다른 의미가 섞일 수 있다.

### 왜 필요한가
유저 레이어는 현재 구조에서 추천 품질을 좌우한다.
특히 ownership / repurchase / texture preference가 잘못 표현되면:
- novelty
- already-owned suppression
- goal/context coupling
- texture/제형 맞춤 추천
이 다 흔들린다.

### 수정 내용

#### A. `OWNS_PRODUCT`는 Product identity로 전환
- `OWNS_PRODUCT`는 `ConceptType.*`로 encode하지 말고
  - `object_ref_kind = ENTITY`
  - `object_type = Product`
  - `object_iri = product:{product_id}` 또는 별도 product identity field
- user serving profile의 `owned_product_ids`는 실제 product_id 또는 product IRI만 가진다.

#### B. `preferred_texture`는 2단 표현으로 전환
예: `젤`, `가벼운 로션`

유저 fact 생성 시 **둘 다 생성**:
1. 상위 축
   - `PREFERS_BEE_ATTR(Texture)`
2. 하위 구체 표현
   - `PREFERS_KEYWORD(GelLike)`
   - `PREFERS_KEYWORD(LightLotionLike)`

필요 시 helper 추가:
```python
def normalize_texture_preference(surface: str) -> tuple[str, list[str]]:
    # returns (bee_attr="Texture", keyword_ids=[...])
```

#### C. repurchase predicate 분리
아래로 분리하라.
- `REPURCHASES_BRAND`
- `REPURCHASES_CATEGORY`
- optional `REPURCHASES_FAMILY`

그리고 serving profile에서도 필드를 분리한다.
- `repurchase_brand_ids`
- `repurchase_category_ids`
- optional `repurchase_family_ids`

### Acceptance Criteria
- `owned_product_ids`가 실제 product identity로 저장되고 candidate/scorer에서 일관되게 비교된다.
- `preferred_texture=["젤", "가벼운 로션"]`이면
  - `PREFERS_BEE_ATTR(Texture)` 생성
  - `PREFERS_KEYWORD(...)`도 생성
- repurchase brand/category가 섞이지 않고 serving profile에서 분리된다.

### 테스트 항목
신규 `tests/test_user_adapter_semantics.py`
- case 1: `OWNS_PRODUCT`가 product identity로 들어감
- case 2: texture preference가 attr+keyword 둘 다 생성
- case 3: repurchase brand/category 분리
- case 4: recommendation에서 owned suppression / novelty가 실제로 작동

---

## P0-3. `serving_product_profile`에 corpus promotion을 모든 기본 경로에서 강제

### 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 필요 시: `src/web/*`, `src/graph/*`, debug/export path
- 테스트:
  - `tests/test_serving_profile_promotion_gate.py` (신규)

### 현재 문제
현재 `aggregate_product_signals.py`는
- `distinct_review_count`
- `avg_confidence`
- `synthetic_ratio`
- `corpus_weight`
- `is_promoted`
를 계산한다.

좋다.
하지만 중요한 건 **이걸 serving/runtime이 일관되게 강제하느냐**다.
현재 `build_serving_product_profile()`는 `promoted_only=True`를 기본으로 갖고 있어서 좋아졌지만,
이 원칙이 다른 downstream/debug/export/API 경로까지 철저히 동일한지는 아직 불명확하다.

### 왜 필요한가
네 최종 목표는 “리뷰 코퍼스 전체를 KG화하고, 노이즈를 걸러내고, weight 높은 신호만 승격해 활용하는 것”이다.
이 목적을 실제 시스템에 반영하려면,
**promoted signal only**가 기본 추천/탐색/표준 설명 경로에서 일관되게 강제되어야 한다.

### 수정 내용
1. `build_serving_product_profile()`는 현재 기본값 유지:
   - `promoted_only=True`
2. candidate/scorer/explainer/web/API/export 중 기본 사용자-facing 경로는
   - promoted-only serving profile만 사용
3. non-promoted signal은 아래 경로에서만 허용:
   - debug / QA / analyst mode
4. `catalog_validation_signal`은 기존대로 defense-in-depth로 제외 유지

### Acceptance Criteria
- 기본 recommendation path는 non-promoted signal을 사용하지 않는다.
- debug 모드에서만 non-promoted signal이 보인다.
- same product에 promoted/non-promoted signal이 섞여 있어도 기본 추천 결과는 promoted-only와 동일하다.

### 테스트 항목
신규 `tests/test_serving_profile_promotion_gate.py`
- case 1: promoted_only=True이면 non-promoted signal이 제외됨
- case 2: debug mode에서만 non-promoted가 노출됨
- case 3: candidate/scoring 결과가 promoted-only 기준으로 안정적

---

## P0-4. provenance 정본을 `signal_evidence`로 강제하고 `source_fact_ids`는 캐시로 강등

### 수정 대상 파일
- `src/wrap/signal_emitter.py`
- `src/db/repos/signal_repo.py`
- `src/db/repos/provenance_repo.py`
- `src/rec/explainer.py`
- `src/mart/aggregate_product_signals.py`
- `sql/ddl_signal.sql`
- 테스트:
  - `tests/test_signal_evidence_sot.py` (신규)

### 현재 문제
최신 코드/문서에서는 provenance SoT가 `signal_evidence`라고 설명하지만,
실제 구현에는 아직 `wrapped_signal.source_fact_ids`가 남아 있고,
merge 시 append도 하며,
aggregate의 `evidence_sample`도 일부는 signal row 자체를 보고 만든다.
즉 개념적으로는 정본을 정했지만, 저장/런타임에선 이중화가 남아 있다.

### 왜 필요한가
정본 provenance가 둘이면 explanation / audit / backfill에서 반드시 어긋난다.
특히 네가 provenance와 explanation fidelity를 강하게 요구하므로,
여기는 하나로 못 박아야 한다.

### 수정 내용
1. **정본**: `signal_evidence`
2. `wrapped_signal.source_fact_ids`
   - 유지한다면 cache/debug only 주석 강화
   - 가능한 경우 write path를 `signal_evidence` derived 결과로 제한
3. `aggregate_product_signals.py`의 `evidence_sample`은
   - raw `source_fact_ids[0]`가 아니라
   - `signal_evidence` 기반 rank/summary를 사용
4. `explainer.py` / `provenance_repo.py`는
   - 반드시 `signal_evidence -> canonical_fact -> fact_provenance -> review_raw`
   경로를 primary path로 사용

### Acceptance Criteria
- explanation path가 `signal_evidence`만으로 복원 가능하다.
- `source_fact_ids`를 지워도(또는 비워도) explanation 동작 가능하다.
- aggregate evidence sample이 signal_evidence와 불일치하지 않는다.

### 테스트 항목
신규 `tests/test_signal_evidence_sot.py`
- case 1: signal_evidence로 explanation chain 복원
- case 2: source_fact_ids 없이도 explainer 동작
- case 3: evidence_sample이 signal_evidence 기반으로 stable ordering 유지

---

## P0-5. review mock schema를 evidence/증분 운영에 더 맞게 보강

### 수정 대상 파일
- `mockdata/review_triples_raw.json`
- `mockdata/README.md`
- `src/loaders/relation_loader.py`
- 테스트:
  - `tests/test_mock_review_contract.py` (신규)

### 현재 문제
mock review는 현재 loader 계약에는 맞지만,
`source_review_key`, `author_key` 같은 stable source identity가 없다.
지금은 `brand + product + text + collected_at + source_row_num` fallback으로 `review_id`를 만들 가능성이 높다.
mock 검증용으론 되지만, idempotency/late-arrival/reorder robustness를 테스트하기엔 약하다.

### 왜 필요한가
네 시스템은 증분 처리, tombstone, provenance, reviewer proxy 분리를 중요하게 본다.
그러면 mock도 production-like stable key를 일부 가져야 한다.

### 수정 내용
mock schema에 optional 필드 추가:
- `source_review_key`
- `author_key`

`relation_loader.py`에서 있으면 사용:
- `source_review_key` -> deterministic review_id 우선 키
- `author_key` -> stable reviewer_proxy 경로

없으면 현재 fallback 유지.

### Acceptance Criteria
- mock review에 stable key가 있는 경우 review_id가 text hash fallback보다 우선된다.
- author_key가 있는 경우 reviewer proxy가 review-local이 아니라 source-stable로 생성된다.
- 기존 mock(키 없는 경우)도 fallback으로 계속 동작한다.

### 테스트 항목
신규 `tests/test_mock_review_contract.py`
- case 1: source_review_key 우선 사용
- case 2: author_key 우선 reviewer_proxy 생성
- case 3: 키 없는 경우 fallback 유지

---

## 2. P1 — 방향은 맞지만 아직 덜 정교한 부분

---

## P1-1. user aggregation에 recency / source-weighting / frequency 반영

### 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `src/user/canonicalize_user_facts.py`
- `src/rec/scorer.py`
- `configs/scoring_weights.yaml`
- 테스트:
  - `tests/test_user_preference_weighting.py` (신규)

### 현재 문제
현재 user aggregation은 `(predicate, dst)` grouping 후 `confidence` 중심으로만 weight를 잡는 경향이 강하다.
즉 다음 차이가 충분히 모델링되지 않는다.
- chat explicit preference
- purchase-derived repeated preference
- recent vs stale preference
- frequency 차이

### 왜 필요한가
유저 레이어는 지금 구조상 충분히 잘 올라와 있다.
문제는 “무엇이 더 강하고 최신인 선호인가”가 약하다는 점이다.
개인화 품질은 이 weighting에서 크게 갈린다.

### 수정 내용
`aggregate_user_preferences.py`에서 weight 계산을 최소 아래 요인으로 확장:
- base confidence
- source priority (`purchase > chat explicit > basic/profile > inferred`)
- recency decay (`last_seen_at` 기반)
- frequency / repetition bonus

그리고 scorer에서 user-side weight를 실제 활용할 수 있도록,
serving_user_profile에 각 preference item마다 `weight`, optional `source`, `last_seen_at` 유지.

### Acceptance Criteria
- 같은 concept라도 purchase 기반 선호가 weak chat 선호보다 높다.
- recent preference가 stale preference보다 높다.
- repeated preference가 single mention보다 높다.

### 테스트 항목
신규 `tests/test_user_preference_weighting.py`
- case 1: purchase > chat weak
- case 2: recency decay 적용
- case 3: repetition bonus 적용

---

## P1-2. candidate generation을 SQL prefilter + Python rerank 구조로 보강

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- 신규 `sql/candidate_prefilter.sql` 또는 repo query module
- `src/db/repos/mart_repo.py` 또는 query repo
- 테스트:
  - `tests/test_candidate_prefilter_sql.py` (신규)

### 현재 문제
현재 candidate generation은 `product_profiles: list[dict]`를 Python에서 루프 돌며 hard filter와 overlap을 계산한다.
mock 규모에선 충분하지만, full catalog에선 비싸다.
또 Postgres-first / SQL-first 방향과도 완전히 align되진 않는다.

### 왜 필요한가
너의 목표는 운영형 추천/개인화/탐색이다.
그러면 candidate generation은 결국 SQL prefilter가 있어야 한다.

### 수정 내용
SQL prefilter 단계 추가:
- active product
- sale status
- category / mode based filter
- avoided ingredient exclusion
- optional price band filter
- optional family/owned suppression

Python은 prefiltered set에 대해서만 overlap + rerank 수행.

### Acceptance Criteria
- prefiltered candidate 수가 전체 catalog보다 충분히 작다.
- STRICT / EXPLORE / COMPARE 모드가 SQL prefilter와 일관되게 작동한다.
- catalog_validation_signal은 candidate generation에서 제외된다.

### 테스트 항목
신규 `tests/test_candidate_prefilter_sql.py`
- case 1: category strict filter
- case 2: avoided ingredient exclusion
- case 3: compare mode에서 comparison neighbor 허용

---

## P1-3. `preferred_texture`를 scorer/explainer에 명시적으로 연결

### 수정 대상 파일
- `src/user/adapters/personal_agent_adapter.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- `configs/scoring_weights.yaml`
- 테스트:
  - `tests/test_texture_preference_flow.py` (신규)

### 현재 문제
이번 사이클에서 가장 중요한 개념 정리는 이것이다.
- `제형`은 BEE_ATTR
- `젤`, `가벼운 로션`, `워터리` 등은 KEYWORD/descriptor

즉 user/product 양쪽에서 둘 다 표현되어야 하고,
scoring에서는 KEYWORD를 더 강하게 쓰고 BEE_ATTR는 residual/backoff로 써야 한다.

### 왜 필요한가
이걸 안 하면 texture preference가
- 너무 상위 축만 남아 구분력이 떨어지거나
- 반대로 keyword만 남아 구조적 그룹핑이 깨진다.

### 수정 내용
1. user adapter에서 `preferred_texture`는 attr+keyword 둘 다 생성
2. candidate generator에서
   - texture keyword overlap은 `keyword:*`
   - texture attr overlap은 `bee_attr:*`
   로 같이 보되, 기존 double-count 방지 원칙 유지
3. scorer에서
   - `keyword_match`가 texture 구체 선호를 먼저 먹고
   - `residual_bee_attr_match`는 keyword로 커버 안 된 attr만 반영
4. explainer에서 반드시 두 층으로 설명
   - “제형 축에서 …”
   - “특히 젤/가벼운 로션 계열 표현이 …”

### Acceptance Criteria
- user texture preference가 attr+keyword 둘 다로 저장된다.
- texture keyword match가 attr-only보다 더 강하게 점수에 반영된다.
- explanation이 “제형 + 구체 texture 표현” 두 층으로 나온다.

### 테스트 항목
신규 `tests/test_texture_preference_flow.py`
- case 1: `preferred_texture=["젤"]` -> `PREFERS_BEE_ATTR(Texture)` + `PREFERS_KEYWORD(GelLike)`
- case 2: gel-like product가 cream-like product보다 점수가 높음
- case 3: explanation에 Texture + GelLike가 함께 노출

---

## P1-4. raw user mock과 normalized user mock의 계약을 명확화

### 수정 대상 파일
- `mockdata/README.md`
- `src/loaders/user_loader.py`
- optional 신규 `src/loaders/user_raw_loader.py`
- 테스트:
  - `tests/test_mock_user_contract.py` (신규)

### 현재 문제
mock README는 raw 7-column과 normalized 3-group을 둘 다 설명하지만,
현재 실제 loader는 normalized만 바로 먹는다.
즉 문서와 실행 경로의 기대가 혼재되어 있다.

### 수정 내용
둘 중 하나 선택:

#### 권장안
- 현재 공식 입력 계약은 `user_profiles_normalized.json`
- README에 이를 명확히 표기
- raw 7-column은 future normalizer input/reference로만 표기

#### 대안
- `user_raw_loader.py`를 추가해 raw 7-column -> normalized transformer 제공

### Acceptance Criteria
- mock README와 실제 loader 계약이 충돌하지 않는다.
- 새 참여자가 raw mock을 바로 loader에 넣을 수 있다고 오해하지 않는다.

### 테스트 항목
신규 `tests/test_mock_user_contract.py`
- case 1: normalized-only loader 계약 확인
- optional case 2: raw loader 추가 시 normalized 변환 검증

---

## P1-5. `shared_entities.json` / `review_kg_output.json`을 실제 회귀 테스트 자산으로 사용

### 수정 대상 파일
- 신규 `tests/test_mock_integrity.py`
- 신규 `tests/test_mock_review_kg_regression.py`
- 필요 시: `mockdata/README.md`

### 현재 문제
`shared_entities.json`과 `review_kg_output.json`은 좋은 참조 자산인데, 현재 코드 경로에서 적극적으로 활용되지 않는다.

### 왜 필요한가
이 두 파일은 cross-source identity와 evidence graph regression을 검증하는 데 매우 적합하다.
mock data가 진짜 계약 테스트 자산이 되려면 여기를 써야 한다.

### 수정 내용
1. `test_mock_integrity.py`
   - user/product/review 간 shared IDs consistency 검증
2. `test_mock_review_kg_regression.py`
   - evidence_kind, synthetic demotion, keyword candidate handling, confidence range regression 검증

### Acceptance Criteria
- shared entity anchors가 product/user/review mock 전반에서 일관된다.
- review KG output fixture가 evidence graph regression 테스트에 사용된다.

### 테스트 항목
- `shared_entities`에 있는 product IDs가 product catalog와 user/review mock에 모두 매칭되는지
- review_kg_output fixture의 edge/entity 참조 무결성

---

## 3. P2 — 유지보수성과 장기 운영성

---

## P2-1. `fact_provenance` 범용화

### 수정 대상 파일
- `sql/ddl_canonical.sql`
- `src/canonical/canonical_fact_builder.py`
- `src/db/repos/provenance_repo.py`

### 내용
현재 provenance는 review-derived facts에 최적화돼 있다.
다음 단계에선 아래 컬럼으로 일반화 검토:
- `source_domain` (`review|user|product|manual|system`)
- `source_kind` (`raw|summary|master|derived`)
- `source_table`
- `source_row_id`

### 이유
product truth / user fact / review fact를 하나의 provenance 철학으로 묶기 위함.

---

## P2-2. repo boundary 정리

### 수정 대상 파일
- `README.md`
- `ARCHITECTURE.md`
- `HANDOFF.md`
- optional refactor in `src/jobs/run_daily_pipeline.py`

### 내용
현재 repo는 `src/kg`, `src/graph`, `src/web`, `src/static`, `src/db`, `src/loaders`가 모두 같이 있다.
지금은 괜찮지만 장기적으로는 코어 serving path와 evidence/experimental path의 boundary를 더 분명히 적어둘 필요가 있다.

### 이유
scope creep 방지 및 미래 유지보수성 향상.

---

## 4. 이번 사이클에서 절대 하지 말 것

1. evidence graph를 다시 전역 KG 정본으로 직접 올리지 말 것
2. synthetic BEE relation이나 auto keyword를 serving-grade edge로 바로 승격하지 말 것
3. texture를 BEE_ATTR로만, 또는 KEYWORD로만 단일층으로 다루지 말 것
4. reviewer proxy와 real user를 어떤 경로로도 합치지 말 것
5. product truth를 review-derived relation로 overwrite하지 말 것
6. catalog_validation_signal을 추천 점수/후보생성에 섞지 말 것

---

## 5. Claude Code 작업 순서

### Phase A — mock schema alignment
1. `src/loaders/product_loader.py`
2. `src/user/adapters/personal_agent_adapter.py`
3. `src/mart/build_serving_views.py`
4. `src/rec/candidate_generator.py`
5. `src/rec/scorer.py`
6. 관련 mock tests

### Phase B — serving/runtime enforcement
7. promoted-only contract across user-facing paths
8. provenance SoT = `signal_evidence`
9. texture attr+keyword scoring/explaining
10. user weighting 강화

### Phase C — scale/maintainability
11. candidate SQL prefilter
12. raw user contract 정리
13. mock regression tests
14. provenance 범용화 / boundary docs

---

## 6. 최종 완료 기준

이번 vNext 수정 완료는 아래를 모두 만족할 때로 본다.

1. product loader가 mock product truth(`SALE_PRICE`, `MAIN_EFFECT`, `MAIN_INGREDIENT`, `REPRESENTATIVE_PROD_CODE`)를 실제 ingest에 반영한다.
2. `OWNS_PRODUCT`가 실제 product identity로 동작한다.
3. `preferred_texture`가 `BEE_ATTR(Texture)` + `KEYWORD(GelLike/...)` 두 층으로 반영된다.
4. serving product profile의 기본 경로는 promoted signal만 사용한다.
5. provenance 정본은 `signal_evidence`로 일관된다.
6. raw/normalized user mock 계약이 문서와 실행 경로에서 충돌하지 않는다.
7. shared_entities / review_kg_output이 실제 테스트 자산으로 사용된다.
8. product truth + review corpus signal + user profile이 shared concept plane에서 일관되게 연결된다.

