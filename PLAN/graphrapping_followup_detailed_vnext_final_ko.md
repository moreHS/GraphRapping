# GraphRapping vNext 후속 수정 지시서 (최신 main + mockdata 반영)

작성 목적:
- 최신 `main` 레포와 `mockdata/`를 기준으로, 남아 있는 구조 보강 포인트를 **파일별 수정 지시**로 정리한다.
- 이미 수정 완료된 항목은 재지시하지 않는다.
- 현재 프로젝트 방향인 **Evidence graph(per-review) → Canonical facts → Wrapped signals → Corpus-promoted serving → Personalized recommendations**를 유지한다.
- 대규모 재설계가 아니라, **입력 계약 정렬 + 구조 활용률 증대 + 운영성 보강**이 목적이다.

---

## 0. 현재 상태 요약

### 이미 잘 되어 있는 것
- Product truth → concept seed → serving product profile 흐름 존재
- User state/goal/behavior → serving user profile 흐름 존재
- Review evidence graph와 serving graph를 분리하는 철학 존재
- Corpus promotion (`distinct_review_count`, `avg_confidence`, `synthetic_ratio`, `is_promoted`) 계산 존재
- Serving product profile 기본값이 `promoted_only=True`
- Texture는 상위 BEE_ATTR(Texture) + 하위 KEYWORD(GelLike, LightLotionLike 등) 구조로 user 쪽에 반영 시작
- Product loader가 mock ES schema의 `SALE_PRICE`, `MAIN_INGREDIENT`, `MAIN_EFFECT`, `COUNTRY_OF_ORIGIN`, `REPRESENTATIVE_PROD_CODE`를 실제 `ProductRecord`로 반영
- User preference aggregation에 source_type / recency / frequency 가중치 반영 시작
- `OWNS_PRODUCT`, `OWNS_FAMILY`, `REPURCHASES_BRAND`, `REPURCHASES_CATEGORY`, `REPURCHASES_FAMILY`가 분리됨
- `top_coused_product_ids`, `tool_alignment`, `coused_product_bonus`, `owned_family_penalty`, `repurchase_family_affinity`가 rec/scoring에 반영되기 시작
- `review_rs_samples.json`, `SCHEMA_RS_JSONL.md` 등 실제 스키마 reference mock이 추가됨

### 아직 남아 있는 핵심 문제
1. **Product family / variant 계층이 product truth에는 있는데, 추천/개인화 경로에서 활용이 아직 약함**
2. **Texture/keyword taxonomy가 user adapter 코드에 부분 하드코딩되어 있고, review 쪽 normalization과 100% 공통 governance가 아님**
3. **User raw 7-column schema는 mock에 있으나 실제 loader path는 normalized-only → raw→normalized transformer 부재**
4. **`review_rs_samples.json` / `SCHEMA_RS_JSONL.md`는 존재하지만 first-class raw transformer가 없음**
5. **`shared_entities.json`, `review_kg_output.json`이 테스트 자산으로 충분히 승격되지 않음**
6. **Candidate generation / aggregate가 여전히 Python 중심 → SQL-first 활용률이 낮음**
7. **`signal_evidence`를 provenance SoT로 선언했지만 `wrapped_signal.source_fact_ids` 캐시가 아직 write-path에 남아 있어 정합성 관리 부담이 있음**
8. **`fact_provenance`가 review 중심 사고에서 완전히 벗어나진 못함**
9. **repo scope(`src/kg`, `src/web`, `src/graph`, `src/static`)가 넓고 실험 경로와 운영 경로 분리가 아직 충분히 선명하지 않음**

---

## 1. 변경 원칙 (반드시 유지)

1. Review evidence graph는 **정본 KG가 아니라 증거 그래프**다.
2. Serving recommendation에 쓰이는 것은 **promoted signal만**이다.
3. Product truth는 review-derived signal이 절대 overwrite 하지 않는다.
4. User/Product 연결은 shared concept plane (`concept_id`)로만 한다.
5. `Texture`는 **BEE_ATTR 축**, `GelLike / LightLotionLike / CreamyLike ...`는 **KEYWORD/descriptor**다.
6. User의 texture 선호는 **축(BEE_ATTR) + 구체 표현(KEYWORD)** 두 층으로 함께 표현한다.
7. Provenance source of truth는 `signal_evidence`이며, `wrapped_signal.source_fact_ids`는 제거 또는 pure cache로 내린다.
8. mock schema와 loader contract가 다르면 mock/schema 쪽이 아니라 **loader/adapter가 맞춰야 한다.**

---

## 2. P0 — 이번 사이클에서 꼭 해야 하는 것

## P0-1. Product family / variant를 추천 런타임에 실제로 쓰기

### 왜 필요한가
mock product schema는 `REPRESENTATIVE_PROD_CODE`를 제공하고, loader는 이를 `variant_family_id`로 실제 반영한다. 하지만 추천/개인화 쪽에서 family-level suppression/affinity는 아직 부분 반영 수준이다.

화장품/쿠션/틴트 계열은:
- exact SKU
- same family (same line / shade family / representative product)
를 구분해야 의미가 있다.

현재는 product truth에는 family 정보가 있는데, user behavior와 scoring 경로에서 활용이 충분치 않다.

### 수정 대상 파일
- `src/ingest/product_ingest.py`
- `src/loaders/product_loader.py`
- `src/mart/build_serving_views.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/user/canonicalize_user_facts.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 필요 시: `sql/ddl_raw.sql`, `sql/ddl_mart.sql`

### 작업 내용
1. `serving_product_profile`에 아래 필드를 공식 필드로 고정
   - `variant_family_id`
   - `representative_product_name` (있으면)
2. user profile 쪽에서 아래를 공식 필드로 유지/강화
   - `owned_family_ids`
   - `repurchased_family_ids`
3. candidate generator에 family-aware logic 추가
   - same exact product → deprioritize or suppress (mode configurable)
   - same family owned → explore mode에서는 약한 penalty, repurchase mode에서는 boost 가능
4. scorer에 family feature를 명시화
   - `owned_family_penalty`
   - `repurchase_family_affinity`
   - optional `same_family_novelty_penalty`
5. explainer에 family relation을 설명 가능하게 추가
   - “현재 사용 중인 제품과 같은 라인/패밀리”
   - “같은 패밀리 재구매 성향”

### Acceptance Criteria
- 제품 A와 제품 B가 같은 `variant_family_id`면 user owned/repurchase behavior와 연결된다.
- same family but different SKU 추천이 exact same SKU 추천과 구분된다.
- scoring output에 family feature contribution이 실제로 나타난다.
- explanation path가 family affinity/penalty를 설명할 수 있다.

### 테스트
- `tests/test_family_affinity.py` 신규
  - owned exact product vs owned same family vs unrelated product
  - repurchased_family_ids가 family match에 반영되는지
  - compare/explore/strict 모드별 family 처리 차이

---

## P0-2. Texture taxonomy를 user/review 공통 config로 통일

### 왜 필요한가
현재 user adapter는 `configs/texture_keyword_map.yaml`을 읽어 `preferred_texture`를 상위 `Texture` BEE_ATTR와 하위 keyword로 변환한다. 이 방향은 맞다. 하지만 review 쪽 keyword normalization과 실제 완전히 같은 사전을 쓴다는 보장이 아직 약하다.

Texture는 이 프로젝트에서 중요한 예외 케이스다.
- `Texture`는 BEE_ATTR 축
- `젤`, `가벼운 로션`, `워터리`, `리치 크림` 등은 그 축 아래의 KEYWORD

즉 “속성인지 키워드인지”가 아니라 **둘 다**다.

### 수정 대상 파일
- `configs/texture_keyword_map.yaml`
- `configs/keyword_surface_map.yaml`
- `src/user/adapters/personal_agent_adapter.py`
- `src/normalize/keyword_normalizer.py`
- `src/normalize/bee_normalizer.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`

### 작업 내용
1. texture normalization을 review/user 공통 사전으로 사용하도록 통일
   - `texture_keyword_map.yaml`을 authoritative source로 둘지
   - `keyword_surface_map.yaml`에 merge할지 결정
   - **하나만 authoritative source**로 정리
2. review path에서도 texture surface를 같은 canonical keyword로 normalize하도록 보장
3. user adapter는 계속 다음 두 층을 동시에 만든다
   - `PREFERS_BEE_ATTR(Texture)`
   - `PREFERS_KEYWORD(GelLike / LightLotionLike / WateryLike ...)`
4. scorer는 texture 관련 점수에서
   - `keyword_match`를 우선
   - `residual_bee_attr_match`를 backoff로 사용
5. explainer는 texture를 아래처럼 두 층으로 설명
   - “제형 축에서 선호가 맞고”
   - “그중에서도 젤/가벼운 로션 계열 표현과 잘 맞음”

### Acceptance Criteria
- user texture 선호와 review/product texture signal이 같은 canonical keyword 공간에서 만난다.
- texture 관련 double counting이 없다.
- explanation에 BEE_ATTR(Texture)와 구체 keyword가 함께 나온다.

### 테스트
- `tests/test_texture_taxonomy_alignment.py` 신규
  - user preferred_texture → Texture + GelLike/LightLotionLike 분해
  - review phrase “젤타입”, “가벼운 로션”, “워터리”가 동일 keyword로 normalize
  - keyword match와 residual attr match가 의도대로 계산되는지

---

## P0-3. rs.jsonl를 first-class raw source로 끌어올리기

### 왜 필요한가
mock에 `review_rs_samples.json`과 `SCHEMA_RS_JSONL.md`가 추가되면서 실제 운영 소스 스키마가 더 분명해졌다. 그런데 현재 공식 loader는 여전히 `review_triples_raw.json` / relation-project-style 입력을 중심으로 움직인다.

즉 지금은:
- relation/raw triple style loader는 있음
- 실제 rs.jsonl raw source는 reference-only mock

이 상태다. production-like validation을 하려면 rs.jsonl를 first-class loader로 올리는 게 맞다.

### 수정 대상 파일
- 신규: `src/loaders/review_rs_loader.py`
- `src/loaders/relation_loader.py`
- `src/ingest/review_ingest.py`
- `mockdata/README.md`
- 필요 시: `tests/test_rs_loader.py`

### 작업 내용
1. 신규 loader 추가
   - `load_reviews_from_rs_jsonl()` 또는 `stream_reviews_from_rs_jsonl()`
   - 입력: `review_rs_samples.json` / JSONL / S3-export style
2. raw source → current `RawReviewRecord`로 변환
   - `source_review_key`
   - `author_key`
   - `source_product_id`
   - `channel`
   - `reviewer_profile`
   - `created_at`/`collected_at`
   - `text`
   - `ner/bee/relation`이 이미 있으면 그대로
   - 없으면 extraction 이전 raw-only path로도 보관 가능하게
3. `mockdata/README.md`에 공식 입력 경로를 명시
   - normalized triple loader path
   - rs raw path
4. review loader 계층을 두 개로 구분
   - `relation_loader.py` = already-extracted review triple loader
   - `review_rs_loader.py` = raw source loader

### Acceptance Criteria
- `review_rs_samples.json`에서 최소 1건 이상 `RawReviewRecord`로 정상 변환된다.
- `source_review_key`, `author_key`, `channel`, `source_product_id`가 review ingest까지 유지된다.
- 기존 relation loader 경로를 깨지 않는다.

### 테스트
- `tests/test_rs_loader.py` 신규
  - own/extn/glb source 각각 1건 이상 파싱
  - `source_review_key`, `author_key`, `channel` 보존 확인
  - stable review_id/reviewer_proxy 생성 확인

---

## P0-4. `shared_entities.json` / `review_kg_output.json`을 테스트 자산으로 승격

### 왜 필요한가
mock에 매우 좋은 cross-check fixture가 이미 있다.
- `shared_entities.json`: cross-source anchor
- `review_kg_output.json`: evidence graph reference output

지금은 문서 자산에 가깝고, 자동 테스트 자산으로는 충분히 활용되지 않는다.

### 수정 대상 파일
- 신규: `tests/test_mock_integrity.py`
- 신규: `tests/test_review_kg_regression.py`
- 신규 또는 확장: `tests/test_shared_entity_alignment.py`
- 필요 시: `mockdata/README.md`

### 작업 내용
1. `shared_entities.json` 기반 integrity test
   - product IDs / brand IDs / user IDs / concept consistency
2. `review_kg_output.json` 기반 regression test
   - evidence_kind 분포
   - synthetic relation demotion
   - auto keyword quarantine/candidate behavior
3. cross-source consistency
   - product_catalog_es + user_profiles + review_triples + shared_entities가 서로 모순되지 않는지

### Acceptance Criteria
- mock schema drift가 테스트에서 바로 잡힌다.
- KG evidence output 형식이 바뀌면 regression이 감지된다.

### 테스트
- `tests/test_mock_integrity.py`
- `tests/test_review_kg_regression.py`
- `tests/test_shared_entity_alignment.py`

---

## 3. P1 — 구조는 맞지만 활용률을 더 올려야 하는 것

## P1-1. candidate generation을 실제 SQL-first로 밀어올리기

### 왜 필요한가
현재 `candidate_generator.py`는 `generate_candidates_prefiltered()` 경로가 생겼지만, 기본 `generate_candidates()`는 여전히 Python 리스트 순회 비중이 크다. catalog가 커질수록 병목이 된다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- 신규 또는 확장: `sql/candidate_queries.sql`
- `src/db/repos/mart_repo.py` 또는 신규 repo query module
- `src/web/server.py` / API entrypoint가 있다면 해당 경로

### 작업 내용
1. SQL prefilter를 공식 기본 경로로 승격
   - active/availability
   - category strict filter
   - avoid ingredient hard exclusion
   - optional price band
   - optional promoted-only signal existence
2. Python은 overlap/rerank만 담당
3. `generate_candidates()`는 내부적으로 prefilter path를 기본 호출

### Acceptance Criteria
- 기본 추천 경로가 전체 product_profiles 리스트를 매번 순회하지 않는다.
- STRICT/EXPLORE/COMPARE 모드가 SQL + Python에 일관되게 반영된다.

### 테스트
- `tests/test_candidate_prefilter_sql.py`

---

## P1-2. Product aggregate를 batch SQL/group-by 경로로 보강

### 왜 필요한가
현재 dirty product 재집계는 correctness는 좋지만, product별 signal full-read 후 Python aggregate 비중이 여전히 있다. 규모가 커질수록 비용이 커진다.

### 수정 대상 파일
- `src/mart/aggregate_product_signals.py`
- `src/db/repos/mart_repo.py`
- `src/jobs/run_incremental_pipeline.py`

### 작업 내용
1. batch dirty product aggregate path 추가
2. Python aggregate는 fallback/debug path로 유지
3. window_type(30d/90d/all) 계산은 SQL group-by 가능하면 SQL로 이동

### Acceptance Criteria
- dirty products N개에 대해 batched aggregation 가능
- Python aggregate 결과와 batch SQL 결과가 동일

### 테스트
- `tests/test_batch_aggregate_consistency.py`

---

## P1-3. user weighting model을 더 구조적으로 만들기

### 왜 필요한가
현재는 `base_confidence × frequency × recency × source_type_weight`로 꽤 좋아졌지만, 아직 explicit preference / purchase behavior / inferred signal의 결합이 단순한 편이다.

### 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `src/rec/scorer.py`
- `configs/scoring_weights.yaml`

### 작업 내용
1. user aggregate에서 source별 confidence bucket을 더 명시
   - purchase > explicit chat > explicit profile > inferred
2. state/goal/context/behavior family별로 가중치 상한 분리
3. scorer에서 user feature provenance를 디버깅 가능하게 expose

### Acceptance Criteria
- user preference weight가 source/recency/frequency에 따라 합리적으로 달라진다.
- 같은 concept라도 source가 다르면 weight가 구분된다.

### 테스트
- `tests/test_user_weighting_model.py`

---

## P1-4. provenance 모델을 review 전용에서 범용으로 확장

### 왜 필요한가
현재 `fact_provenance`는 주로 review raw rows를 전제로 한다. 하지만 이 구조는 product master fact, user fact, manual fact까지 Layer 2로 끌어오는 방향이라 provenance를 더 일반화하는 편이 좋다.

### 수정 대상 파일
- `sql/ddl_canonical.sql`
- `src/canonical/canonical_fact_builder.py`
- `src/db/repos/provenance_repo.py`
- `src/user/canonicalize_user_facts.py`
- `src/ingest/product_ingest.py`

### 작업 내용
`fact_provenance`에 아래 개념을 확실히 두기
- `source_domain` (`review|user|product|manual|system`)
- `source_kind` (`raw|summary|master|derived`)
- `source_table`
- `source_row_id`

### Acceptance Criteria
- review/user/product/master facts 모두 provenance로 추적 가능
- explainer는 review facts는 snippet, product/user facts는 적절한 provenance summary 반환

### 테스트
- `tests/test_generic_provenance_model.py`

---

## P1-5. `signal_evidence` 정본화 마무리

### 왜 필요한가
현재 docstring/DDL 주석은 `signal_evidence`가 provenance SoT라고 말하지만, `wrapped_signal.source_fact_ids`가 아직 남아 있고 emitter도 cache를 채운다. 기능상 문제는 아니지만 정합성 관리 포인트가 남는다.

### 수정 대상 파일
- `sql/ddl_signal.sql`
- `src/wrap/signal_emitter.py`
- `src/rec/explainer.py`
- `src/db/repos/provenance_repo.py`
- `src/db/repos/signal_repo.py`

### 작업 내용
1. `source_fact_ids`를 완전 제거 또는 optional cache-only로 더 명확히 다운그레이드
2. aggregate의 `evidence_sample`도 `signal_evidence`에서만 만들기
3. `explainer`는 `signal_evidence`만 정본 경로 사용

### Acceptance Criteria
- `source_fact_ids` 없이도 explanation/provenance 체인이 완성된다.
- 정합성 문제 없이 `signal_evidence`만으로 signal origin 추적 가능하다.

### 테스트
- `tests/test_signal_evidence_sot.py`

---

## 4. P2 — 레포 건강도 / 운영성

## P2-1. kg shadow / web / graph / static boundary 정리

### 왜 필요한가
현재 core pipeline + experimental KG + web/static/graph projection이 한 repo에 공존한다. 기능적으로 문제는 없지만 boundary가 흐리면 scope creep가 생긴다.

### 수정 대상 파일
- `src/jobs/run_daily_pipeline.py`
- `src/kg/*`
- `src/web/*`
- `src/graph/*`
- `README.md`
- `ARCHITECTURE.md`

### 작업 내용
1. core path와 shadow/evidence path의 역할을 문서에서 더 분명히 구분
2. `kg_mode`가 운영 기본 경로를 오염시키지 않게 entrypoint 분리 검토
3. web/static이 추천 코어 의존성을 과도하게 만들지 않도록 boundary 정리

### Acceptance Criteria
- 신규 개발자가 README/ARCHITECTURE만 보고 core path와 experimental path를 구분할 수 있다.

---

## P2-2. tooling 강화

### 수정 대상 파일
- `pyproject.toml`
- CI config (있다면)

### 작업 내용
추가 검토:
- `ruff`
- `mypy`
- `pytest-cov`
- integration test harness (dockerized Postgres 또는 ephemeral fixture)

### Acceptance Criteria
- lint/type/test coverage를 최소한 로컬/CI에서 일관되게 돌릴 수 있다.

---

## 5. 실행 순서 권장

### 지금 바로 (P0)
1. Product family/variant를 rec path에 연결
2. Texture taxonomy authoritative config 통일
3. rs.jsonl first-class loader 추가
4. shared_entities/review_kg_output 테스트 자산화

### 다음 사이클 (P1)
5. SQL-first candidate generation 기본화
6. batch aggregate
7. user weighting model 정교화
8. provenance 범용화
9. signal_evidence 정본화 마무리

### 이후 (P2)
10. core vs experimental boundary 정리
11. repo tooling 강화

---

## 6. 이번 사이클 완료 기준

- product family/variant가 user owned/repurchase behavior와 추천 score에 실제 반영된다.
- texture는 BEE_ATTR(Texture) + KEYWORD(GelLike/LightLotionLike...) 구조로 user/review 양쪽이 같은 taxonomy를 쓴다.
- rs.jsonl raw source를 first-class loader로 ingest 가능하다.
- shared_entities/review_kg_output이 테스트 자산으로 승격된다.
- 추천 후보 생성은 SQL prefilter 경로를 공식 기본으로 사용할 준비가 된다.
- aggregate와 provenance 구조는 장기 운영 기준으로 더 단단해진다.

