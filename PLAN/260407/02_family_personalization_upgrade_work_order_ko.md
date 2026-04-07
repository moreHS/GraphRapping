# Step 2 상세 실행 작업지시서 — Family 활용 심화

## 1. 목적
exact SKU / same family other variant / repurchased family를 **1급 personalization 신호**로 승격한다.

현재는 penalty/bonus feature가 들어갔고 key mismatch도 대부분 해결됐지만,
family를 아직 주로 scorer feature 수준으로만 사용한다.
뷰티 도메인에서는 family가 추천 탐색 단위로 매우 중요하므로,
candidate generation, serving profile, explanation까지 더 강하게 반영한다.

---

## 2. 목표
1. family identity를 product-level과 완전히 일관된 key 체계로 사용한다.
2. exact owned / same family other variant / repurchased family를 별도 candidate bucket으로 구분한다.
3. serving profile에 family-level summary를 추가하거나, 최소한 family-aware candidate generation을 표준화한다.
4. scorer mode별(strict/explore/compare) family 규칙을 명확히 문서화/테스트한다.

---

## 3. 현재 상태 요약
- `src/loaders/product_loader.py`
  - `REPRESENTATIVE_PROD_CODE -> variant_family_id`
- `src/user/adapters/personal_agent_adapter.py`
  - `OWNS_FAMILY`, `REPURCHASES_FAMILY`를 product ref로 생성
- `src/rec/candidate_generator.py`
  - `already_owned`, `owned_family_match`, `repurchased_family_match`
- `src/rec/scorer.py`
  - `exact_owned_penalty`
  - `owned_family_penalty`
  - `same_family_explore_bonus`
  - `repurchase_family_affinity`

구조는 좋아졌지만, family는 아직 1급 candidate bucket/serving summary로는 약하다.

---

## 4. 방향성 규칙 (불변)
- exact SKU와 same family other variant는 절대 동일 취급하지 않는다.
- same family other variant는 **strict에서는 보수적**, **explore에서는 적극적**으로 다룬다.
- repurchased family는 loyalty/comfort zone 신호지만, novelty와 균형 있게 본다.
- family key는 전 경로에서 동일한 canonical ref를 사용한다.

---

## 5. 수정 대상 파일

### 핵심 파일
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/mart/build_serving_views.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/common/enums.py` (필요 시 recommendation mode 확장)
- `tests/test_family_level_personalization.py`
- `tests/test_family_id_normalization.py`

### 선택적 신규 파일
- `tests/test_family_candidate_buckets.py`
- `tests/test_family_explanation.py`

---

## 6. 상세 구현 지시

### 6-1. family key canonicalization helper 통일
#### 해야 할 일
공통 helper 추가:

```python
def normalize_family_ref(value: str | None) -> str | None:
    """Accept raw family id or product:{family_id} and return canonical family key."""
```

#### 적용 위치
- `src/user/adapters/personal_agent_adapter.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/mart/build_serving_views.py`

#### 목적
family comparison이 raw id / product ref 혼용 때문에 흔들리지 않게 한다.

---

### 6-2. Candidate bucket을 3개로 분리
#### 수정 파일
- `src/rec/candidate_generator.py`

#### 해야 할 일
`CandidateProduct`에 아래 필드가 이미 있으면 유지하고, bucket을 추가한다.

```python
candidate_bucket: Literal[
    "EXACT_OWNED",
    "SAME_FAMILY_OTHER_VARIANT",
    "NON_FAMILY"
]
```

또는
```python
is_exact_owned: bool
is_same_family_other_variant: bool
is_repurchased_family: bool
```

#### 기본 규칙
- `exact owned`:
  - strict: exclude 또는 강한 penalty
  - explore: 강한 penalty
- `same family other variant`:
  - strict: mild penalty 또는 optional skip
  - explore: explore bonus 적용
- `repurchased family`:
  - brand/category loyalty와 별도 신호로 사용

#### 목적
family를 단순 overlap feature가 아니라 추천 탐색 단위로 승격한다.

---

### 6-3. serving product profile에 family summary 추가 검토
#### 수정 파일
- `src/mart/build_serving_views.py`

#### 최소안
profile에 이미 `variant_family_id`가 있으면 유지.
추가로 선택적으로 아래를 넣는다.

- `family_review_count`
- `family_top_keyword_ids`
- `family_top_bee_attr_ids`

#### 목적
same family 후보를 더 설명 가능하게 만들기 위함.

#### 주의
이번 사이클에 과도한 family aggregate까지는 필수 아님.
최소한 candidate/scorer에서 쓰기 쉬운 family metadata만 명시해도 된다.

---

### 6-4. scorer의 mode별 family 규칙 명문화
#### 수정 파일
- `src/rec/scorer.py`
- `configs/scoring_weights.yaml`

#### 해야 할 일
strict/explore/compare 각각에 대해 아래 규칙을 명확히 구현/주석화한다.

예:
- `STRICT`
  - exact owned: 큰 penalty
  - same family other variant: penalty 유지
- `EXPLORE`
  - exact owned: penalty
  - same family other variant: `same_family_explore_bonus` 허용
- `COMPARE`
  - same family 신호는 neutral 또는 약한 bonus

#### 목적
family bonus/penalty가 mode에 따라 왜 다른지 코드 수준에서 분명히 한다.

---

### 6-5. explanation에 family reasoning 추가
#### 수정 파일
- `src/rec/explainer.py`

#### 해야 할 일
family 관련 설명을 아래처럼 지원한다.

예:
- "이미 사용 중인 동일 라인 제품군과 가까운 변형이라 적응 비용이 낮습니다."
- "동일 family이지만 다른 variant라서 탐색 추천으로 적합합니다."
- "최근 반복 구매한 family와 유사한 제품군입니다."

#### 목적
same family 추천이 왜 나왔는지 설명 가능하게 한다.

---

## 7. Acceptance Criteria
1. exact SKU / same family other variant / repurchased family가 실제로 서로 다른 경로로 분기된다.
2. family key mismatch로 false negative가 발생하지 않는다.
3. strict/explore/compare 모드에서 family bonus/penalty가 다르게 적용된다.
4. family 관련 추천 이유가 explanation에 노출될 수 있다.

---

## 8. 테스트 항목

### 8-1. `tests/test_family_id_normalization.py`
- raw family id vs `product:{family_id}`가 동일 canonical key로 수렴하는지 검증

### 8-2. `tests/test_family_level_personalization.py`
- same family 다른 variant가 `owned_family_match=True`
- exact owned는 `already_owned=True`
- repurchased family가 scorer bonus로 반영되는지 검증

### 8-3. `tests/test_family_candidate_buckets.py`
- candidate bucket이 exact/same-family/non-family로 분리되는지 확인

### 8-4. `tests/test_family_explanation.py`
- family 추천일 때 explanation에 family reasoning이 노출되는지 확인

---

## 9. 완료 후 검토 포인트
- shade-level personalization까지 확장할지 여부
- family-level aggregate mart를 별도로 둘지 여부
