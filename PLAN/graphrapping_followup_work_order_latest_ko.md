# GraphRapping 후속 수정 작업 지시서 (latest main 기준)

## 전제
- 이 문서는 **최신 `main` 레포 상태를 다시 확인한 뒤**, 아직 남아 있는 이슈만 정리한 후속 작업 지시서다.
- 사용자가 별도로 전달한 “이미 수정 완료” 목록은 **원칙적으로 제외**했다.
- 단, 아래 2개는 **수정 완료로 보고되었지만 현재 `main`에서 여전히 남아 있는 것으로 확인되어 다시 포함**했다.
  1. `projection_registry.csv`의 `recommended_to,Product,UserSegment`가 아직 `qualifier_required=Y`로 남아 있음
  2. `signal_emitter.py`의 reverse transform이 아직 `dst_ref_kind = "ENTITY"`를 하드코딩함

## 이번 문서에서 제외한 항목
아래는 사용자가 "이미 수정 완료"라고 전달했고, 최신 레포에서 핵심 방향상 재지시가 불필요해 보여 **이번 문서에 반복하지 않는 항목**이다.
- `ReviewPersistBundle`에 `.get()` 사용하던 문제
- `bee_normalizer.py` 한국어 substring false positive
- `mention_extractor.py` `keyword_source` 기본값
- `canonical_fact_builder.py`에서 `None fact_id` guard
- `candidate_generator.py`의 already-owned deprioritize
- `run_incremental_pipeline.py` watermark skip
- `run_daily_pipeline.py` shadow mode quarantine 추가

## 이번 문서에서 제외한 deferred 항목
사용자가 "향후 작업"으로 명시한 아래 항목은 **이번 문서의 핵심 후속수정 범위에서는 제외**한다.
- NER-BeE flatten → anchor evidence 전환
- BEE contract 추가 필드(`evidence_text`, `evidence_span`, `derived_qualifiers`)
- Registry source/evidence gate 강제 enforcement
- Usage pattern mart
- Candidate SQL prefilter / explainer goal split의 대규모 재설계 버전
- `kg_mode` legacy/shadow 완전 분리

---

## P0-1. `recommended_to` projection 계약 불일치 수정

### 왜 문제인가
현재 `run_daily_pipeline.py`는 `recommended_to`, `targeted_at`, `addressed_to`를 만나면 object text를 `derive_segment()`로 `UserSegment` concept로 직접 승격한다. 그런데 projection registry에는 `recommended_to,Product,UserSegment`가 여전히 `qualifier_required=Y, qualifier_type=segment`로 남아 있다. 이 조합이면 object가 이미 `UserSegment`여도 qualifier가 없다는 이유로 signal emitter에서 quarantine될 수 있다.

### 최신 main에서 확인된 근거
- `run_daily_pipeline.py`는 `recommended_to`를 `UserSegment` concept로 직접 올린다.【turn425037view4】
- 그런데 `projection_registry.csv`는 같은 조합을 `qualifier_required=Y`로 정의한다.【turn425037view8】

### 수정 대상 파일
- `configs/projection_registry.csv`
- `src/wrap/projection_registry.py`
- `src/wrap/signal_emitter.py`
- 필요 시 `src/jobs/run_daily_pipeline.py`

### 수정 지시
**권장안으로 통일한다.**
- `recommended_to,Product,UserSegment`는 `qualifier_required=N`으로 수정
- `dst_type=UserSegment`, `output_edge_type=RECOMMENDED_TO_SEGMENT_SIGNAL` 유지
- qualifier는 추가 맥락이 있을 때만 optional 사용
- `projection_registry.py` validation에서도 이 row를 정상 조합으로 처리

### Acceptance Criteria
- object가 이미 `UserSegment`인 `recommended_to` fact는 quarantine 없이 signal 생성
- qualifier가 없어도 direct object 승격 경로가 정상 동작
- `targeted_at`, `addressed_to` 등 유사 패턴도 같은 계약 철학으로 정렬

### 테스트
- `tests/test_recommended_to_projection.py` 신규
  - case 1: object=`UserSegment`, qualifier 없음 → signal 생성
  - case 2: object=`Person`, qualifier 있음 → signal 생성
  - case 3: object=`Person`, qualifier도 없음 → quarantine

---

## P0-2. reverse transform `dst_ref_kind` 하드코딩 제거

### 왜 문제인가
reverse transform은 `dst_id = fact.subject_iri`를 사용한다. 그런데 현재 `signal_emitter.py`는 reverse transform이면 `dst_ref_kind = "ENTITY"`를 하드코딩한다. subject가 concept인 reverse mapping(`caused_by(Concern, Product)`, `ingredient_of(Ingredient, Product)` 등)에서는 `dst_id`가 concept인데도 ENTITY로 찍혀 ref-kind 일관성이 깨진다.

### 최신 main에서 확인된 근거
- `signal_emitter.py` reverse 분기에서 `dst_ref_kind = "ENTITY"`가 남아 있다.【turn425037view7】

### 수정 대상 파일
- `src/wrap/signal_emitter.py`
- `src/canonical/canonical_fact_builder.py`
- 필요 시 `sql/ddl_canonical.sql`

### 수정 지시
- `CanonicalFact`에 `subject_ref_kind`를 추가하고 persistence까지 연결
- reverse transform 시
  - `dst_id = fact.subject_iri`
  - `dst_ref_kind = fact.subject_ref_kind`
- 이미 subject가 concept인지 entity인지 추론 가능한 곳이 있다면 거기서 채우고, 없으면 `canonical_fact_builder.py`에서 생성 시 명시

### Acceptance Criteria
- reverse mapping 결과의 `dst_ref_kind`가 실제 subject kind와 일치
- concept reverse mapping이 ENTITY로 잘못 저장되지 않음
- explanation/serving query에서 ref-kind mismatch가 발생하지 않음

### 테스트
- `tests/test_reverse_transform_ref_kind.py` 신규
  - case 1: `caused_by(Concern, Product)` reverse → `dst_ref_kind=CONCEPT`
  - case 2: `ingredient_of(Ingredient, Product)` reverse → `dst_ref_kind=CONCEPT`
  - case 3: 실제 entity subject reverse → `dst_ref_kind=ENTITY`

---

## P0-3. `signal_evidence`를 provenance 정본으로 고정

### 왜 문제인가
최신 DDL 기준 `wrapped_signal`에는 `source_fact_ids text[]`가 남아 있고, 동시에 `signal_evidence(signal_id, fact_id, evidence_rank, contribution)`도 있다. 또 emitter merge 경로는 `existing.source_fact_ids.append(fact.fact_id)`를 수행한다. 즉 signal provenance 정본 위치가 두 군데다. 장기적으로는 반드시 어긋난다.

### 최신 main에서 확인된 근거
- `wrapped_signal.source_fact_ids`가 아직 DDL에 있다.【turn710491view1】
- `signal_evidence` 테이블도 별도로 존재한다.【turn710491view0】
- emitter는 merge 시 `existing.source_fact_ids.append(...)`를 수행한다.【turn204212view11】

### 수정 대상 파일
- `sql/ddl_signal.sql`
- `src/wrap/signal_emitter.py`
- `src/db/repos/signal_repo.py`
- `src/rec/explainer.py`
- `src/db/repos/provenance_repo.py`

### 수정 지시
- provenance 정본은 **`signal_evidence` 하나로 고정**
- `wrapped_signal.source_fact_ids`는 아래 둘 중 하나로 처리
  - 제거
  - 캐시/디버그용 optional field로 강등
- `signal_repo.py`는 `signal_evidence`를 항상 쓰고, explainer/provenance chain은 오직 `signal_evidence → canonical_fact → fact_provenance`만 사용
- emitter 내부 merge는 `source_fact_ids` 직접 append 대신 evidence rows를 누적하는 방식으로 정렬

### Acceptance Criteria
- signal provenance를 `signal_evidence`만으로 완전 재구성 가능
- `source_fact_ids`를 제거해도 explanation path가 유지됨
- `source_fact_ids`를 남기더라도 `signal_evidence`와 불일치가 발생하지 않음

### 테스트
- `tests/test_signal_evidence_source_of_truth.py` 신규
  - case 1: signal_evidence만으로 explanation 재구성 가능
  - case 2: 캐시 필드가 있다면 signal_evidence와 동기화 일치
  - case 3: signal_evidence 삭제 시 explainer 실패가 명확히 감지됨

---

## P0-4. `signal_id`/dedup policy의 qualifier 반영 여부를 확정

### 왜 문제인가
현재 emitter는 signal key에 polarity/negated/qualifier_fingerprint까지 반영하는 방향으로 보강돼 있다.【turn204212view11】 다만 실제 DB/merge 정책이 이 계약을 완전히 따르는지, qualifier 차이가 serving 의미를 바꿀 때도 충돌 없이 separate signal이 유지되는지 명확히 못 박아야 한다.

### 수정 대상 파일
- `src/common/ids.py`
- `src/wrap/signal_emitter.py`
- 필요 시 `sql/ddl_signal.sql`

### 수정 지시
- 공식 dedup key를 문서/코드에 명시
  - `review_id`
  - `target_product_id`
  - `edge_type`
  - `dst_id`
  - `polarity`
  - `negated`
  - `qualifier_fingerprint`
  - `registry_version`
- merge rule도 코드 주석과 테스트로 고정
  - `weight = max(...)`
  - `confidence = max(...)`
  - `source modalities = union`
  - `evidence = top-k by contribution`
- qualifier가 serving semantics를 바꾸는 조합은 절대 same signal로 합치지 않음

### Acceptance Criteria
- 같은 `dst_id`라도 polarity/qualifier가 다르면 충돌 없이 분리
- 같은 semantic signal은 재처리 시 중복 없이 idempotent
- merge 결과가 deterministic

### 테스트
- `tests/test_signal_dedup_polarity_qualifier.py` 신규
  - case 1: same dst, different polarity → 분리
  - case 2: same dst, same polarity, different qualifier → 분리
  - case 3: same semantic signal 2회 입력 → 1 signal 유지

---

## 제외: Candidate SQL prefilter / explainer goal split

이 항목은 사용자가 직접 **Deferred**로 지정했으므로 이번 후속 작업 지시서에서는 제외한다. 다만 현재 구현이 Python list scan 중심이라는 구조적 사실 자체는 여전히 남아 있다.【turn8file2】

## P1-1. Product aggregate를 batch SQL/group-by 중심으로 개선

### 왜 문제인가
현재 dirty product 재집계는 product별로 wrapped_signal 전체를 읽어 Python aggregate를 다시 만든다.【turn710491view6】 correctness는 맞아졌지만 규모가 커지면 비효율적이다.

### 수정 대상 파일
- `src/mart/aggregate_product_signals.py`
- `src/db/repos/mart_repo.py`
- `src/jobs/run_incremental_pipeline.py`

### 수정 지시
- dirty product set을 batch로 받아 SQL group-by aggregate 경로 추가
- product별 full scan + Python aggregate는 fallback/debug 모드로만 남김
- 30d/90d/all window 집계를 SQL-friendly path로 분리

### Acceptance Criteria
- dirty products N개에 대해 batch aggregate 실행 가능
- batch 결과가 기존 Python aggregate와 동일
- tombstone/relink 후에도 old/new product aggregate 일관성 유지

### 테스트
- `tests/test_batch_aggregate_consistency.py` 신규
  - case 1: batch aggregate == single aggregate
  - case 2: tombstone 후 support 감소 일치
  - case 3: relink 후 old/new product 둘 다 dirty 처리

---

## P1-2. provenance를 review 전용에서 범용 모델로 일반화

### 왜 문제인가
현재 구조는 review-derived fact에는 잘 맞지만, product truth / user fact / manual fact까지 Layer 2에 함께 두는 아키텍처에 비하면 provenance 사고가 아직 review 중심이다.【turn8file2】

### 수정 대상 파일
- `sql/ddl_canonical.sql`
- `src/canonical/canonical_fact_builder.py`
- `src/db/repos/provenance_repo.py`
- 필요 시 `src/user/canonicalize_user_facts.py`
- 필요 시 `src/ingest/product_ingest.py`

### 수정 지시
`fact_provenance`에 아래 컬럼을 추가하거나 재해석한다.
- `source_domain` = `review | user | product | manual | system`
- `source_kind` = `raw | summary | master | derived`
- `source_table`
- `source_row_id`
- `source_modality`는 review-derived facts에 대해서만 2차 메타데이터로 사용

### Acceptance Criteria
- review fact / user fact / product truth fact 모두 provenance로 역추적 가능
- explanation chain은 review fact에는 snippet, product/user fact에는 summary provenance를 반환

### 테스트
- `tests/test_generic_provenance_model.py` 신규
  - case 1: review fact provenance
  - case 2: user fact provenance
  - case 3: product truth provenance

---

## P1-3. concept key 타입을 `concept_id`로 통일

### 왜 문제인가
`build_serving_views.py`는 “Concept IRI fields”라고 적으면서 실제 필드는 `brand_concept_ids`, `category_concept_ids`, `ingredient_concept_ids`, `main_benefit_concept_ids`다.【turn710491view7】 즉 주석/명명과 실제 값 타입이 섞여 있다. 나중에 join/debug를 어렵게 만든다.

### 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/common/ids.py`
- 필요 시 `sql/ddl_concept.sql`

### 수정 지시
- serving/profile/runtime에서는 **`concept_id`를 공식 조인 키로 고정**
- IRI는 canonical/entity layer에서만 사용
- 주석에서 “Concept IRI fields” 표현 제거, `*_concept_ids` 용어로 통일
- candidate generator 주석/로그도 concept_id 기준으로 통일

### Acceptance Criteria
- serving_product_profile / serving_user_profile / candidate overlap 계산이 동일 concept key 타입만 사용
- 주석/필드명/실제 값이 일관됨

### 테스트
- `tests/test_concept_key_consistency.py` 신규
  - case 1: product/user overlap 계산에 동일 key 사용
  - case 2: reviewer proxy가 concept join으로 user와 섞이지 않음

---

## P1-4. `catalog_validation_signal`을 추천 경로에서 완전 분리

### 왜 문제인가
현재 scorer 쪽 excluded family 철학은 맞지만,【turn8file9】 후보 생성이나 standard explanation path에 catalog validation이 간접적으로 섞이면 master truth와 review QA signal의 경계가 흐려진다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- 필요 시 `src/mart/build_serving_views.py`

### 수정 지시
- candidate generation: catalog validation 사용 금지
- scoring: 사용 금지 유지
- standard explanation: 사용 금지
- QA/debug mode에서만 노출 허용

### Acceptance Criteria
- catalog validation은 추천 score/candidate에 영향 없음
- explain path는 review-native / user-native / product truth만 사용
- QA 모드에서만 catalog validation 노출 가능

### 테스트
- 기존 `tests/test_truth_override_protection.py` 확장
  - case 1: candidate overlap에 catalog validation 미반영
  - case 2: scorer에서 excluded family 유지
  - case 3: standard explainer에서 catalog validation 비노출

---

## P1-5. README / ARCHITECTURE / CHANGELOG / tooling 정리

### 왜 문제인가
최신 GitHub root는 description, website, topics가 비어 있고, visible history도 적다.【turn476431view0】 이 프로젝트처럼 계약이 많은 시스템에서는 repo hygiene가 유지보수성에 직접 영향을 준다. 또 `pyproject.toml`에 lint/type/integration tooling이 얇다는 지적도 여전히 유효하다.【turn8file14】

### 수정 대상 파일
- `README.md` (신규)
- `ARCHITECTURE.md` (신규)
- `CHANGELOG.md` (신규)
- `pyproject.toml`

### 수정 지시
- README: 목적, 5-layer + common concept plane, local run, test run, pipeline entrypoints
- ARCHITECTURE: data contracts, invariants, provenance chain, recommendation flow
- CHANGELOG: 큰 구조 변경 기록
- pyproject: `ruff`, `mypy`, `pytest-cov`, Postgres integration test runner 추가 검토

### Acceptance Criteria
- 신규 참여자가 README만 보고 환경 구성 가능
- 아키텍처 원칙과 핵심 invariants가 루트 문서에서 확인 가능
- lint/type/test 도구가 선언됨

### 테스트
- 문서 검토 + CI smoke check

---

## 권장 작업 순서
1. P0-1 `recommended_to` 계약 정리
2. P0-2 reverse `dst_ref_kind`
3. P0-3 `signal_evidence` 정본화
4. P0-4 signal dedup/qualifier key 확정
5. P1-1 batch aggregate SQL
6. P1-2 generic provenance
7. P1-3 concept key 통일
8. P1-4 catalog_validation 완전 분리
9. P1-5 repo hygiene / tooling

## 이번 사이클의 완료 기준
- `recommended_to` direct UserSegment projection이 quarantine 없이 deterministic하게 동작한다.
- reverse transform이 concept/entity ref kind를 정확히 유지한다.
- signal provenance 정본이 `signal_evidence` 하나로 통일된다.
- signal dedup이 polarity/negated/qualifier 차이를 보존한다.
- candidate prefilter와 dirty aggregate가 SQL/batch 쪽으로 이동한다.
- provenance 모델이 review/user/product 사실을 모두 담을 수 있다.
- concept key 타입이 serving/runtime에서 일관된다.
- catalog validation이 추천 경로에서 완전히 분리된다.
