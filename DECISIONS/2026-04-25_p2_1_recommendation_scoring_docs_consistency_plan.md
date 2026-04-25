# P2-1 Recommendation Scoring / UI / Docs Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** recommendation scoring config, backend scorer, frontend weight controls, explanation, README 실행 명령을 같은 계약으로 맞춘다.

**Architecture:** 추천 레이어는 candidate overlap -> scorer feature -> explanation path -> UI slider/docs가 같은 feature 이름을 공유해야 한다. `goal_review`는 candidate generator에서 이미 제거된 dead path이므로 scorer/explainer/UI/docs에서도 제거한다. negative contribution은 scoring debug와 explanation에서 누락되지 않게 `feature_contributions`에 보존한다.

**Tech Stack:** Python 3.11, YAML config, vanilla JS frontend, pytest, markdown docs.

---

## 현재 문제

### 1. `goal_review` dead path

`src/rec/candidate_generator.py`는 이미 아래 주석과 함께 `goal_review` 생성을 제거했다.

```python
# NOTE: goal_review (goal × concern cross-match) removed — different concept planes
# cannot match through separate resolvers. Use concern_bridge instead.
```

하지만 아래 파일에는 여전히 남아 있다.

- `configs/scoring_weights.yaml`: `goal_fit_review_signal`
- `src/rec/scorer.py`: `goal_review_count`, `goal_fit_review_signal`
- `src/rec/explainer.py`: `_EDGE_MAP["goal_review"]`, `_concept_to_feature("goal_review")`
- `src/static/app.js`: `DEFAULT_WEIGHTS.goal_fit_review_signal`, UI label/desc
- `README.md`, `ARCHITECTURE.md`: feature list에 goal review 포함
- 일부 tests: goal_review 존재를 기대

### 2. Backend/frontend weight mismatch

현재 YAML과 frontend 기본값이 다르다.

예:
- YAML `keyword_match: 0.17`
- frontend `keyword_match: 0.20`

UI에서 slider를 건드리지 않으면 server YAML을 쓰지만, 화면의 기본 숫자는 다른 값을 보여준다. 운영/디버그 혼선이 생긴다.

### 3. Negative contribution 누락

`src/rec/scorer.py`는 contribution을 아래처럼 만든다.

```python
contributions = {k: weight * v for k, v in features.items() if v > 0}
```

`exact_owned_penalty`, `owned_family_penalty` 같은 음수 feature가 explanation/debug에서 빠진다.

`src/rec/explainer.py`도 `contribution > 0`만 explanation path에 포함한다.

### 4. README 실행 명령 불일치

README는 아래 명령을 안내한다.

```bash
python -m src.jobs.run_daily_pipeline --kg-mode=on
python -m src.web.server
```

하지만 현재 두 모듈은 CLI entrypoint가 완성되어 있지 않다. 또한 `pyproject.toml`에는 `fastapi`, `pydantic`, `uvicorn`이 dependency에 없다.

## 비목표

- 추천 알고리즘 자체를 새로 설계하지 않는다.
- frontend를 대규모 리디자인하지 않는다.
- FastAPI 서버 CLI 전체 구현은 별도 항목으로 분리할 수 있다.
- 현 dirty 파일의 사용자 변경을 되돌리지 않는다.

## 변경 파일

- Modify: `configs/scoring_weights.yaml`
- Modify: `src/rec/scorer.py`
- Modify: `src/rec/explainer.py`
- Modify: `src/static/app.js`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `pyproject.toml`
- Modify tests referencing `goal_fit_review_signal` / `goal_review`
- Add: `tests/test_recommendation_contract_consistency.py`

## 설계

### 1. Feature contract source of truth

YAML `features`를 backend/frontend/docs의 source of truth로 본다.

P2-1 이후 active feature set:

```text
keyword_match
residual_bee_attr_match
context_match
concern_fit
concern_bridge_fit
ingredient_match
brand_match_conf_weighted
goal_fit_master
category_affinity
freshness_boost
skin_type_fit
purchase_loyalty_score
novelty_bonus
exact_owned_penalty
owned_family_penalty
same_family_explore_bonus
repurchase_family_affinity
tool_alignment
coused_product_bonus
```

`goal_fit_review_signal`은 제거한다.

### 2. Negative contribution contract

`Scorer.score()`는 0이 아닌 contribution을 모두 보존한다.

```python
contributions = {
    k: self._weights.get(k, 0.0) * v
    for k, v in features.items()
    if v != 0
}
```

`Explainer.explain()`은 positive/negative contribution을 모두 path로 만들되, 정렬은 `abs(contribution)` 기준으로 한다.

### 3. Frontend weight sync

`src/static/app.js::DEFAULT_WEIGHTS`는 YAML과 같은 값을 갖게 한다.

단기적으로는 수동 동기화와 regression test로 고정한다. 장기적으로는 `/api/config/scoring` 같은 endpoint로 server config를 내려주는 방식을 고려한다.

### 4. README dependency/command sync

`pyproject.toml`에 web dependency를 추가한다.

```toml
"fastapi>=0.110",
"pydantic>=2.0",
"uvicorn>=0.29",
```

README는 현재 실제 가능한 명령만 안내한다.

```bash
python -m pytest tests/ -q
python -m ruff check src --statistics
```

서버 명령은 CLI가 완성되기 전까지 "planned" 또는 uvicorn module path로 명시한다.

## Task 1: Recommendation contract tests

**Files:**
- Add: `tests/test_recommendation_contract_consistency.py`

- [x] Step 1: Add test that YAML feature keys match scorer active features.

- [x] Step 2: Add test that `goal_fit_review_signal` is absent from YAML/scorer/explainer/frontend.

- [x] Step 3: Add test that negative contribution is retained for owned family penalty.

- [x] Step 4: Add test that frontend `DEFAULT_WEIGHTS` values match YAML.

## Task 2: Remove `goal_review` dead path

**Files:**
- Modify: `configs/scoring_weights.yaml`
- Modify: `src/rec/scorer.py`
- Modify: `src/rec/explainer.py`
- Modify: `src/static/app.js`
- Modify tests referencing goal review

- [x] Step 1: Remove `goal_fit_review_signal` from YAML.

- [x] Step 2: Remove `goal_review_count` and `goal_fit_review_signal` from scorer features.

- [x] Step 3: Remove `goal_review` from explainer `_EDGE_MAP`, `_concept_to_feature()`, and Korean summary branch.

- [x] Step 4: Remove frontend slider/default/meta text for `goal_fit_review_signal`.

- [x] Step 5: Update tests that still expect `goal_review`.

## Task 3: Preserve negative contributions

**Files:**
- Modify: `src/rec/scorer.py`
- Modify: `src/rec/explainer.py`
- Modify tests

- [x] Step 1: Change scorer contribution filter from `v > 0` to `v != 0`.

- [x] Step 2: Change explainer path filter from `contribution > 0` to `contribution != 0`.

- [x] Step 3: Sort explanation paths by `abs(contribution)`.

- [x] Step 4: Add/adjust UI rendering so negative contribution displays with sign.

## Task 4: Sync frontend weights

**Files:**
- Modify: `src/static/app.js`
- Add/modify: `tests/test_recommendation_contract_consistency.py`

- [x] Step 1: Copy YAML feature weights into `DEFAULT_WEIGHTS`.

- [x] Step 2: Ensure frontend has no active key absent from YAML.

- [x] Step 3: Run contract test.

## Task 5: Docs/dependency sync

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `pyproject.toml`

- [x] Step 1: Update feature count/list in README and ARCHITECTURE.

- [x] Step 2: Add missing web dependencies to `pyproject.toml`.

- [x] Step 3: Replace invalid CLI examples or mark them as planned until entrypoints exist.

## Task 6: Verification

- [x] Run recommendation tests.

```bash
python -m pytest tests/test_recommendation.py tests/test_recommendation_contract_consistency.py tests/test_family_id_normalization.py tests/test_family_level_personalization.py -q
```

- [x] Run full test suite.

```bash
python -m pytest tests/ -q
```

- [x] Run static check as observation.

```bash
python -m ruff check src --statistics
```

## Completion Record

Date: 2026-04-25

Changed files:
- `configs/scoring_weights.yaml`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- `src/rec/candidate_generator.py`
- `src/static/app.js`
- `README.md`
- `ARCHITECTURE.md`
- `pyproject.toml`
- `tests/test_recommendation_contract_consistency.py`
- `tests/test_recommendation.py`
- `tests/test_catalog_validation_exclusion.py`
- `tests/test_family_id_normalization.py`
- `tests/test_family_level_personalization.py`
- `tests/test_coused_product_and_tool_features.py`
- `tests/test_texture_preference_flow.py`
- `tests/test_integration_real_data.py`

Implemented:
- `goal_fit_review_signal` / `goal_review` active path를 scoring config, scorer, explainer, frontend, docs에서 제거했다.
- `SCORING_FEATURE_KEYS`를 추가해 backend active feature contract를 명시했다.
- scorer가 음수 contribution을 `feature_contributions`에 보존하도록 변경했다.
- explainer가 음수 contribution path를 생성하고 `abs(contribution)` 기준으로 정렬하도록 변경했다.
- recommendation UI 기본 weight를 YAML과 동기화하고, 음수 contribution을 음수 부호/색상으로 표시하게 했다.
- README/ARCHITECTURE의 feature count/list를 현재 19개 feature contract로 갱신했다.
- `pyproject.toml`에 web server dependency(`fastapi`, `pydantic`, `uvicorn`)를 추가했다.
- README의 invalid module CLI 예시는 제거하고, 현재 가능한 `uvicorn src.web.server:app --reload` 경로로 정리했다.

Focused tests:
- `python -m pytest tests/test_recommendation.py tests/test_recommendation_contract_consistency.py tests/test_family_id_normalization.py tests/test_family_level_personalization.py tests/test_coused_product_and_tool_features.py tests/test_texture_preference_flow.py tests/test_catalog_validation_exclusion.py -q`
- Result: `41 passed`

Full tests:
- `python -m pytest tests/ -q`
- Result: `324 passed`

Static check:
- `python -m ruff check src/rec/scorer.py src/rec/explainer.py src/rec/candidate_generator.py tests/test_recommendation_contract_consistency.py`
- Result: `All checks passed!`
- `python -m ruff check src --statistics`
- Result: `42 errors` remain globally (`28 F401`, `9 E402`, `4 E741`, `1 F541`)

Remaining issues:
- DB migration and batch pipeline remain library entrypoints, not CLI commands.
- 전체 ruff 잔여 이슈는 아직 별도 cleanup 항목으로 남아 있다.

Next priority:
- Global lint cleanup and/or real Postgres integration verification

## Rollback / safety

If removing `goal_fit_review_signal` causes scoring regressions:
- keep the config key with weight `0.0` for one compatibility cycle.
- keep candidate generator as source of truth: no new `goal_review` overlaps.
