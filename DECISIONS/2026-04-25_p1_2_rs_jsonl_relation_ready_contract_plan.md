# P1-2 rs.jsonl Relation-Ready Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** relation model 서빙 전/후 rs.jsonl 입력의 의미를 코드, 테스트, 문서, 관측 지표로 분리한다.

**Architecture:** `rs_jsonl_loader`는 relation 없는 rs.jsonl을 계속 valid input으로 허용한다. 다만 BEE span은 relation 없이 추천 signal로 억지 승격하지 않고, loader-level contract stats에 `relation_pending` 상태를 남긴다. relation model 서빙 후 relation 포함 rs.jsonl이 들어오면 같은 loader가 relation rows를 `RawReviewRecord.relation`으로 보존한다.

**Tech Stack:** Python 3.11, loader utilities, pytest, markdown docs.

---

## 현재 문제

`mockdata/review_rs_samples.json`는 relation model 서빙 전 상태라 `relation: []`가 정상이다. 하지만 코드/문서상으로는 아래가 명확히 분리되어 있지 않다.

- relation 없는 rs.jsonl: valid but relation-pending input
- relation 포함 rs.jsonl: relation-ready input

이 구분이 없으면 나중에 BEE-only rs.jsonl을 target-linked BEE처럼 오해하거나, relation model이 붙은 뒤에도 관측 지표 없이 입력 품질을 판단하게 된다.

## 확정 원칙

- relation 없는 BEE는 자동 승격하지 않는다.
- loader는 relation 없는 rs.jsonl을 실패 처리하지 않는다.
- relation 포함 rs.jsonl은 production canonical input으로 간주한다.
- 관측 지표는 loader input contract 수준에서 계산한다.
- 최종 BEE attribution의 source of truth는 `process_review()`/relation 기반 attribution path다.

## 비목표

- relation model 자체를 구현하지 않는다.
- relation 없는 BEE를 heuristic으로 product target에 붙이지 않는다.
- `wrapped_signal` promotion metadata 관통은 P1-3에서 처리한다.
- 두 loader를 동시에 실행하는 merge pipeline을 만들지 않는다.

## 변경 파일

- Modify: `src/loaders/rs_jsonl_loader.py`
- Modify: `src/web/state.py`
- Modify: `mockdata/SCHEMA_RS_JSONL.md`
- Add: `tests/test_rs_jsonl_contract_stats.py`

## 설계

### 1. Loader contract stats

`rs_jsonl_loader`에 record-level stats helper를 추가한다.

```python
def summarize_rs_jsonl_contract(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total_records": ...,
        "bee_span_count": ...,
        "relation_row_count": ...,
        "relation_ready_review_count": ...,
        "relation_pending_review_count": ...,
        "ner_bee_relation_count": ...,
        "bee_without_relation_count": ...,
    }
```

의미:
- `relation_ready_review_count`: `relation[]`가 1개 이상인 review 수
- `relation_pending_review_count`: `bee_spans[]`는 있지만 `relation[]`가 비어 있는 review 수
- `ner_bee_relation_count`: `source_type == "NER-BeE"`이거나 object가 BEE label로 보이는 relation row 수
- `bee_without_relation_count`: 전체 BEE span 수에서 NER-BeE relation row 수를 뺀 값

### 2. Loader with report

기존 API는 유지한다.

```python
load_reviews_from_rs_jsonl(...)
stream_reviews_from_rs_jsonl(...)
```

신규 API를 추가한다.

```python
def load_reviews_from_rs_jsonl_with_report(file_path, max_count=None) -> tuple[list[RawReviewRecord], dict[str, int]]:
    ...
```

### 3. Web demo state exposure

`DemoState`에 아래 필드를 추가한다.

```python
input_contract_stats: dict[str, int] = field(default_factory=dict)
```

`review_format == "rs_jsonl"`일 때는 `load_reviews_from_rs_jsonl_with_report()`를 사용해 stats를 저장하고, `batch_result["input_contract_stats"]`에도 넣는다.

### 4. Documentation

`mockdata/SCHEMA_RS_JSONL.md`에 relation-ready 상태를 명시한다.

```text
relation[] empty + bee_spans[] non-empty = relation_pending
relation[] non-empty = relation_ready
```

## Task 1: Loader stats tests

**Files:**
- Add: `tests/test_rs_jsonl_contract_stats.py`

- [x] Step 1: Add tests for relation-pending input.

```python
from src.loaders.rs_jsonl_loader import summarize_rs_jsonl_contract


def test_relation_empty_bee_records_are_relation_pending():
    stats = summarize_rs_jsonl_contract([
        {"id": "r1", "bee_spans": [{"text": "촉촉", "label": "보습력"}], "relation": []},
    ])
    assert stats["total_records"] == 1
    assert stats["bee_span_count"] == 1
    assert stats["relation_row_count"] == 0
    assert stats["relation_pending_review_count"] == 1
```

- [x] Step 2: Add tests for relation-ready input.

```python
def test_relation_present_records_are_relation_ready():
    stats = summarize_rs_jsonl_contract([
        {
            "id": "r1",
            "bee_spans": [{"text": "촉촉", "label": "보습력"}],
            "relation": [
                {
                    "subject": {"word": "Review Target", "entity_group": "PRD"},
                    "object": {"word": "촉촉", "entity_group": "보습력"},
                    "relation": "has_attribute",
                    "source_type": "NER-BeE",
                },
            ],
        },
    ])
    assert stats["relation_ready_review_count"] == 1
    assert stats["ner_bee_relation_count"] == 1
```

## Task 2: Loader report implementation

**Files:**
- Modify: `src/loaders/rs_jsonl_loader.py`

- [x] Step 1: Extract file parsing into `_read_rs_records(file_path, max_count=None)`.

- [x] Step 2: Make `stream_reviews_from_rs_jsonl()` iterate parsed records.

- [x] Step 3: Add `summarize_rs_jsonl_contract(records)`.

- [x] Step 4: Add `load_reviews_from_rs_jsonl_with_report(file_path, max_count=None)`.

- [x] Step 5: Run loader tests.

```bash
python -m pytest tests/test_rs_jsonl_transform.py tests/test_rs_jsonl_contract_stats.py -q
```

## Task 3: Web state observability

**Files:**
- Modify: `src/web/state.py`

- [x] Step 1: Add `input_contract_stats` field to `DemoState`.

- [x] Step 2: Use `load_reviews_from_rs_jsonl_with_report()` for `review_format == "rs_jsonl"`.

- [x] Step 3: Put stats into `demo_state.input_contract_stats` and `batch_result["input_contract_stats"]`.

## Task 4: Docs

**Files:**
- Modify: `mockdata/SCHEMA_RS_JSONL.md`

- [x] Step 1: Add relation-ready status section under relation schema.

- [x] Step 2: State explicitly that relation-less BEE is not auto-promoted.

## Task 5: Verification

- [x] Run focused tests.

```bash
python -m pytest tests/test_rs_jsonl_transform.py tests/test_rs_jsonl_contract_stats.py -q
```

- [x] Run smoke tests.

```bash
python -m pytest tests/test_mock_pipeline_smoke.py tests/test_quarantine_batch_summary.py -q
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
- `src/loaders/rs_jsonl_loader.py`
- `src/web/state.py`
- `mockdata/SCHEMA_RS_JSONL.md`
- `tests/test_rs_jsonl_contract_stats.py`

Implemented:
- `summarize_rs_jsonl_contract()`로 rs.jsonl relation-readiness 지표를 계산한다.
- `load_reviews_from_rs_jsonl_with_report()`를 추가해 기존 loader API는 유지하면서 관측 지표를 함께 받을 수 있게 했다.
- `DemoState.input_contract_stats`와 `batch_result["input_contract_stats"]`에 rs_jsonl 입력 계약 지표를 싣는다.
- schema 문서에 `relation_pending`/`relation_ready` 상태와 relation-less BEE 비승격 원칙을 명시했다.
- `src/web/state.py`에서 기존 미사용 import 2개를 제거했다.

Focused tests:
- `python -m pytest tests/test_rs_jsonl_transform.py tests/test_rs_jsonl_contract_stats.py -q`
- Result: `9 passed`

Smoke tests:
- `python -m pytest tests/test_rs_jsonl_transform.py tests/test_rs_jsonl_contract_stats.py tests/test_mock_pipeline_smoke.py tests/test_quarantine_batch_summary.py -q`
- Result: `12 passed`

Full tests:
- `python -m pytest tests/ -q`
- Result: `314 passed`

Static check:
- `python -m ruff check src/loaders/rs_jsonl_loader.py src/web/state.py tests/test_rs_jsonl_contract_stats.py`
- Result: `All checks passed!`
- `python -m ruff check src --statistics`
- Result: `72 errors` remain globally (`38 F401`, `29 E402`, `4 E741`, `1 F541`)

Remaining issues:
- 최종 target-linked BEE 판단은 여전히 relation attribution path에서만 수행한다.
- relation model 산출물이 들어온 뒤 실제 relation-ready fixture를 운영 샘플로 보강해야 한다.
- `wrapped_signal` promotion metadata 관통은 P1-3 범위다.

Next priority:
- P1-3 Promotion metadata 관통

## Rollback / safety

If stats calculation causes unexpected overhead:
- keep `load_reviews_from_rs_jsonl()` unchanged.
- use `load_reviews_from_rs_jsonl_with_report()` only where observability is needed.
