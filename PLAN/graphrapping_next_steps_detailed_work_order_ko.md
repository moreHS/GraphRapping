# GraphRapping — 다음 단계 상세 작업 지시서 (최신 main 기준)

작성 목적:
- 최신 main + mockdata 상태를 기준으로, 이미 반영된 구조를 존중하면서
  **추천/개인화/탐색 활용도를 높이는 다음 단계 작업**을 Claude Code에 파일 단위로 지시하기 위함.
- 이번 문서는 **대규모 재설계 금지**가 원칙이다.
- 기존 방향(증거 그래프 → canonical fact → promoted signal → personalized recommendations)은 유지한다.

---

## 0. 현재 상태 진단 요약

최신 main 기준으로 이미 잘 되어 있는 것:
- product loader가 mock/ES truth에서 `SALE_PRICE`, `MAIN_INGREDIENT`, `MAIN_EFFECT`, `COUNTRY_OF_ORIGIN`, `REPRESENTATIVE_PROD_CODE`를 실제 ProductRecord로 매핑함
- user adapter가 `preferred_texture`를 상위 `Texture` 축(BEE_ATTR) + 하위 texture keyword로 같이 올림
- `OWNS_PRODUCT`, `OWNS_FAMILY`, `REPURCHASES_BRAND`, `REPURCHASES_CATEGORY`, `REPURCHASES_FAMILY`가 분리됨
- serving product profile은 `promoted_only=True` 기본값을 가지며 non-promoted와 `CATALOG_VALIDATION_SIGNAL`을 추천 프로필에서 제외함
- candidate/scorer는 goal, skin_type, purchase loyalty, family affinity, tool alignment까지 일부 활용함
- rs.jsonl loader가 실제 운영 원본 형식을 RawReviewRecord로 변환할 수 있음

현재 남은 핵심 과제:
1. **Product family / variant 활용 강화**
2. **Texture taxonomy를 user/review 공통 authoritative source로 통일**
3. **rs.jsonl raw source를 1급 ingest 경로로 승격**
4. **shared_entities / review_kg_output을 regression fixture로 승격**
5. **candidate/aggregate를 더 SQL-first로 이동**
6. **provenance 캐시 필드 정리 및 정본 경로 고정**
7. **promoted-only 정책을 모든 serving/export/API 경로에서 일관되게 강제**

---

## 1. 작업 원칙 (절대 불변)

1. Product master truth는 review-derived signal이 절대 overwrite 하면 안 된다.
2. Review evidence graph는 계속 evidence-only다. 전역 KG 정본은 Layer 2/2.5/3에서 재구성한다.
3. Layer 2 canonical fact semantics는 깨지면 안 된다.
4. Layer 3 signal은 projection registry 외 경로로 생성하면 안 된다.
5. reviewer proxy와 real user는 어떤 조인 경로로도 merge하면 안 된다.
6. `signal_evidence`는 explanation provenance의 source of truth다.
7. `Texture`는 BEE_ATTR 축이고, `GelLike/LightLotionLike/...`는 그 아래 KEYWORD/descriptor다.
8. 모든 신규 기능은 idempotent / late-arrival / tombstone을 고려해야 한다.

---

## 2. 우선순위

- **P0**: 추천 활용도/정확도를 즉시 올리는 구조 작업
- **P1**: 운영성/성능/계약 강화
- **P2**: 문서화/개발자 경험/테스트 자산화

---

# P0-1. Product family / variant를 1급 personalization unit으로 승격

## 목적
현재 product loader는 `REPRESENTATIVE_PROD_CODE -> variant_family_id`를 product truth에 넣고 있고,
user profile도 `owned_family_ids`, `repurchased_family_ids`를 들고 있다.
하지만 recommendation runtime은 family를 주로 suppression/affinity 보조 축으로만 본다.

뷰티 도메인(특히 쿠션, 립, 스킨케어 라인)에서는 **exact SKU보다 family가 더 안정적인 개인화 단위**다.
다음 단계는 family를 1급 personalization unit으로 끌어올리는 것이다.

## 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/mart/build_serving_views.py`
- `src/loaders/product_loader.py`
- 필요 시: `sql/views_serving.sql` 또는 serving materialization 관련 코드
- 테스트:
  - `tests/test_candidate_generator.py`
  - `tests/test_recommendation.py`
  - 신규 `tests/test_family_personalization.py`

## 변경 내용

### A. Candidate generator
1. family-aware prefilter를 추가:
   - `already_owned exact SKU`
   - `owned same family`
   - `repurchased family`
   를 명시적으로 구분
2. `RecommendationMode`별 정책
   - `STRICT`: exact owned SKU는 기본 suppress, owned family는 soft suppress 또는 candidate 남기되 penalty
   - `EXPLORE`: exact owned SKU는 낮게, owned family의 다른 variant는 적극 허용
   - `COMPARE`: family neighbor / co-use / comparison product도 허용
3. candidate object에 아래 필드 추가 검토:
   - `same_family_exact_variant: bool`
   - `same_family_other_variant: bool`
   - `repurchased_family_match: bool`

### B. Scorer
1. 기존 `owned_family_penalty`, `repurchase_family_affinity`를 유지하되,
   exact SKU / same family-other variant를 분리된 가중치로 처리
2. 신규 feature 예시:
   - `exact_owned_penalty`
   - `same_family_explore_bonus`
   - `repurchase_family_affinity`
3. `novelty_bonus`와 충돌하지 않게 우선순위 문서화

### C. Serving profile
1. `serving_product_profile`에 family 관련 필드를 더 명시적으로 추가 검토:
   - `variant_family_id`
   - `representative_product_name`
   - optional: `family_review_count`, `family_signal_density`
2. `serving_user_profile`에 이미 있는
   - `owned_family_ids`
   - `repurchased_family_ids`
   를 추천기와 설명기에서 직접 쓰는 계약을 주석으로 못 박기

## Acceptance Criteria
- same exact SKU와 same family other variant가 추천기에서 다른 정책으로 처리된다.
- family match가 explanation에 명시적으로 드러난다.
- repurchased family를 가진 product는 동일 family의 신규 variant 탐색이 가능해진다.

## 테스트 항목
- `test_family_personalization.py`
  - 같은 exact SKU는 STRICT에서 suppress
  - 같은 family 다른 variant는 EXPLORE에서 통과
  - repurchased family가 있으면 score 상승
  - explanation에 family-related reason이 포함됨

---

# P0-2. Texture taxonomy를 review/user 공통 authoritative source로 통일

## 목적
현재 user adapter는 `texture_keyword_map.yaml`을 읽어
- 상위 `PREFERS_BEE_ATTR(Texture)`
- 하위 `PREFERS_KEYWORD(GelLike / LightLotionLike / WateryLike ...)`
를 같이 생성한다.

이 방향은 맞다.
하지만 review-side normalization이 같은 taxonomy file을 authoritative source로 쓰지 않으면,
장기적으로 user와 review가 다른 keyword space를 갖게 된다.

즉 목표는:
- `Texture` = 상위 축 (BEE_ATTR)
- `GelLike`, `LightLotionLike`, `WateryLike`, `RichCreamLike` = 하위 KEYWORD
를 **user와 review에서 동일한 정규 사전**으로 강제하는 것.

## 수정 대상 파일
- `configs/texture_keyword_map.yaml`
- `src/normalize/bee_normalizer.py`
- `src/normalize/keyword_normalizer.py` (있다면)
- `src/user/adapters/personal_agent_adapter.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- 테스트:
  - 신규 `tests/test_texture_taxonomy_alignment.py`
  - 기존 `tests/test_bee_normalizer.py`
  - 기존 `tests/test_recommendation.py`

## 변경 내용

### A. Config를 authoritative source로 고정
1. `texture_keyword_map.yaml`에 아래를 명시적으로 유지/확장:
   - `texture_axis: Texture`
   - `surface_to_keyword`
   - optional `keyword_to_parent_attr`
2. 사용자용/리뷰용 별도 hardcode 금지

### B. Review-side normalization
1. `bee_normalizer.py`에서 texture 계열 phrase를 만나면:
   - 상위 attr = `Texture`
   - 하위 keyword = yaml 기반 normalized keyword
   를 같이 생성
2. review side keyword 생성이 texture map을 우선 사용하도록 수정
3. fallback auto keyword는 texture 계열에서는 사용하지 않거나 매우 보수적으로만 사용

### C. User-side normalization
1. 현재 adapter 로직 유지
2. 다만 map version/hash를 optional meta로 남겨 drift 추적 가능하게 검토

### D. Scoring / Explanation
1. scorer는 현재 방향 유지:
   - keyword match 우선
   - residual attr match backoff
2. explanation은 아래 형태로 출력 가능해야 함:
   - "제형(Texture) 축에서"
   - "젤(GelLike) / 가벼운 로션(LightLotionLike) 계열 신호가 강합니다"

## Acceptance Criteria
- user `preferred_texture=[젤]`와 review-side `젤 타입`, `젤처럼`, `젤형 제형`이 모두 같은 `Texture + GelLike` 구조로 정규화된다.
- texture 관련 추천은 attr만이 아니라 keyword 수준 설명이 가능하다.
- user/review에서 같은 texture taxonomy version을 사용한다.

## 테스트 항목
- `test_texture_taxonomy_alignment.py`
  - user "젤" → `Texture + GelLike`
  - review phrase "젤 타입" → `Texture + GelLike`
  - review phrase "가벼운 로션 느낌" → `Texture + LightLotionLike`
  - explanation에 attr + keyword 동시 노출

---

# P0-3. promoted-only 정책을 serving/export/API 전체에 일관 적용

## 목적
현재 `build_serving_product_profile()`는 `promoted_only=True`를 기본값으로 두고,
non-promoted 및 `CATALOG_VALIDATION_SIGNAL`을 추천 프로필에서 제거한다.
이건 맞다.

다만 이 정책이 API/debug/export/analyst path 전반에 동일하게 적용되는지 더 명시할 필요가 있다.
너의 목표는 review evidence를 그대로 추천에 쓰는 것이 아니라,
**코퍼스에서 승격된 신호만 serving에 쓰는 것**이기 때문이다.

## 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/web/server.py` 또는 serving/export endpoint 관련 파일
- `src/db/repos/mart_repo.py`
- 필요 시: `src/rec/explainer.py`
- 테스트:
  - 기존 `tests/test_truth_override_protection.py`
  - 신규 `tests/test_promoted_only_contract.py`

## 변경 내용
1. serving profile 생성 경로에서 `promoted_only=True`를 표준으로 고정
2. debug/analyst path에서만 `promoted_only=False` 허용
3. API 응답/추천 입력으로 쓰는 product profile은 모두 promoted-only contract를 따른다고 문서화
4. `CATALOG_VALIDATION_SIGNAL`은 serving/debug 분기 명시
   - standard recommendation path: 제외
   - QA/debug path: optional 노출

## Acceptance Criteria
- 추천/탐색/설명 기본 경로에서는 non-promoted signal이 노출되지 않는다.
- promoted gate를 통과하지 못한 signal은 debug path에서만 볼 수 있다.
- `CATALOG_VALIDATION_SIGNAL`은 추천 점수와 overlap 계산에서 완전히 제외된다.

## 테스트 항목
- `test_promoted_only_contract.py`
  - promoted_only=True 프로필엔 non-promoted 없음
  - promoted_only=False debug 프로필엔 non-promoted 존재 가능
  - catalog_validation signal은 standard profile에 없음

---

# P1-1. rs.jsonl raw source를 first-class ingest 경로로 승격

## 목적
mockdata는 이제 `review_triples_raw.json`뿐 아니라 `review_rs_samples.json`과 `SCHEMA_RS_JSONL.md`를 제공한다.
README도 운영 원본 소스는 rs.jsonl이라고 설명한다.

현재 `rs_jsonl_loader.py`는 존재하지만, 이를 1급 ingest 계약으로 더 올려야 production-like 검증이 가능하다.

## 수정 대상 파일
- `src/loaders/rs_jsonl_loader.py`
- `src/loaders/relation_loader.py`
- `src/jobs/run_daily_pipeline.py`
- 필요 시: 신규 CLI/entrypoint (`src/jobs/run_rs_ingest.py` 등)
- `mockdata/README.md`
- 테스트:
  - 신규 `tests/test_rs_jsonl_loader.py`
  - 신규 `tests/test_rs_ingest_smoke.py`

## 변경 내용
1. `rs_jsonl_loader.py`를 공식 운영 원본 ingest 경로로 문서화
2. CLI or batch helper 추가:
   - `load_reviews_from_rs_jsonl(path)`를 바로 run path에 연결
3. `source_review_key = id`, `author_key = _build_author_key(record)` 경로를 유지하고,
   stable key fallback 정책 문서화
4. `relation_loader.py`는 “이미 canonicalized relation[]를 받은 Relation-project JSON” 전용 경로로 위치를 더 명확히 한다.

## Acceptance Criteria
- rs.jsonl input으로도 batch pipeline을 직접 실행할 수 있다.
- rs.jsonl loader와 relation loader의 역할이 문서/코드에서 혼동되지 않는다.
- source_review_key / author_key가 idempotency와 reviewer_proxy 안정성에 기여한다.

## 테스트 항목
- `test_rs_jsonl_loader.py`
  - mock rs sample 파싱
  - source_review_key / author_key 존재 확인
  - NER/BEE/REL mapping 확인
- `test_rs_ingest_smoke.py`
  - rs sample → batch run → non-empty artifacts

---

# P1-2. mock assets를 regression fixture로 승격

## 목적
`shared_entities.json`과 `review_kg_output.json`은 지금 문서 자산으로만 보기엔 너무 좋다.
- `shared_entities.json`은 교차 참조 앵커
- `review_kg_output.json`은 evidence graph 출력 참조

이걸 regression fixture로 쓰면 schema drift와 graph/evidence drift를 막을 수 있다.

## 수정 대상 파일
- `tests/test_mock_integrity.py`
- `tests/test_mock_review_kg_regression.py`
- 필요 시 신규 fixture helper
- `mockdata/README.md`

## 변경 내용
1. `test_mock_integrity.py`에서 아래 교차 규칙을 자동 검증
   - user_profiles 상품 코드 ⊂ product catalog `ONLINE_PROD_SERIAL_NUMBER`
   - 브랜드명 교차 일치
   - review `prod_nm` ↔ product `prd_nm` 퍼지 일치
   - user_profiles 카테고리 ⊂ product catalog category
2. `test_mock_review_kg_regression.py`에서
   - `review_kg_output.json`의 `evidence_kind` confidence band
   - entity/edge cardinality
   - synthetic/auto keyword demotion 계약
   을 회귀 테스트로 고정

## Acceptance Criteria
- mock assets가 단순 문서가 아니라 실제 regression gate로 작동한다.
- evidence graph 관련 구조 drift가 테스트에서 잡힌다.

## 테스트 항목
- 기존 테스트 확장
- confidence band mismatch, missing entity references, unexpected edge inflation 케이스 추가

---

# P1-3. aggregate / candidate runtime의 SQL-first 비중 확대

## 목적
현재 구조는 맞지만, runtime 기본 경로가 아직 Python loop에 기울어 있다.
다음 단계는 구조를 유지하면서 운영성을 올리는 것이다.

## 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/db/repos/mart_repo.py`
- `src/mart/aggregate_product_signals.py`
- 필요 시 신규 SQL helper/query module
- 테스트:
  - 신규 `tests/test_candidate_prefilter_sql_equivalence.py`
  - 신규 `tests/test_batch_aggregate_sql_equivalence.py`

## 변경 내용

### A. Candidate generation
1. `generate_candidates_prefiltered()`를 기본 경로로 승격 검토
2. SQL prefilter는 최소 아래를 포함
   - active product
   - category constraint
   - ingredient exclusion
   - price band (옵션)
   - availability (옵션)
3. Python 단계는 prefiltered 후보에 대해서만 overlap + rerank 수행

### B. Product aggregate
1. dirty product set에 대해 batch aggregate SQL/group-by path 추가
2. per-product full scan + Python aggregate는 fallback/debug로만 유지
3. `is_promoted` 계산이 batch path에서도 동일하게 재현되도록 보장

## Acceptance Criteria
- SQL prefilter 경로와 기존 Python 경로의 결과가 의미적으로 일치한다.
- dirty product batch aggregate 결과가 single product aggregate와 일치한다.
- catalog가 커져도 기본 경로는 batch/SQL을 우선 사용한다.

## 테스트 항목
- `test_candidate_prefilter_sql_equivalence.py`
- `test_batch_aggregate_sql_equivalence.py`
- strict/explore/compare 모드별 equivalence

---

# P1-4. user weighting model 추가 정교화

## 목적
지금 `aggregate_user_preferences.py`는 이미 `base_confidence × frequency_factor × recency_factor × source_type_weight`를 사용한다.
방향은 맞다.
다음 단계는 이 weighting이 실제 추천 feature와 더 자연스럽게 맞물리게 정교화하는 것이다.

## 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `src/rec/scorer.py`
- `configs/user_weighting.yaml` (신규 또는 확장)
- 테스트:
  - 신규 `tests/test_user_weighting_model.py`

## 변경 내용
1. `source_type_weight`를 config 기반으로 더 명확히 관리
   - purchase > chat > basic > inferred 유지
2. recency decay / frequency boost를 user feature family별로 다르게 줄지 검토
   - concern/goal은 slower decay
   - context/tool은 faster decay
3. scorer에서 user-derived weight를 더 직접적으로 활용
   - 현재는 overlap count 중심인데, user preference weight를 일부 반영

## Acceptance Criteria
- 동일 concept라도 source/recency/frequency에 따라 user weight가 달라진다.
- scorer가 user weight를 반영한 결과를 낸다.
- recent chat signal과 오래된 inferred signal의 영향이 구분된다.

## 테스트 항목
- `test_user_weighting_model.py`
  - purchase vs chat vs inferred 우선순위
  - recent vs stale preference 차등
  - weighted overlap이 score에 반영되는지

---

# P1-5. provenance SoT cleanup 마무리

## 목적
현재 방향은 이미 좋다.
- `signal_evidence` = provenance source of truth
- `source_fact_ids` = cache/debug

다만 cache field를 어떻게 장기적으로 관리할지 한 번 더 정리하면 좋다.

## 수정 대상 파일
- `src/wrap/signal_emitter.py`
- `src/db/repos/signal_repo.py`
- `src/rec/explainer.py`
- `src/db/repos/provenance_repo.py`
- `sql/ddl_signal.sql`
- 테스트:
  - 기존 provenance 관련 테스트 확장

## 변경 내용
1. `source_fact_ids`를 공식적으로 cache-only/deprecated로 주석 강화
2. aggregate/explainer가 provenance가 필요할 때는 `signal_evidence`만 읽게 정리
3. 캐시 field를 남긴다면 `signal_evidence`에서 재생성 가능해야 함

## Acceptance Criteria
- explanation path는 `signal_evidence -> fact_provenance -> raw`만으로 완결된다.
- `source_fact_ids`가 없어도 기능이 유지된다.

## 테스트 항목
- provenance chain regression tests 확장

---

# P2-1. raw user contract를 더 명확히 하거나 transformer 추가

## 목적
현재 mock README는 normalized 3-group가 공식 입력이고 raw 7-column은 reference-only라고 명시한다.
이건 괜찮다.
다만 production-like path를 더 선명히 하려면 raw→normalized transformer를 repo 안에 둘지 결정할 시점이다.

## 수정 대상 파일
- `src/loaders/user_loader.py`
- 필요 시 신규 `src/loaders/user_raw_loader.py`
- `mockdata/README.md`
- 테스트:
  - 신규 `tests/test_user_raw_transformer.py` (도입 시)

## 변경 내용
선택지 A (권장)
- 지금 계약 유지
- README/loader docstring에 normalized-only 공식 입력을 더 강하게 명시

선택지 B
- raw 7-column → normalized 3-group transformer 추가

## Acceptance Criteria
- 신규 참여자가 raw와 normalized의 역할을 오해하지 않는다.
- raw transformer를 넣는다면 normalized loader와 완전 호환된다.

---

# P2-2. repo hygiene / tooling / docs 마감

## 목적
루트 문서가 이미 좋아졌지만, 운영형 프로젝트로 가려면 tooling과 hygiene를 더 보완하는 게 좋다.

## 수정 대상 파일
- `.gitignore`
- `pyproject.toml`
- GitHub Actions workflow (신규)
- 필요 시 `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`

## 변경 내용
1. `__pycache__` 같은 산출물 추적 제거 확인
2. dev tooling 추가
   - `ruff`
   - `mypy`
   - `pytest-cov`
3. CI workflow 추가
   - lint
   - unit test
   - mock regression test

## Acceptance Criteria
- 새 PR에서 lint/test 자동 실행 가능
- repo에 불필요한 빌드 산출물이 남지 않는다.

---

## 권장 실행 순서

### 이번 사이클에서 먼저
1. P0-1 Product family personalization
2. P0-2 Shared texture taxonomy enforcement
3. P0-3 promoted-only contract hardening

### 다음 사이클
4. P1-1 rs.jsonl first-class ingest
5. P1-2 mock regression fixture 승격
6. P1-3 SQL-first runtime upgrade
7. P1-4 user weighting refinement
8. P1-5 provenance SoT cleanup

### 그 이후
9. P2-1 raw user transformer 여부 결정
10. P2-2 repo hygiene / tooling

---

## 최종 완료 기준

이번 후속 작업이 끝나면 아래가 성립해야 한다.

- Product family / variant가 추천/개인화의 1급 신호로 쓰인다.
- Texture는 user/review 모두 `Texture + keyword` 2단 구조로 같은 taxonomy를 쓴다.
- Standard serving path는 promoted-only signal만 사용한다.
- rs.jsonl raw source를 공식 ingest 경로로 취급할 수 있다.
- shared_entities / review_kg_output이 regression fixture로 작동한다.
- candidate/aggregate가 지금보다 더 SQL-first해진다.
- user weighting은 source/recency/frequency를 실제로 반영한다.
- provenance SoT는 `signal_evidence` 하나로 일관된다.
