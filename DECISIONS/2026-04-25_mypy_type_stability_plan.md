# Mypy Type Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m mypy src` 기준을 실제로 실행 가능하게 만들고, 타입 안정성 baseline을 확보한다.

**Architecture:** 먼저 dev dependency 설치가 가능한 패키징 설정을 복구한다. 그 다음 mypy를 실행해 오류 규모를 확인하고, 기능 변경 없이 명백한 타입 오류부터 정리한다.

**Tech Stack:** Python 3.11, hatchling, mypy, pytest, ruff.

---

## Baseline Discovery

Initial command:

```bash
python -m mypy src
```

Result:

```text
/Users/amore/anaconda3/bin/python: No module named mypy
```

Attempted dev install:

```bash
python -m pip install -e '.[dev]'
```

Result:

```text
ValueError: Unable to determine which files to ship inside the wheel...
The most likely cause of this is that there is no directory that matches the name of your project (graphrapping).
```

Root cause:
- Project name is `graphrapping`, but the import package is currently the top-level `src` package.
- `hatchling` needs explicit wheel package selection.

## 변경 범위

- Modify: `pyproject.toml`
- Potentially modify: files reported by `python -m mypy src`
- Update: `README.md` if the verified dev install/type-check command changes
- Update: `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

## Tasks

- [x] Add explicit hatchling wheel target so editable dev install works.
- [x] Run `python -m pip install -e '.[dev]'`.
- [x] Run `python -m mypy src` and record the baseline.
- [x] Fix safe, local type errors without behavior changes.
- [x] Run `python -m mypy src`.
- [x] Run `python -m ruff check src`.
- [x] Run `python -m pytest tests/ -q`.
- [x] Record completion status and remaining type debt.

## Non-goals

- Do not rename imports from `src.*` to `graphrapping.*` in this pass.
- Do not weaken mypy globally just to make the command green.
- Do not refactor runtime logic unless mypy exposes a real correctness issue.

## Completion Record

Date: 2026-04-25

Changed files:
- `pyproject.toml`
- `README.md`
- `src/common/config_loader.py`
- `src/common/concept_resolver.py`
- `src/db/repos/provenance_repo.py`
- `src/db/repos/review_repo.py`
- `src/db/unit_of_work.py`
- `src/jobs/run_daily_pipeline.py`
- `src/jobs/run_incremental_pipeline.py`
- `src/kg/adapter.py`
- `src/kg/mention_extractor.py`
- `src/kg/same_entity_merger.py`
- `src/loaders/product_loader.py`
- `src/loaders/rs_jsonl_loader.py`
- `src/mart/aggregate_product_signals.py`
- `src/mart/aggregate_user_preferences.py`
- `src/mart/build_serving_views.py`
- `src/normalize/ner_normalizer.py`
- `src/rec/scorer.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/web/server.py`
- `src/web/state.py`
- `DECISIONS/2026-04-25_mypy_type_stability_plan.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

Implementation notes:
- Added `[tool.hatch.build.targets.wheel] packages = ["src"]` so `pip install -e '.[dev]'` works with the current `src.*` import package.
- Added `types-PyYAML` to dev dependencies.
- Added a narrow mypy override for untyped `asyncpg`.
- Fixed concrete Optional/Any boundaries without changing pipeline behavior.

Verification:
- `python -m pip install -e '.[dev]'` -> success
- `python -m mypy src --show-error-codes` -> `Success: no issues found in 86 source files`
- `python -m ruff check src` -> `All checks passed!`
- `python -m pytest tests/ -q` -> `324 passed, 3 skipped`
- `bash scripts/run_postgres_integration.sh` -> `3 passed`
- `docker ps -a --filter name=graphrapping-postgres-it` -> no remaining containers

Remaining issues:
- `pip install -e '.[dev]'` reported unrelated existing conda environment conflicts for Spyder/PyQt and `python-lsp-black`/`black`; GraphRapping installation itself succeeded.
- The project still imports through the top-level `src` package. Renaming to a project package such as `graphrapping.*` is intentionally out of scope.
