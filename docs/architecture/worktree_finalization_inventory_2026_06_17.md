# Worktree Finalization Inventory - 2026-06-17

## Verdict

Code/data verification for the current final-output direction is complete. The
Git worktree is still dirty because the accepted final baseline has not been
staged/committed yet, not because generated cache residue remains.

Measured on 2026-06-17:

| Check | Count |
|---|---:|
| `git status --short` lines | 204 |
| tracked changed/deleted files | 143 |
| tracked deleted files | 56 |
| untracked files | 75 |
| generated cache dirs found | 0 |

The dirty worktree is now mostly a mix of final source-grounded implementation,
large refreshed fixtures, local source snapshots, active decision records, and
intentional deletion of old planning/future documents. It should not be cleaned
by blind revert.

## Already Cleaned

Generated Python/test/lint cache residue has been removed:

- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`
- `__pycache__/`

`mockdata/_remapped_reviews.json` is not present. The current web endpoint loads
review files as-is and does not create remapped review files.

## Keep As Final Baseline

These files/directories are part of the current final output or its
reproducibility trail and should be preserved unless a new decision replaces
the current contract.

### Source-grounded fixtures

- `mockdata/review_triples_raw.json`
  - refreshed to 906 review rows.
  - large diff is expected because the previous tracked fixture was much
    smaller and mock-era.
- `mockdata/shared_entities.json`
  - 38 brands, 517 products, 50 users.
- `mockdata/product_catalog_es.json`
- `mockdata/README.md`
- `mockdata/SCHEMA_RS_JSONL.md`
- `scripts/synthesize_mock_from_v260605.py`

### Product master / source identity / review stats

- `data/source_snapshots/*`
- `src/loaders/product_loader.py`
- `src/loaders/product_truth_merge.py`
- `src/loaders/source_review_stats_loader.py`
- `src/db/repos/product_repo.py`
- `src/jobs/run_full_load.py`
- `tests/test_source_product_id_contract.py`
- `tests/test_product_truth_merge.py`
- `tests/test_source_review_stats_loader.py`
- `tests/test_product_review_stats_repo.py`
- `DECISIONS/2026-06-17_product_source_identity_amoresim_integration.md`

### Review summary sidecar

- `sql/ddl_mart.sql`
- `sql/consumer_contract_queries.sql`
- `src/loaders/review_summary_sidecar_loader.py`
- `src/db/repos/review_summary_repo.py`
- `src/jobs/load_review_summary_sidecar.py`
- `scripts/load_review_summary_sidecar.py`
- `tests/test_review_summary_sidecar_loader.py`
- `tests/test_review_summary_repo.py`
- `DECISIONS/2026-06-17_review_summary_sidecar_final_output.md`

### Consumer contract / architecture records

- `docs/architecture/db_consumer_contract.md`
- `docs/architecture/product_master_review_graph_linkage_2026_06_16.md`
- `docs/architecture/final_artifact_cleanup_2026_06_17.md`
- `docs/architecture/product_master_real_snapshot_2026_06_16.md`
- `docs/architecture/v260605_906_fixture_lineage.md`
- `docs/architecture/amoresim_handoff_2026_06_16.md`
- `docs/architecture/graphrapping_snapshot_2026_06_16.json`
- `DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`

### Required workflow records

- `DECISIONS/`
- `ERR_HIST/`

The repository instructions require these records for technical decisions and
repeated error history.

## Removed From Active Baseline

These were removed from the active repository surface because they are old
planning/history artifacts or unrelated future briefs, not current 906-review
runtime/data contracts.

| Path | Current read |
|---|---|
| `PLAN/` | Superseded planning worklogs. |
| `FUTURE/` | Future action/intent briefs, unrelated to final GraphRapping runtime. |
| `PROJECT_OVERVIEW_KO.md` | Superseded by active README/ARCHITECTURE/architecture docs. |
| old `DECISIONS/2026-03-*` through wave `2026-06-10_*` records | Superseded by final 2026-06-15/17 decisions. |
| `docs/superpowers/plans/` | Implementation worklogs superseded by active decision/architecture docs. |
| `scripts/sync_product_catalog.py` | Legacy mock catalog generator that could overwrite the source-grounded 517-product catalog. |
| `HANDOFF.md` | Stale handoff; workflow says delete after completion. |

`AGENTS.md` is kept and should be tracked as the repository-local workflow
contract. `data/source_snapshots/*` is kept as reproducibility evidence.

## Verification Already Run

Current verified baseline:

- `python -m pytest -q`: 666 passed, 36 skipped.
- changed-file `ruff check`: passed for the files touched by the final
  review-summary/source-grounded cleanup.
- `git diff --check`: passed.
- local DB review-summary sidecar manifest and row counts were rechecked after
  tests.

Known remaining verification caveat:

- full `ruff check .` still reports pre-existing lint in untouched historical
  tests. That is not resolved by the final-output cleanup.

## Finalization Path

1. Keep the current source-grounded and review-summary implementation as the
   final baseline.
2. Keep `data/source_snapshots/*` in this repo for the current finalization.
3. Keep `AGENTS.md` as the repo-local workflow contract.
4. Keep `HANDOFF.md` deleted.
5. Stage the accepted final baseline after verification.
