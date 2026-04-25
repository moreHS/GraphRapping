# P0-1 Product Match / Data Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mock/product/review 계약을 복구해 review -> product match -> canonical fact -> signal 흐름이 실제로 살아 있음을 보장한다.

**Architecture:** Product matching을 brand-prefix aware하게 만들고, mock fixture 계약을 최신 실제 데이터와 맞춘다. smoke test는 loader 전체 경로를 사용해 mock review batch에서 최소 하나 이상의 signal이 만들어지는지 검증한다.

**Tech Stack:** Python 3.11, pytest, existing loader/pipeline modules.

---

## 현재 문제

현재 `mockdata/review_triples_raw.json` 15개를 `mockdata/product_catalog_es.json`와 함께 로드하면 모든 리뷰가 `QUARANTINE`으로 떨어지고 signal이 0이다.

확인된 예:

```text
review prod_nm: 워터뱅크 블루 히알루로닉 세럼
review brnd_nm: 라네즈
catalog prd_nm: 라네즈 워터뱅크 블루 히알루로닉 세럼
catalog brand: 라네즈
match score: 0.8888888888888888
match method: fuzzy_manual_review
status: QUARANTINE
```

문제의 본질:
- catalog `prd_nm`에 brand prefix가 포함되어 있다.
- review `prod_nm`에는 brand prefix가 빠져 있다.
- `ProductIndex.build()`의 normalized key가 `brand|product_name` 하나뿐이다.
- fuzzy auto threshold가 0.93이라 prefix 차이만으로도 quarantine된다.
- `strip_brand_prefixes`는 import되어 있지만 실질적으로 사용되지 않는다.

## 비목표

- fuzzy threshold를 무작정 낮추지 않는다.
- 다른 브랜드 상품까지 auto match되는 broad fuzzy를 만들지 않는다.
- relation/BEE promotion 로직은 이 작업에서 수정하지 않는다.
- quarantine UI 집계는 P0-2에서 별도 처리한다.

## 변경 파일

- Modify: `src/common/text_normalize.py`
- Modify: `src/link/product_matcher.py`
- Modify: `mockdata/README.md`
- Modify or add: `tests/test_product_matcher.py`
- Add: `tests/test_mock_pipeline_smoke.py`

## 설계

### 1. Brand-prefix normalization

`strip_brand_prefixes(name, brand_names=None)` 형태로 확장한다.

동작:
- `normalize_text()`는 그대로 둔다.
- `strip_brand_prefixes()`는 괄호 텍스트 제거 후 normalize한다.
- `brand_names`가 주어지면 normalized product name이 normalized brand로 시작할 때만 prefix를 제거한다.
- prefix 제거는 단어 경계가 있는 경우만 수행한다.

예:

```python
strip_brand_prefixes("라네즈 워터뱅크 세럼", ["라네즈"]) == "워터뱅크 세럼"
strip_brand_prefixes("구딸 대표 에센스", ["구딸"]) == "대표 에센스"
strip_brand_prefixes("라네즈워터뱅크", ["라네즈"]) == "라네즈워터뱅크"
```

### 2. ProductIndex secondary keys

`ProductIndex`에 brand-stripped normalized key를 추가한다.

권장 방식:
- dataclass 필드 추가: `norm_stripped: dict[str, str]`
- build 시 아래 키를 함께 등록한다.

```text
brand_norm | normalized product_name
brand_norm | brand-stripped product_name
```

`match_product()` 순서:
1. exact normalized key
2. stripped normalized key
3. alias
4. brand-filtered fuzzy
5. quarantine

`stripped_norm_exact` 매칭은 score 0.99, status `NORM` 또는 `ALIAS` 중 하나로 둔다.

권장:
- 같은 catalog에서 유도되는 deterministic normalized key이므로 `MatchStatus.NORM`
- method는 `"norm_brand_stripped"`

### 3. Ambiguity guard

brand-stripped key가 여러 product_id로 충돌하면 자동 매칭하면 안 된다.

간단한 방어:
- `norm_stripped`는 `dict[str, str]` 대신 `dict[str, list[str]]`로 둘 수 있다.
- lookup 결과가 1개면 auto match.
- 2개 이상이면 fuzzy/manual review로 내려간다.

권장:
- 첫 구현에서는 충돌 방어를 넣는다.
- test로 같은 브랜드 내 동일 stripped name 2개가 있으면 auto match하지 않음을 보장한다.

### 4. Mock smoke test

새 테스트 `tests/test_mock_pipeline_smoke.py`를 추가한다.

테스트 목적:
- 실제 mock product/user/review fixture를 로드한다.
- `run_batch()` 또는 `load_demo_data()`를 통해 end-to-end를 실행한다.
- 최소 조건을 확인한다.

기대 조건:

```python
assert state.review_count == 15
assert state.product_count == 47
assert state.user_count == 50
assert matched_count > 0
assert total_signals > 0
```

주의:
- P0-2 전까지 `state.quarantine_stats`는 아직 0일 수 있으므로 여기서는 검증하지 않는다.
- KG mode 기본이 web state에서 `on`이므로 `load_demo_data()` 기준 smoke가 적합하다.

### 5. mockdata README 갱신

현재 문서는 product 12/user 3으로 되어 있으나 실제 fixture는 product 47/user 50/review 15다.

수정:
- 파일 목록 레코드 수 갱신
- product name 계약에 "catalog 상품명은 brand prefix를 포함할 수 있고, matcher는 brand-aware stripped key를 사용한다" 명시
- relation loader와 rs_jsonl loader는 동시에 사용하는 것이 아니라 입력 포맷별 adapter라는 설명 추가

## Task 1: Brand-prefix normalization tests

**Files:**
- Modify: `src/common/text_normalize.py`
- Test: `tests/test_product_matcher.py`

- [ ] Step 1: Add tests for brand-prefix stripping.

Add cases:

```python
from src.common.text_normalize import strip_brand_prefixes


def test_strip_brand_prefix_with_known_brand():
    assert strip_brand_prefixes("라네즈 워터뱅크 블루 히알루로닉 세럼", ["라네즈"]) == "워터뱅크 블루 히알루로닉 세럼"


def test_strip_brand_prefix_does_not_strip_without_boundary():
    assert strip_brand_prefixes("라네즈워터뱅크", ["라네즈"]) == "라네즈워터뱅크"
```

- [ ] Step 2: Run failing tests.

Run:

```bash
python -m pytest tests/test_product_matcher.py -q
```

Expected:
- New strip-prefix tests fail before implementation.

- [ ] Step 3: Implement `strip_brand_prefixes(name, brand_names=None)`.

Implementation constraints:
- Preserve current behavior when `brand_names` is omitted.
- Use `normalize_text()`.
- Strip only when `name_norm == brand_norm` is false and `name_norm.startswith(brand_norm + " ")`.

- [ ] Step 4: Run tests again.

Run:

```bash
python -m pytest tests/test_product_matcher.py -q
```

Expected:
- All product matcher tests pass.

## Task 2: ProductIndex stripped key matching

**Files:**
- Modify: `src/link/product_matcher.py`
- Test: `tests/test_product_matcher.py`

- [ ] Step 1: Add product matcher regression test.

Test intent:

```python
def test_brand_prefixed_catalog_matches_unprefixed_review_name():
    products = [
        {"product_id": "P002", "product_name": "라네즈 워터뱅크 블루 히알루로닉 세럼", "brand_name": "라네즈"},
    ]
    index = ProductIndex.build(products)
    result = match_product("라네즈", "워터뱅크 블루 히알루로닉 세럼", index)
    assert result.matched_product_id == "P002"
    assert result.match_status == MatchStatus.NORM
    assert result.match_method == "norm_brand_stripped"
```

Also add ambiguity test:

```python
def test_brand_stripped_key_collision_does_not_auto_match():
    products = [
        {"product_id": "P1", "product_name": "라네즈 워터뱅크 세럼", "brand_name": "라네즈"},
        {"product_id": "P2", "product_name": "라네즈 워터뱅크 세럼", "brand_name": "라네즈"},
    ]
    index = ProductIndex.build(products)
    result = match_product("라네즈", "워터뱅크 세럼", index)
    assert not (result.match_status == MatchStatus.NORM and result.match_method == "norm_brand_stripped")
```

- [ ] Step 2: Run failing tests.

Run:

```bash
python -m pytest tests/test_product_matcher.py -q
```

Expected:
- Brand-stripped match test fails before implementation.

- [ ] Step 3: Implement stripped key index.

Implementation outline:
- Add `norm_stripped: dict[str, list[str]]` to `ProductIndex`.
- In `build()`, compute stripped key with `strip_brand_prefixes(pname, [bname])`.
- Only add stripped key when stripped product name differs from raw normalized product name.
- In `match_product()`, compute stripped raw product key using same helper.
- If exactly one product id for stripped key, return `MatchStatus.NORM`, score `0.99`, method `"norm_brand_stripped"`.
- If more than one, continue to alias/fuzzy.

- [ ] Step 4: Run product matcher tests.

Run:

```bash
python -m pytest tests/test_product_matcher.py -q
```

Expected:
- Product matcher tests pass.

## Task 3: Mock pipeline smoke regression

**Files:**
- Add: `tests/test_mock_pipeline_smoke.py`

- [ ] Step 1: Add smoke test.

Use `load_demo_data()` with actual mock fixtures:

```python
import json

from src.web.state import load_demo_data


def test_mock_relation_fixture_generates_at_least_one_signal():
    products = json.load(open("mockdata/product_catalog_es.json", encoding="utf-8"))
    users = json.load(open("mockdata/user_profiles_normalized.json", encoding="utf-8"))

    state = load_demo_data(
        "mockdata/review_triples_raw.json",
        products,
        users,
        max_reviews=15,
        source="test_mock_smoke",
        review_format="relation",
    )

    matched = [
        r for r in state.batch_result.get("review_results", [])
        if r.get("matched_product_id")
    ]

    assert state.review_count == 15
    assert state.product_count == 47
    assert state.user_count == 50
    assert len(matched) > 0
    assert state.batch_result.get("total_signals", 0) > 0
```

- [ ] Step 2: Run smoke test.

Run:

```bash
python -m pytest tests/test_mock_pipeline_smoke.py -q
```

Expected:
- Fails before ProductIndex stripped-key fix.
- Passes after Task 2.

## Task 4: mockdata README update

**Files:**
- Modify: `mockdata/README.md`

- [ ] Step 1: Update fixture counts.

Expected content:
- `product_catalog_es.json`: 47 records
- `user_profiles_normalized.json`: 50 records
- `review_triples_raw.json`: 15 records

- [ ] Step 2: Add loader usage note.

Add:

```markdown
`relation_loader` and `rs_jsonl_loader` are input-format adapters. A normal pipeline run selects one loader. They are not both required unless a batch intentionally merges multiple source formats after converting both to `RawReviewRecord`.
```

- [ ] Step 3: Add product matching contract note.

Add:

```markdown
Catalog `prd_nm` may include the brand prefix while review `prod_nm` may omit it. Product matching is brand-aware and uses a brand-stripped normalized key before fuzzy matching.
```

## Task 5: Verification

- [ ] Run focused tests.

```bash
python -m pytest tests/test_product_matcher.py tests/test_mock_pipeline_smoke.py -q
```

Expected:
- All pass.

- [ ] Run full tests.

```bash
python -m pytest tests/ -q
```

Expected:
- All pass.

- [ ] Run static check as observation.

```bash
python -m ruff check src --statistics
```

Expected:
- Existing ruff errors may remain unless separately fixed.
- Do not claim ruff clean in this P0-1 unless it is actually clean.

## Completion record template

After implementation, append:

```markdown
## Completion Record

Date:
Changed files:
Focused tests:
Full tests:
Remaining issues:
Next priority:
```

## Rollback / safety

If brand-stripped key creates false positives:
- Keep exact normalized match first.
- Only allow stripped auto match when brand matches and stripped key has exactly one product id.
- Otherwise fall through to existing fuzzy/quarantine behavior.

## Completion Record

Date: 2026-04-25

Changed files:
- `src/common/text_normalize.py`
- `src/link/product_matcher.py`
- `tests/test_product_matcher.py`
- `tests/test_mock_pipeline_smoke.py`
- `mockdata/README.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`
- `DECISIONS/2026-04-25_p0_1_product_match_data_contract_plan.md`

Implemented:
- Added brand-aware prefix stripping for product names.
- Added `ProductIndex.norm_stripped` with ambiguity protection.
- Added deterministic `norm_brand_stripped` and `norm_input_brand_stripped` match methods.
- Added mock pipeline smoke regression proving checked-in mock relation fixture now produces signals.
- Updated mockdata README fixture counts and loader contract.

Focused tests:
- `python -m pytest tests/test_product_matcher.py -q` -> 11 passed
- `python -m pytest tests/test_mock_pipeline_smoke.py -q` -> 1 passed

Full tests:
- `python -m pytest tests/ -q` -> 300 passed

Static check:
- `python -m ruff check src --statistics` -> 87 errors remain
  - 45 F401 unused-import
  - 37 E402 module-import-not-at-top-of-file
  - 4 E741 ambiguous-variable-name
  - 1 F541 f-string-missing-placeholders

Remaining issues:
- P0-2 quarantine batch/web summary still needs separate fix.
- ruff cleanup remains outside P0-1 scope.

Next priority:
- P0-2 Quarantine aggregation/display recovery.
