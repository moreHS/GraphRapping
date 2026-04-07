# Step 3 상세 실행 작업지시서 — SQL-first 전환

## 1. 목적
기본 recommendation runtime을 Python full scan에서 SQL-first prefilter 경로로 전환한다.

현재 구조는 Postgres-first hybrid를 표방하지만,
기본 candidate generation은 여전히 Python 리스트 순회 비중이 높다.
catalog와 review volume이 커질수록 이 부분이 병목이 된다.

---

## 2. 목표
1. `generate_candidates_prefiltered()` 또는 동등한 SQL prefilter 경로를 기본 candidate generation path로 승격한다.
2. candidate prefilter 단계에서 category / ingredient / promoted-only / family constraints를 SQL 쪽에서 먼저 처리한다.
3. dirty product aggregate recompute를 더 batch SQL 중심으로 옮긴다.
4. Python path는 overlap scoring / reranking / explanation에 집중한다.

---

## 3. 현재 상태 요약
- `src/rec/candidate_generator.py`
  - `generate_candidates_prefiltered()` 존재
  - 기본 generate path는 여전히 Python product profile 순회
- aggregate recompute도 일부 Python 후처리에 기댄다
- promoted-only contract는 serving builder 수준에선 잘 지켜진다

---

## 4. 방향성 규칙 (불변)
- recommendation correctness가 성능 최적화보다 우선이다.
- SQL prefilter는 **1차 후보 축소** 용도다.
- 최종 점수와 explanation fidelity는 Python scorer/explainer가 책임진다.
- promoted-only / catalog_validation exclusion 규칙은 SQL prefilter에서도 동일하게 유지해야 한다.

---

## 5. 수정 대상 파일

### 핵심 파일
- `src/rec/candidate_generator.py`
- `src/web/server.py`
- `src/db/repos/mart_repo.py`
- `src/jobs/run_incremental_pipeline.py`
- `sql/` (신규 candidate prefilter SQL)
- `tests/test_candidate_prefiltered_equivalence.py`
- `tests/test_incremental_batch_aggregate.py`

### 선택적 신규 파일
- `sql/candidate_prefilter.sql`
- `src/db/repos/candidate_repo.py`

---

## 6. 상세 구현 지시

### 6-1. `generate_candidates_prefiltered()`를 기본 경로로 승격
#### 수정 파일
- `src/rec/candidate_generator.py`
- `src/web/server.py`

#### 해야 할 일
현재 기본 generate path를 아래처럼 정리한다.

```python
def generate_candidates(...):
    if prefiltered_profiles is None:
        prefiltered_profiles = generate_candidates_prefiltered(...)
    ...
```

또는 server endpoint가 기본적으로 prefiltered 경로를 사용하도록 바꾼다.

#### 목적
기본 운영 경로를 SQL-first에 가깝게 만든다.

---

### 6-2. candidate prefilter SQL 추가
#### 신규/수정 파일
- `sql/candidate_prefilter.sql`
- 또는 `src/db/repos/candidate_repo.py`

#### 필수 조건
prefilter 단계에서 최소 아래를 처리한다.

- promoted serving products만
- category 기본 일치 (mode별 penalty/strict 처리 전 1차 필터)
- avoided ingredient exclusion
- exact owned suppression (strict 모드)
- optional: family-aware bucket용 metadata 포함

#### 출력 컬럼 예
- `product_id`
- `variant_family_id`
- `brand_concept_ids`
- `category_concept_ids`
- `ingredient_concept_ids`
- `main_benefit_concept_ids`
- `top_bee_attr_ids`
- `top_keyword_ids`
- `top_context_ids`
- `top_tool_ids`
- `top_coused_product_ids`

#### 목적
Python으로 전체 serving profile을 다 스캔하지 않게 한다.

---

### 6-3. aggregate recompute의 batch SQL path 강화
#### 수정 파일
- `src/db/repos/mart_repo.py`
- `src/jobs/run_incremental_pipeline.py`

#### 해야 할 일
dirty product set이 있을 때 product별 loop select 대신,
가능한 경우 batch query로 `wrapped_signal`을 한 번에 읽고 group-by 후 upsert한다.

#### 최소안
- product별 Python recompute path는 유지
- batch SQL path를 feature flag 또는 repo method로 추가
- 동일 결과를 내는지 비교 테스트 추가

#### 목적
incremental recompute 비용을 줄인다.

---

### 6-4. SQL path와 Python path 동등성 테스트 추가
#### 신규 테스트
- `tests/test_candidate_prefiltered_equivalence.py`
- `tests/test_incremental_batch_aggregate.py`

#### 검증 내용
- 동일 입력에서 prefiltered 경로와 legacy Python 경로의 top-k candidate set이 동일하거나 허용 오차 범위 내인지
- aggregate batch SQL path와 기존 Python path가 동일 signal summary를 만드는지

---

## 7. Acceptance Criteria
1. 기본 recommendation path가 prefiltered SQL 경로를 사용한다.
2. promoted-only / ingredient exclusion / strict category constraints가 SQL prefilter에 반영된다.
3. Python full-scan path는 fallback 또는 비교용으로만 남는다.
4. batch aggregate path가 존재하고 기존 결과와 동등성이 검증된다.

---

## 8. 테스트 항목

### 8-1. `tests/test_candidate_prefiltered_equivalence.py`
- 동일 user/product mock 입력에서 prefiltered vs legacy candidate set 비교

### 8-2. `tests/test_incremental_batch_aggregate.py`
- 동일 wrapped_signal 입력에서 batch SQL aggregate와 Python aggregate 결과 비교

### 8-3. `tests/test_promoted_only_contract.py`
- SQL prefilter 경로에서도 promoted-only contract가 깨지지 않는지 재검증

---

## 9. 완료 후 검토 포인트
- prefilter SQL이 커지면 view/materialized view로 올릴지 검토
- Postgres query plan / index tuning 검토
