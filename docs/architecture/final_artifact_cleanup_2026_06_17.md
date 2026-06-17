# Final Artifact Cleanup - 2026-06-17

## Scope

This cleanup was done after the product master, source review stats, graph
output, and review-summary sidecar were refreshed into the local `graphrapping`
Postgres database.

The goal was to remove generated residue without deleting source snapshots,
mock fixtures, or decision records that are still needed for reproducibility.

## Removed

Generated cache artifacts only:

- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`
- all `__pycache__/` directories under `src/`, `tests/`, and `scripts/`
- generated `*.pyc` files inside those cache directories

Verification after deletion:

- no `__pycache__`
- no `.pytest_cache`
- no `.ruff_cache`
- no `.mypy_cache`

## Kept Intentionally

These are not safe to delete as "residue" without a separate review:

- `data/`
  - contains source snapshots / local evidence used to reproduce the current
    product master and review corpus work.
- `mockdata/`
  - still acts as regression fixtures and compat examples.
- `DECISIONS/`
  - records historical implementation decisions and avoids repeating old
    debates.
- `ERR_HIST/`
  - required by repository workflow for repeated error tracking.
- `docs/architecture/*snapshot*` and lineage docs
  - current and historical measurement records.

## Current Final Output Surface

As of this cleanup, the local DB final output includes:

- `product_master`
- `product_review_stats`
- `review_raw`
- `review_catalog_link`
- graph layers: `canonical_fact`, `wrapped_signal`, `agg_product_signal`
- `serving_product_profile`
- `review_summary_sidecar`
- `review_summary_manifest`

Review summary is included as a serving sidecar, not as graph evidence.
