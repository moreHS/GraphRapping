# GraphRapping 최신 레포 기준 후속 수정 지시서

작성 기준:
- GitHub `main` 최신 상태 재검토
- 최근 2개 커밋 확인 (`d32626e`, `a3db51e`)
- mockdata/README와 실제 loader/adapter/scorer/serving code 대조

## 0. 이번 검토에서 확인된 현 상태

### 이미 잘 반영된 것
- README 기준으로 프로젝트 방향은 `Reviews -> KG extraction -> canonical facts -> promoted signals -> personalized recommendations`로 정리되어 있고, evidence graph(per-review)와 serving graph(corpus-promoted)를 분리한다.
- `build_serving_product_profile()`는 기본적으로 `promoted_only=True`이며, `is_promoted=False` 신호와 `CATALOG_VALIDATION_SIGNAL`을 표준 serving profile에서 제외한다.
- `product_loader.py`는 `SALE_PRICE`, `MAIN_INGREDIENT`, `MAIN_EFFECT`, `COUNTRY_OF_ORIGIN`, `REPRESENTATIVE_PROD_CODE`를 실제 `ProductRecord`로 매핑한다.
- `personal_agent_adapter.py`는 `preferred_texture`를 상위 `Texture` 축(BEE_ATTR)과 하위 keyword로 같이 올리고, `OWNS_PRODUCT`를 entity ref로 분리한다.
- `candidate_generator.py` / `scorer.py`에는 exact owned, owned family, repurchased family, tool/co-use, goal, skin type, purchase loyalty 등의 feature가 실제 코드로 들어와 있다.

### 이번 검토에서 남은 핵심 이슈
1. **Family key 정규화 불일치**
   - `OWNS_FAMILY`는 product ref처럼 다루지만, `REPURCHASES_FAMILY`는 아직 `ConceptType.BRAND`로 저장된다.
   - product side `variant_family_id`는 raw family code이고, user side는 IRI/개념 ref가 섞일 수 있어 same-family logic이 조용히 miss 될 가능성이 있다.
2. **Texture taxonomy는 방향은 맞지만 정본(source of truth) 동기화가 약함**
   - `texture_keyword_map.yaml`과 `keyword_surface_map.yaml`의 texture 항목이 이중 관리될 위험이 있다.
3. **SQL-first runtime이 아직 부분 구현**
   - candidate prefilter 경로는 생겼지만 기본 candidate generation은 여전히 Python loop 중심이다.
   - aggregate도 batch SQL보다 Python recompute 경로 의존이 남아 있다.
4. **mock 자산을 regression fixture로 더 강하게 써야 함**
   - `shared_entities.json`, `review_kg_output.json`, `review_rs_samples.json`이 좋은 자산인데 테스트 활용도가 아직 낮다.
5. **rs.jsonl raw source를 first-class ingest path로 더 공식화할 필요**
   - 현재 loader는 존재하지만, 표준 로컬/배치 실행 경로에서 더 잘 드러나게 만들 필요가 있다.

---

## P0-1. Family-level personalization 정합성 마무리

### 목적
variant-heavy 뷰티 도메인에서 exact SKU, same family other variant, repurchased family를 확실히 구분해 추천 품질을 높인다.

### 문제 상세
- `product_loader.py`는 `REPRESENTATIVE_PROD_CODE -> variant_family_id`를 product truth로 넣는다.
- `candidate_generator.py`는 `variant_family_id`와 `owned_family_ids`를 직접 비교한다.
- 하지만 `personal_agent_adapter.py`는 `OWNS_FAMILY`를 product ref처럼 넣는 반면, `REPURCHASES_FAMILY`는 아직 `ConceptType.BRAND` 기반 `_make_pref(...)`로 저장한다.
- 결과적으로 family identity가 `raw family code / product ref / brand concept` 사이에서 섞여, same-family detection이 false negative를 낼 수 있다.

### 수정 대상 파일
- `src/user/adapters/personal_agent_adapter.py`
- `src/user/canonicalize_user_facts.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `tests/test_family_personalization.py` (신규)
- 필요 시 `src/common/ids.py`

### 구현 지시
1. **Family identity를 하나로 통일**
   - 권장: `product-family` entity ref 형태로 통일 (`product:{family_id}` 또는 별도 `family:{id}` 네임스페이스)
   - `variant_family_id`를 candidate/scorer 비교 전에 동일한 family ref 형태로 normalize
2. `REPURCHASES_FAMILY`는 더 이상 `ConceptType.BRAND`를 쓰지 말 것
   - `_make_product_ref(...)`와 유사한 family ref helper를 만들거나
   - 명시적 `object_ref_kind=ENTITY`, `concept_type=ProductFamily` 등으로 저장
3. `build_serving_views.py`에서 `owned_family_ids`, `repurchased_family_ids`를 모두 동일 key 타입으로 반환
4. `candidate_generator.py`
   - exact SKU owned
   - same family owned
   - repurchased family
   를 각각 별도 플래그로 유지
5. `scorer.py`
   - strict / explore / compare 모드별 규칙을 명시
   - 예시:
     - strict: exact owned 강한 penalty, owned family도 suppress 성향
     - explore: exact owned는 penalty 유지, same-family-other-variant는 explore bonus 허용
     - compare: family overlap을 neutral 또는 보조 feature로

### Acceptance Criteria
- exact SKU owned, same family other variant, repurchased family가 서로 다른 케이스로 분기된다.
- family mismatch 때문에 `owned_family_match` / `repurchased_family_match`가 false negative를 내지 않는다.
- `REPURCHASES_FAMILY`가 brand concept로 저장되지 않는다.

### 테스트 항목
- `tests/test_family_personalization.py`
  - case 1: exact owned SKU → `already_owned=True`
  - case 2: different SKU, same family → `owned_family_match=True`
  - case 3: repurchased family → `repurchased_family_match=True`
  - case 4: explore mode에서 same family other variant bonus 적용
  - case 5: strict mode에서 same family suppression 동작

---

## P0-2. Texture taxonomy를 user/review 공통 authoritative source로 고정

### 목적
`Texture`를 상위 BEE_ATTR 축, `GelLike / LightLotionLike / WateryLike ...`를 하위 KEYWORD로 보는 현재 방향을 user/review 양쪽에서 완전히 일관되게 유지한다.

### 문제 상세
- `texture_keyword_map.yaml`은 상위 `texture_axis: "Texture"`와 하위 surface→keyword 매핑을 정의한다.
- `personal_agent_adapter.py`는 이 map을 직접 사용해 user texture preference를 `PREFERS_BEE_ATTR(Texture)` + `PREFERS_KEYWORD(...)`로 올린다.
- 하지만 review-side normalization도 정확히 같은 map을 authoritative source로 쓰는지, `keyword_surface_map.yaml`의 texture 관련 항목과 drift가 없는지 더 강하게 보장할 필요가 있다.

### 수정 대상 파일
- `configs/texture_keyword_map.yaml`
- `configs/keyword_surface_map.yaml`
- `src/user/adapters/personal_agent_adapter.py`
- `src/normalize/bee_normalizer.py` 또는 `src/normalize/keyword_normalizer.py`
- `tests/test_texture_taxonomy_sync.py` (신규)

### 구현 지시
1. **정본 선언**
   - `texture_keyword_map.yaml`을 texture 계열 authoritative source로 선언
   - `keyword_surface_map.yaml`의 texture 항목은 generated/synced subset로 취급하거나 제거
2. review-side normalization에서 texture phrase가 나오면 반드시 `texture_keyword_map.yaml`을 먼저 참조
3. user-side와 review-side 모두 다음 2단 구조 유지
   - 상위: `Texture` (BEE_ATTR)
   - 하위: `GelLike`, `LightLotionLike`, `WateryLike`, `RichCreamLike` ... (KEYWORD)
4. 설명(explainer)에서는
   - “제형 축(Texture)에서”
   - “그중 젤/가벼운 로션 계열 표현이 강함”
   형태로 상위/하위를 같이 보여줄 수 있게 유지

### Acceptance Criteria
- user `preferred_texture`와 review-side texture 표현이 같은 keyword taxonomy로 정규화된다.
- texture 관련 drift가 두 설정 파일 사이에서 발생하지 않는다.
- texture는 attr-only 또는 keyword-only가 아니라 2단 구조로 일관되게 표현된다.

### 테스트 항목
- `tests/test_texture_taxonomy_sync.py`
  - case 1: user `젤` → `Texture + GelLike`
  - case 2: review `젤타입` → `Texture + GelLike`
  - case 3: user `가벼운 로션`과 review `가벼운로션`이 같은 keyword로 수렴
  - case 4: `texture_keyword_map.yaml`과 `keyword_surface_map.yaml` texture subset sync 검사

---

## P1-1. Candidate generation 기본 경로를 SQL prefilter 우선으로 전환

### 목적
catalog가 커졌을 때 Python full-scan 병목을 줄이고, Postgres-first / SQL-first 방향을 실제 런타임에 더 강하게 반영한다.

### 문제 상세
- 현재 `generate_candidates_prefiltered()`가 있긴 하지만, 기본 generate 경로는 여전히 Python loop 중심이다.
- product truth와 promoted-only serving profile이 이미 DB/mart에 있는 만큼, 1차 후보는 SQL에서 줄이는 게 맞다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/db/repos/mart_repo.py` 또는 신규 repo query module
- `sql/candidate_queries.sql` (신규 권장)
- `tests/test_candidate_prefilter_sql_path.py` (신규)

### 구현 지시
1. 표준 경로를 `generate_candidates_prefiltered()`로 승격
2. SQL prefilter에서 최소 아래를 수행
   - active product only
   - promoted serving profile only
   - avoided ingredient conflict 제외
   - strict/explore 모드별 category prefilter
   - 필요 시 price band / owned product prefilter
3. Python 쪽은 prefiltered 후보에 대해서만 overlap + scoring 수행
4. `catalog_validation_signal`은 candidate prefilter에도 절대 개입하지 않도록 유지

### Acceptance Criteria
- 전체 product list full scan 대신 prefiltered product id set 기반으로 candidate generation이 가능하다.
- strict/explore/compare 모드별 후보 수가 일관되게 다르다.
- promoted-only와 avoided ingredient 규칙이 SQL stage에서 이미 반영된다.

### 테스트 항목
- `tests/test_candidate_prefilter_sql_path.py`
  - case 1: strict mode category mismatch 제외
  - case 2: explore mode category mismatch는 남기되 penalty path로 전달
  - case 3: avoided ingredient product 제외
  - case 4: promoted-only 아닌 product는 prefilter에서 제외

---

## P1-2. Aggregate recompute를 batch SQL/group-by 중심으로 강화

### 목적
dirty product마다 full signal scan + Python aggregate를 도는 비용을 줄이고, incremental pipeline 운영성을 높인다.

### 문제 상세
- 현재 aggregate 구조는 correctness는 맞지만, dirty product 수가 커질수록 product별 wrapped_signal 재조회/재집계 비용이 커진다.
- batch SQL path는 일부 존재하지만, promoted metrics와 후처리가 더 필요하다.

### 수정 대상 파일
- `src/mart/aggregate_product_signals.py`
- `src/db/repos/mart_repo.py`
- `src/jobs/run_incremental_pipeline.py`
- `tests/test_batch_aggregate_consistency.py` (신규)

### 구현 지시
1. dirty product set을 batch로 받아 SQL group-by 집계하는 경로를 기본화
2. Python aggregate는 fallback/debug path로 유지
3. `review_count / distinct_review_count / avg_confidence / synthetic_ratio / corpus_weight / is_promoted` 계산이 batch path에서도 동일하도록 보장
4. dirty product 계산은 old/new product union 유지

### Acceptance Criteria
- batch aggregate 결과가 기존 single-product Python aggregate와 동일하다.
- tombstone, relink, late-arrival 이후 aggregate 일관성이 유지된다.
- promoted-only 기준이 batch path에서도 동일하게 적용된다.

### 테스트 항목
- `tests/test_batch_aggregate_consistency.py`
  - case 1: batch == single aggregate
  - case 2: tombstone 후 감소 일치
  - case 3: old/new product relink 후 양쪽 dirty aggregate 일치

---

## P1-3. mockdata를 regression fixture로 승격

### 목적
mockdata를 문서 자산이 아니라 계약 회귀 자산으로 만들어, schema drift와 graph contract 깨짐을 빨리 잡는다.

### 문제 상세
- `shared_entities.json`, `review_kg_output.json`, `review_rs_samples.json`은 좋은 자산이지만, 현재는 문서/참조 비중이 더 크다.
- 이걸 테스트 fixture로 쓰면 input/output contract를 훨씬 안정적으로 고정할 수 있다.

### 수정 대상 파일
- `tests/test_mock_integrity.py` (확장)
- `tests/test_mock_review_kg_regression.py` (신규)
- `tests/test_mock_rs_loader.py` (신규)
- 필요 시 `mockdata/README.md`

### 구현 지시
1. `shared_entities.json` 기반 cross-source integrity 테스트 추가
   - product / user / review mock 간 공통 entity ID/이름/anchor consistency 확인
2. `review_kg_output.json`을 gold-ish fixture로 써서 evidence-kind regression 테스트 추가
3. `review_rs_samples.json`에 대해
   - `source_review_key`
   - `author_key`
   - channel / timestamps
   로더 검증 추가
4. mock README에는 각 fixture가 어떤 테스트를 위한 것인지 한 줄씩 더 명시

### Acceptance Criteria
- mock schema 변경 시 테스트가 깨져서 drift를 빠르게 감지할 수 있다.
- evidence demotion/promotion 규칙을 fixture 기반으로 회귀 검증할 수 있다.
- rs loader가 stable key/author key를 제대로 채운다.

### 테스트 항목
- `tests/test_mock_review_kg_regression.py`
  - case 1: `RAW_REL / NER_BEE_ANCHOR / BEE_SYNTHETIC / AUTO_KEYWORD` evidence kind 기대값 유지
- `tests/test_mock_rs_loader.py`
  - case 1: `source_review_key` 추출
  - case 2: `author_key` 추출
  - case 3: timestamp/channel mapping

---

## P1-4. promoted-only contract를 추천 표준 경로 전체로 확장 검증

### 목적
`build_serving_product_profile()`는 이미 `promoted_only=True`를 기본값으로 두지만, 표준 추천 경로 전체가 이 계약을 지키는지 보강한다.

### 문제 상세
- 현재 테스트는 builder 단위 중심이다.
- 이후 API/export/debug path가 추가될수록 promoted-only contract가 우회될 수 있다.

### 수정 대상 파일
- `tests/test_promoted_only_contract.py` (확장)
- 필요 시 `src/web/server.py`, `src/jobs/run_daily_pipeline.py`, `src/rec/candidate_generator.py`

### 구현 지시
1. 추천 표준 경로 smoke test 추가
   - standard recommendation path가 non-promoted signal을 사용하지 않는지
2. debug/export path는 `promoted_only=False`를 명시적으로 opt-in 해야만 non-promoted를 보게 만들기
3. 문서/주석에 standard vs debug profile 차이를 명시

### Acceptance Criteria
- standard recommendation path는 non-promoted를 쓰지 않는다.
- debug/export path만 명시적 opt-in으로 non-promoted를 볼 수 있다.

### 테스트 항목
- `tests/test_promoted_only_contract.py` 확장
  - case 1: standard recommend path non-promoted 미사용
  - case 2: debug profile은 non-promoted 포함 가능

---

## P1-5. User weighting model을 추천기에서 더 적극적으로 소비

### 목적
이미 집계한 `base_confidence × frequency_factor × recency_factor × source_type_weight`를 serving과 scorer에서 더 분명하게 활용한다.

### 문제 상세
- `aggregate_user_preferences.py`는 weighting을 계산하지만, candidate/scorer가 그 weight를 얼마나 강하게 쓰는지 더 선명하게 할 수 있다.
- 특히 user concern/context/goal/texture/behavior의 강도 차이를 추천에 더 반영할 여지가 있다.

### 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `tests/test_user_weighting_consumption.py` (신규)

### 구현 지시
1. serving user profile에 주요 선호/회피/행동 신호의 대표 weight를 함께 노출
2. candidate overlap 계산 시 단순 set intersection뿐 아니라 최소한 대표 weight를 참조할 수 있게 준비
3. scorer에서
   - concern/context/goal/texture/purchase behavior의 source/recency/frequency 차이를 더 반영
4. 문서에 source priority 예시 명시
   - purchase > explicit chat > basic profile > inferred

### Acceptance Criteria
- 같은 선호라도 purchase-derived가 chat weak preference보다 더 강하게 작동한다.
- 최근/반복적 신호가 오래된 약한 신호보다 높은 weight를 가진다.

### 테스트 항목
- `tests/test_user_weighting_consumption.py`
  - case 1: purchase-derived brand > chat weak brand
  - case 2: repeated context preference > one-shot context mention
  - case 3: recent signal > stale signal

---

## P2-1. rs.jsonl ingest 경로를 first-class path로 공식화

### 목적
운영 원본 소스가 `rs.jsonl`에 가까운 만큼, `review_triples_raw`뿐 아니라 raw source ingest 경로를 공식화한다.

### 수정 대상 파일
- `src/loaders/rs_jsonl_loader.py`
- 신규 `src/jobs/run_rs_ingest.py` 또는 `src/jobs/run_full_load.py` 확장
- `mockdata/README.md`
- `README.md`

### 구현 지시
1. rs.jsonl 전용 CLI/entrypoint 추가
2. README/local dev 문서에 rs loader 사용 예시 추가
3. `review_triples_raw` path와 `rs_jsonl` path가 각각 어떤 상황에 쓰이는지 문서화

### Acceptance Criteria
- rs.jsonl 샘플만으로도 raw review ingest smoke run이 가능하다.
- README에서 공식 입력 경로가 혼동되지 않는다.

---

## P2-2. repo hygiene / quality tooling 보강

### 목적
현재 구조가 커진 만큼 lint/type/coverage/CI를 붙여 유지보수성을 높인다.

### 수정 대상 파일
- `pyproject.toml`
- `.gitignore`
- `.github/workflows/*` (신규 권장)
- 루트 문서

### 구현 지시
1. dev tooling 추가
   - `ruff`
   - `mypy`
   - `pytest-cov`
2. tracked `__pycache__` 제거 및 ignore 보강
3. CI workflow 추가
   - unit tests
   - lint
   - type check
4. README/ARCHITECTURE/CHANGELOG의 최신 상태 반영

### Acceptance Criteria
- 로컬/CI에서 lint/type/tests를 일관되게 돌릴 수 있다.
- 새 참여자가 루트 문서만 보고 개발 환경을 세팅할 수 있다.

---

## 작업 우선순위

### 이번 사이클 꼭 할 것
1. P0-1 Family-level personalization 정합성 마무리
2. P0-2 Texture taxonomy authoritative source 고정
3. P1-4 promoted-only contract 전 경로 확장 검증

### 그 다음 사이클
4. P1-1 SQL-first candidate prefilter
5. P1-2 batch aggregate
6. P1-3 mock regression fixture 승격
7. P1-5 user weighting consumption 강화

### 여유 있을 때
8. P2-1 rs.jsonl first-class ingest
9. P2-2 repo hygiene / CI

---

## 최종 완료 기준

- family identity가 exact SKU / same family / repurchased family로 일관되게 구분된다.
- texture는 `Texture` 축 + 하위 keyword로 user/review 양쪽에서 같은 taxonomy를 사용한다.
- standard serving/recommendation path는 promoted-only contract를 지킨다.
- mockdata가 regression fixture로 작동한다.
- candidate/aggregate는 점진적으로 SQL-first에 가까워진다.
