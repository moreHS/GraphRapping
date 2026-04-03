# GraphRapping — Claude Code 후속 수정 지시서 (최신 main + mockdata 기준)

작성 기준:
- 최신 `main` 레포 정적 리뷰
- `mockdata/`에 추가된 product / user / review / KG output 참조 데이터 반영
- 현재 방향성: **Evidence graph(per-review) → Canonical facts → Wrapped signals → Corpus promotion → Serving profiles → Personalized recommendations** 유지

이번 문서의 목적:
1. 이미 큰 방향이 맞게 잡힌 현재 구조를 **더 정확하고 운영 가능하게 다듬기**
2. mockdata 스키마를 실제 코드 계약과 더 잘 맞추기
3. Product / Review / User 3축을 shared concept plane 위에서 더 강하게 연결하기
4. 전역 리뷰 KG 활용이라는 최종 목표에 맞게 **promoted signal 중심 serving**을 더 확실히 강제하기

---

## 0. 작업 전 공통 원칙

1. 리뷰 per-review KG(`src/kg`)는 **정본 전역 KG가 아니라 evidence graph**다.
2. 전역적으로 추천/탐색에 쓰는 KG 성격의 신호는 **Layer 2 canonical facts → Layer 2.5 wrapped signals → Layer 3 promoted serving profile**에서 나온다.
3. Product master truth는 review-derived signal이 overwrite하면 안 된다.
4. reviewer proxy와 real user는 어떤 조인 경로로도 merge하면 안 된다.
5. `catalog_validation_signal`은 QA/debug 전용이다. candidate/scoring/standard explanation에는 들어가면 안 된다.
6. `signal_evidence`는 signal provenance의 정본이다.
7. Texture 같은 축은 **BEE_ATTR(상위 축) + KEYWORD(하위 구체 표현)** 2단 구조를 유지한다.
8. 변경 사항은 mockdata와 unit/integration test에 반드시 반영한다.

---

# P0 — 이번 사이클에서 가장 먼저 고쳐야 하는 항목

## P0-1. Product loader를 mock product truth와 1:1로 맞추고 family-level identity를 추천에 연결

### 왜 필요한가
현재 mock product schema는 `SALE_PRICE`, `MAIN_EFFECT`, `MAIN_INGREDIENT`, `COUNTRY_OF_ORIGIN`, `REPRESENTATIVE_PROD_CODE`, `REPRESENTATIVE_PROD_NAME`, `REVIEW_COUNT`, `REVIEW_SCORE`까지 제공한다. 최신 loader는 이 중 주요 truth 필드 상당수를 이미 매핑하고 있다. 하지만 **variant family를 실제 추천/개인화에서 거의 쓰지 않는다.** 즉 product truth ingestion은 richer해졌지만, user/rec/runtime이 family-level identity를 아직 활용하지 못한다.

이 문제를 해결하지 않으면:
- 같은 family의 다른 shade/volume를 이미 쓰는 사용자를 제대로 해석하지 못함
- novelty / repurchase / already-owned가 SKU 단위에서만 작동함
- 쿠션/립 같이 variant가 많은 카테고리에서 개인화가 약해짐

### 수정 대상 파일
- `src/loaders/product_loader.py`
- `src/ingest/product_ingest.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 필요 시: `sql/ddl_raw.sql`, `sql/ddl_mart.sql`
- 테스트: `tests/test_product_loader.py`, `tests/test_candidate_generator.py`, `tests/test_recommendation.py`

### 변경 내용
1. `product_loader.py`
   - 아래 필드를 ProductRecord에 **반드시** 채운다.
     - `SALE_PRICE -> price`
     - `MAIN_INGREDIENT -> ingredients`
     - `MAIN_EFFECT -> main_benefits`
     - `COUNTRY_OF_ORIGIN -> country_of_origin`
     - `REPRESENTATIVE_PROD_CODE -> variant_family_id`
     - `REPRESENTATIVE_PROD_NAME -> attrs._es_meta.representative_prod_name`
     - `REVIEW_COUNT`, `REVIEW_SCORE`는 attrs/meta로 보존
   - 값 누락 시 `None`/빈 배열 fallback은 유지하되, **mockdata에서 제공되는 값은 실제로 소화**되게 한다.

2. `product_ingest.py`
   - `variant_family_id`가 있으면 product master에 정식 컬럼/attrs로 보존
   - family-level concept 또는 entity link 전략 중 하나를 결정한다.
     - 권장: family는 아직 별도 concept type로 올리지 말고, `product_master.variant_family_id` + optional family entity 정도로 시작
   - main_benefit concept seeding은 계속 유지

3. `personal_agent_adapter.py`
   - purchase-derived feature에서 `owned_family_ids`, `repurchased_family_ids`를 받을 수 있도록 확장
   - 아직 upstream이 못 준다면 빈 배열 허용

4. `build_serving_views.py`
   - `serving_product_profile`에 아래를 추가
     - `variant_family_id`
   - `serving_user_profile`에 아래를 추가
     - `owned_family_ids`
     - `repurchased_family_ids`

5. `candidate_generator.py`
   - `owned_family_ids`와 product `variant_family_id`를 비교해
     - exact same product는 already_owned
     - same family는 owned_family_match 플래그
   - compare/novelty 정책에서 same family를 별도 해석

6. `scorer.py`
   - feature 추가
     - `owned_family_penalty` 또는 `owned_family_bonus` (mode/config 기반)
     - `repurchase_family_affinity`
   - exact owned product와 same family를 구분해 점수에 반영

### Acceptance Criteria
- mock product JSON에서 `SALE_PRICE`, `MAIN_EFFECT`, `MAIN_INGREDIENT`, `COUNTRY_OF_ORIGIN`, `REPRESENTATIVE_PROD_CODE`가 실제 ProductRecord와 serving profile에 반영된다.
- user profile이 family-level 소유/재구매 힌트를 가지면 추천에서 same-family 판단이 가능하다.
- exact same product와 same family가 scorer에서 다르게 해석된다.

### 테스트 항목
- `tests/test_product_loader.py`
  - ES mock row 1건을 넣으면 price/ingredients/main_benefits/country/variant_family_id가 정확히 채워지는지
- `tests/test_family_level_personalization.py` 신규
  - exact owned product는 stronger penalty
  - same family different SKU는 weaker penalty or affinity
  - no family info면 graceful fallback

---

## P0-2. Texture 축을 shared taxonomy로 정리하고 user/review 양쪽에서 동일 dictionary를 쓰게 만들기

### 왜 필요한가
현재 방향은 맞다. `preferred_texture`는 최신 adapter에서 **상위 `PREFERS_BEE_ATTR(Texture)` + 하위 `PREFERS_KEYWORD(GelLike / LightLotionLike …)`**로 같이 올린다. 이 해석은 정확하다.

문제는 이 mapping이 **adapter 내부 하드코드 `_TEXTURE_KEYWORD_MAP`** 에 묶여 있다는 점이다. 리뷰 쪽 keyword normalization과 user 쪽 texture normalization이 다른 사전으로 진화하면, user와 review가 shared concept plane에서 만나지 못한다.

### 수정 대상 파일
- `src/user/adapters/personal_agent_adapter.py`
- `src/normalize/keyword_normalizer.py`
- `src/normalize/bee_normalizer.py`
- `configs/keyword_surface_map.yaml`
- 신규(권장): `configs/texture_keyword_map.yaml`
- 필요 시: `src/common/config_loader.py`
- 테스트: `tests/test_texture_taxonomy_alignment.py`

### 변경 내용
1. texture 정규화 하드코드를 config로 뺀다.
   - 권장 신규 파일: `configs/texture_keyword_map.yaml`
   - 예시:
     ```yaml
     Texture:
       젤: GelLike
       젤타입: GelLike
       가벼운로션: LightLotionLike
       가벼운 로션: LightLotionLike
       워터리: WateryLike
       크리미: CreamyLike
     ```

2. `personal_agent_adapter.py`
   - `_TEXTURE_KEYWORD_MAP` 제거 또는 fallback용으로만 남김
   - config loader를 통해 texture map을 불러와 사용
   - user texture 처리 규칙 명시:
     - 상위 attr: 항상 `PREFERS_BEE_ATTR(Texture)` 1회
     - 하위 keyword: 각 texture를 canonical keyword로 매핑하여 `PREFERS_KEYWORD` 생성

3. `keyword_normalizer.py` / `bee_normalizer.py`
   - 리뷰 쪽에서도 texture 관련 surface form이 동일 canonical keyword로 정규화되도록 보장
   - 즉 `젤`, `젤 타입`, `가벼운 로션`, `워터리` 등 리뷰와 user가 같은 keyword ID를 쓰게 한다.

4. scoring 원칙 유지
   - keyword 우선, residual bee_attr backoff
   - double count 방지 유지

5. explainer 원칙 강화
   - explanation 시 texture 관련 문구는
     - 상위 attr(제형)
     - 하위 keyword(젤/가벼운 로션 등)
     를 함께 보여준다.

### Acceptance Criteria
- user `preferred_texture`와 review/product 쪽 texture keyword가 동일 canonical keyword로 만난다.
- Texture는 BEE_ATTR 축으로 남고, GelLike/LightLotionLike 등은 KEYWORD로 남는다.
- adapter 하드코드 없이 config만 바꿔도 새 texture 표현을 추가할 수 있다.

### 테스트 항목
- `tests/test_texture_taxonomy_alignment.py` 신규
  - user `preferred_texture=["젤", "가벼운 로션"]` → `PREFERS_BEE_ATTR(Texture)` + `PREFERS_KEYWORD(GelLike, LightLotionLike)` 생성
  - review BEE/keyword normalization도 같은 keyword ID로 맞춰지는지
  - explanation에 “제형 축 / 젤 계열” 같이 상·하위가 함께 나오는지

---

## P0-3. Promoted-only serving 정책을 모든 downstream 경로에서 강제

### 왜 필요한가
지금 프로젝트의 핵심 방향은 **review evidence graph는 evidence-only**, **serving graph는 corpus-promoted signal만 사용**이다. README도 그렇게 정의하고 있고, `aggregate_product_signals.py`는 `review_count >= 3`, `confidence >= 0.6`, `synthetic_ratio <= 0.5`를 promotion gate로 계산한다.

하지만 이 철학이 추천/탐색/프로필/디버그/export 경로 전반에 일관되게 강제돼야 한다. `build_serving_product_profile()` 기본 경로는 `promoted_only=True`를 쓰지만, 다른 path가 이 원칙을 우회하면 구조가 다시 흔들린다.

### 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- 필요 시: `src/web/*`, `src/graph/*`, analyst/debug export 코드
- 테스트: `tests/test_promoted_only_enforcement.py`

### 변경 내용
1. `build_serving_views.py`
   - 현재 `promoted_only=True`를 유지하되, 문서/주석에 **standard serving contract**임을 명시
   - standard profile 생성 함수는 promoted-only가 기본이고, debug mode만 non-promoted 허용

2. `candidate_generator.py`
   - 입력으로 받는 `product_profiles`가 standard serving profile임을 전제
   - fallback/debug 경로에서 raw agg rows를 직접 먹지 않게 한다.

3. `scorer.py`
   - promoted-only serving profile을 전제로 계산
   - review_count_all 등 truth-like aggregate 메타를 쓸 수는 있지만, non-promoted signal을 직접 참조하지 않도록 주의

4. `explainer.py`
   - 기본 explain path는 promoted signal만 사용
   - evidence retrieval은 promoted signal에 연결된 fact/provenance만 추적
   - debug/QA explain은 별도 mode로만 non-promoted evidence 허용

5. debug/export/API path 점검
   - standard API/serving path는 promoted-only
   - analyst/debug export만 explicit flag로 non-promoted 허용

### Acceptance Criteria
- standard recommendation path는 non-promoted signal을 사용하지 않는다.
- debug/export가 non-promoted를 보여줄 수 있어도, standard serving contract와 명확히 분리된다.
- promoted gate가 recommendation runtime 전체에서 일관되게 작동한다.

### 테스트 항목
- `tests/test_promoted_only_enforcement.py` 신규
  - promoted=False signal만 있는 product는 standard profile에서 top signal로 노출되지 않음
  - debug mode에서는 보일 수 있음
  - candidate/scorer/explainer 기본 경로에서 non-promoted를 사용하지 않음

---

## P0-4. user raw schema와 normalized schema의 계약을 코드/문서로 명확히 고정

### 왜 필요한가
mock README는 raw 7-column profile과 normalized 3-group profile을 모두 설명하지만, 현재 공식 loader path는 normalized만 입력으로 받는다. 이건 나쁜 건 아니지만, 새 협업자가 raw JSON을 바로 넣을 수 있다고 오해하면 바로 막힌다.

### 수정 대상 파일
- `src/loaders/user_loader.py`
- `mockdata/README.md`
- 신규(권장): `src/loaders/user_raw_loader.py` 또는 `src/user/normalize_raw_profile.py`
- 테스트: `tests/test_user_loader_contract.py`

### 변경 내용
선택지는 둘 중 하나다.

#### 권장안 A
- 현재 공식 입력 계약은 **normalized only**라고 문서/코드에서 더 강하게 고정
- `user_loader.py` docstring, README, mock README에 명시
- raw mock은 reference-only fixture로 둔다

#### 권장안 B
- `user_raw_loader.py` 또는 `normalize_raw_profile.py`를 추가해
  - raw 7-column profile → normalized 3-group profile transformer 제공
- 이후 `load_users_from_profiles()`에 연결 가능

현재 scope를 고려하면 **A를 먼저 하고, B는 다음 사이클**이 현실적이다.

### Acceptance Criteria
- 신규 개발자가 어떤 user mock이 공식 입력인지 바로 알 수 있다.
- 잘못된 raw 입력을 공식 loader에 넣었을 때 명시적 에러가 난다.
- raw schema는 reference 또는 future transformer source라는 점이 분명하다.

### 테스트 항목
- `tests/test_user_loader_contract.py` 신규
  - normalized profile은 정상 ingest
  - raw profile 직접 입력 시 명시적 validation error
  - README/mock README 예시와 실제 loader 계약이 일치

---

# P1 — 구조를 더 단단하게 만드는 개선

## P1-1. user aggregation weighting을 더 구조화 (recency / frequency / source weighting)

### 왜 필요한가
최신 `aggregate_user_preferences.py`는 이미 `base_confidence × frequency_factor × recency_factor × source_type_weight`를 사용한다. 이 방향은 맞다. 다만 지금은 이 weighting model이 product-side aggregate만큼 정식 계약처럼 다뤄지지는 않는다. user preference가 개인화 품질의 핵심인 만큼, **어떤 source가 얼마나 강한지, 얼마나 빨리 decay하는지**를 더 명시적으로 가져가야 한다.

### 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `configs/scoring_weights.yaml` 또는 신규 `configs/user_weighting.yaml`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 테스트: `tests/test_user_weighting_model.py`

### 변경 내용
1. user weighting config 분리
   - `source_type_weight` (purchase/chat/basic/inferred)
   - `recency_decay`
   - `frequency_cap`
   - optional explicitness boost
2. aggregate 결과에 아래 메타를 남긴다.
   - `support_count`
   - `last_seen_at`
   - `source_types`
   - `weight_explanation`
3. scorer는 중요 user signals에 대해 weight provenance를 설명 가능하게 한다.

### Acceptance Criteria
- purchase 기반 선호가 chat 약선호보다 항상 더 강하게 반영된다.
- 오래된 선호는 recency에 따라 감쇠된다.
- aggregate 결과를 보고 “왜 이 weight가 나왔는지” 설명할 수 있다.

### 테스트 항목
- `tests/test_user_weighting_model.py` 신규
  - purchase > chat > inferred
  - recency decay 확인
  - frequency saturation/cap 확인

---

## P1-2. co-used product / tool signal을 실제 candidate/scorer feature로 연결

### 왜 필요한가
최신 serving product profile에는 `top_tool_ids`와 co-use product 축이 살아 있다. 그런데 recommendation runtime은 아직 brand/category/keyword/BEE_ATTR/context/concern/goal 중심이고, routine/bundle 추천 잠재력을 fully 활용하지 않는다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/mart/build_serving_views.py`
- 필요 시: `configs/scoring_weights.yaml`
- 테스트: `tests/test_coused_product_and_tool_features.py`

### 변경 내용
1. candidate generator
   - mode가 `COMPARE`이거나 routine recommendation context일 때
     - `top_coused_product_ids`
     - `top_tool_ids`
     를 후보 확장/가산에 활용할 옵션 추가
2. scorer
   - feature 추가
     - `coused_product_bonus`
     - `tool_alignment_score`
   - 단, default weight는 낮게 시작
3. explainer
   - “이 제품은 퍼프와 함께 자주 언급됩니다” / “이 제품은 OO 제품과 함께 쓰는 패턴이 많습니다” 같은 설명축 추가

### Acceptance Criteria
- co-use product와 tool signal이 최소 하나의 recommendation mode에서 실제 feature로 사용된다.
- 기본 mode에서 과도한 영향 없이 optional boost로 동작한다.
- explanation에 routine/bundle 이유를 노출할 수 있다.

### 테스트 항목
- `tests/test_coused_product_and_tool_features.py` 신규
  - co-use overlap이 있을 때 점수 상승
  - tool alignment 있을 때 점수 상승
  - default mode에서 영향이 제한적임

---

## P1-3. provenance 모델을 review 전용에서 범용으로 조금 더 일반화

### 왜 필요한가
현재 provenance는 review fact에는 잘 맞는다. 하지만 product truth fact, user fact, manual fact까지 Layer 2에 같이 두려면 `fact_provenance`를 더 범용적으로 보는 게 낫다.

### 수정 대상 파일
- `sql/ddl_canonical.sql`
- `src/db/repos/provenance_repo.py`
- `src/canonical/canonical_fact_builder.py`
- `src/user/canonicalize_user_facts.py`
- `src/ingest/product_ingest.py`
- 테스트: `tests/test_generic_provenance_model.py`

### 변경 내용
`fact_provenance`에 아래 컬럼을 추가하거나 의미를 확장:
- `source_domain` (`review|user|product|manual|system`)
- `source_kind` (`raw|summary|master|derived`)
- `source_table`
- `source_row_id`
- snippet/offset은 review provenance에만 필수

### Acceptance Criteria
- review/user/product/master fact가 모두 provenance로 역추적 가능하다.
- explanation은 review fact에는 snippet을, product/user fact에는 source summary를 돌려준다.

### 테스트 항목
- `tests/test_generic_provenance_model.py`
  - review fact provenance
  - user fact provenance
  - product master provenance

---

## P1-4. review source 계약을 `review_triples_raw`와 `rs.jsonl` 양쪽으로 명확히 분리

### 왜 필요한가
mock README에는 `review_triples_raw.json`과 `review_rs_samples.json`이 모두 있고, `SCHEMA_RS_JSONL.md`도 들어왔다. 이건 아주 좋은 자산이다. 다만 현재 공식 loader path는 `review_triples_raw.json` 계약 중심이고, `rs.jsonl`은 reference-only에 가깝다. 운영 소스가 rs.jsonl이라면, 중간 변환기를 repo 안에 first-class로 두는 편이 schema drift를 막는다.

### 수정 대상 파일
- `src/loaders/relation_loader.py`
- 신규(권장): `src/loaders/rs_jsonl_loader.py`
- `mockdata/README.md`
- 테스트: `tests/test_rs_jsonl_transform.py`

### 변경 내용
1. `rs_jsonl_loader.py` 추가
   - S3 `rs.jsonl` sample row → `RawReviewRecord`로 변환
   - `source_review_key`, `author_key`, `source_product_id`를 보존
2. `relation_loader.py`는 기존 `review_triples_raw` 계약 유지
3. 문서에 두 입력의 역할 분리 명시
   - triples_raw = normalized review extraction fixture
   - rs_jsonl = raw operational source fixture

### Acceptance Criteria
- rs.jsonl mock row를 RawReviewRecord로 안정적으로 변환할 수 있다.
- stable review key / author key / source product id를 파이프라인에 전달할 수 있다.
- review_triples_raw와 rs_jsonl이 서로 다른 layer의 fixture라는 점이 명확하다.

### 테스트 항목
- `tests/test_rs_jsonl_transform.py` 신규
  - rs row → RawReviewRecord 변환
  - stable review_id / reviewer_proxy 사용
  - channel-specific optional fields 보존

---

# P2 — 운영성 / 문서 / 테스트 자산 강화

## P2-1. `shared_entities.json`, `review_kg_output.json`을 회귀 테스트 자산으로 승격

### 왜 필요한가
mockdata에 이미 cross-reference anchor와 KG output 참조가 들어왔지만, 현재는 주로 문서 자산에 가깝다. 이걸 실제 regression fixture로 쓰면 review KG / adapter / concept join 안정성을 더 강하게 검증할 수 있다.

### 수정 대상 파일
- 신규 테스트:
  - `tests/test_mock_integrity.py`
  - `tests/test_mock_review_kg_regression.py`
- 필요 시: `src/kg/adapter.py`, `src/kg/kg_pipeline.py`

### 변경 내용
1. `shared_entities.json`을 사용해서
   - product / user / review mock 간 ID consistency를 검증
2. `review_kg_output.json`을 gold-ish fixture로 사용해서
   - evidence demotion
   - synthetic confidence range
   - adapter output shape
   regression 확인

### Acceptance Criteria
- mock assets가 단순 문서가 아니라 실제 regression test fixture로 사용된다.
- review KG pipeline의 evidence-kind / confidence policy가 깨지면 테스트가 바로 실패한다.

### 테스트 항목
- `tests/test_mock_integrity.py`
- `tests/test_mock_review_kg_regression.py`

---

## P2-2. runtime 경로를 SQL-first 방향으로 더 밀기 위한 준비

### 왜 필요한가
현재 generate path는 점점 좋아지고 있지만, 기본 경로는 아직 Python loop 비중이 높다. 장기적으로 catalog가 커질수록 SQL prefilter / batch aggregate / thin Python scorer 구조가 유리하다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/db/repos/mart_repo.py`
- 신규(권장): `sql/candidate_queries.sql`
- 테스트: `tests/test_candidate_prefilter_sql_logic.py`

### 변경 내용
1. candidate SQL prefilter path를 공식 경로로 승격
2. Python loop path는 debug/fallback로 유지
3. mart repo에 batch aggregate query hook 추가

### Acceptance Criteria
- standard path가 SQL prefilter를 우선 사용한다.
- fallback Python path는 debug/테스트 용도로만 남는다.

### 테스트 항목
- `tests/test_candidate_prefilter_sql_logic.py`
- `tests/test_batch_aggregate_consistency.py`

---

# 큰 방향성 정렬 확인 (절대 벗어나지 말 것)

1. **리뷰 그래프는 정본 전역 KG가 아니라 evidence graph**다.
2. 네가 원하는 진짜 “전체 리뷰 KG”는 Layer 2 canonical facts + Layer 2.5 wrapped signals + Layer 3 promoted serving signals에서 형성된다.
3. 따라서 per-review KG를 더 화려하게 만드는 것보다, **corpus promotion과 serving enforcement를 더 강하게 만드는 것**이 우선이다.
4. Texture처럼 “속성 축이면서 구체 표현이 있는 것”은 반드시
   - 상위 BEE_ATTR
   - 하위 KEYWORD
   2단으로 유지한다.
5. Product truth는 점점 더 풍부하게 쓰되, review-derived validation이 절대 truth를 덮지 않게 한다.
6. User personalization은
   - 상태(state)
   - 선호(preference)
   - 금기(avoid)
   - 목표(goal)
   - 맥락(context)
   - 행동(behavior/purchase)
   을 함께 보되, source/recency/frequency weighting을 명시적으로 유지한다.

---

# 권장 작업 순서

## 이번 사이클에서 바로
1. P0-1 product loader truth/family 연결
2. P0-2 texture taxonomy shared config화
3. P0-3 promoted-only serving enforcement 전 경로 점검
4. P0-4 user input contract 정리

## 다음 사이클
5. P1-1 user weighting 고도화
6. P1-2 co-used product / tool feature 활용
7. P1-3 generic provenance
8. P1-4 rs.jsonl first-class loader

## 여유 있을 때
9. P2-1 mock regression tests
10. P2-2 SQL-first runtime 강화

---

# 최종 완료 기준

이번 후속 수정 사이클이 완료됐다고 보기 위한 기준은 아래다.

- mock product schema에서 제공되는 truth 필드가 loader → ingest → serving까지 실제로 반영된다.
- user texture preference가 BEE_ATTR(Texture) + KEYWORD(GelLike...)로 일관되게 정규화된다.
- same-family ownership/repurchase가 추천에서 실제로 구분되어 반영된다.
- standard serving path는 promoted signal만 사용한다.
- normalized user profile이 공식 입력 계약임이 문서와 loader에 명확하다.
- rs.jsonl raw source도 first-class transform 경로를 가진다.
- mock assets가 실제 regression 테스트 자산으로 승격된다.
- 추천/탐색/설명은 product truth + promoted review signals + user weighted preferences를 함께 사용한다.
