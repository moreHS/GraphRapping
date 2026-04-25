# Global Lint Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 남은 ruff 42건을 기능 변경 없이 정리해 repo-wide lint baseline을 0으로 만든다.

**Architecture:** unused import, import order, ambiguous variable name, placeholder 없는 f-string만 수정한다. 동작 로직은 바꾸지 않고, cleanup 후 전체 테스트와 전체 ruff를 실행한다.

**Tech Stack:** Python 3.11, ruff, pytest.

---

## 현재 ruff 잔여

Baseline:

```bash
python -m ruff check src --statistics
```

Result before cleanup:

```text
28 F401 unused-import
9 E402 module-import-not-at-top-of-file
4 E741 ambiguous-variable-name
1 F541 f-string-missing-placeholders
```

## 변경 범위

- Remove unused imports across `src/`
- Move `logger = logging.getLogger(__name__)` below imports in KG modules
- Rename local list-comprehension variable `l` to `link`
- Remove one unnecessary f-string prefix

## Tasks

- [x] Remove all F401 unused imports reported by ruff.
- [x] Fix E402 in `src/kg/adapter.py`, `src/kg/canonicalizer.py`, `src/kg/mention_extractor.py`.
- [x] Fix E741 in `src/mart/build_serving_views.py`.
- [x] Fix F541 in `src/jobs/run_full_load.py`.
- [x] Run `python -m ruff check src`.
- [x] Run `python -m pytest tests/ -q`.

## Completion Record

Date: 2026-04-25

Changed files:
- `src/canonical/canonical_fact_builder.py`
- `src/db/repos/provenance_repo.py`
- `src/db/repos/quarantine_repo.py`
- `src/ingest/user_ingest.py`
- `src/jobs/run_full_load.py`
- `src/kg/adapter.py`
- `src/kg/canonicalizer.py`
- `src/kg/config.py`
- `src/kg/mention_extractor.py`
- `src/kg/models.py`
- `src/link/bee_attribution.py`
- `src/link/placeholder_resolver.py`
- `src/mart/aggregate_product_signals.py`
- `src/mart/build_serving_views.py`
- `src/normalize/bee_normalizer.py`
- `src/normalize/ner_normalizer.py`
- `src/normalize/relation_canonicalizer.py`
- `src/qa/dictionary_growth.py`
- `src/qa/evidence_sampler.py`
- `src/qa/quarantine_handler.py`
- `src/rec/hook_generator.py`
- `src/user/canonicalize_user_facts.py`
- `src/web/server.py`
- `src/wrap/relation_projection.py`

Verification:
- `python -m ruff check src` -> `All checks passed!`
- `python -m pytest tests/ -q` -> `324 passed`

Remaining issues:
- No ruff issues remain under `src/`.
