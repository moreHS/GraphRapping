# Final 906-Review Baseline Cleanup

## Background

GraphRapping now uses the source-grounded 906-review fixture as the local final
test/data baseline. The active final output must connect these sources without
losing source-quality information:

- product master / source identity
- raw review rows
- graph relation output and promoted signals
- source review count/rating stats
- ES review-summary sidecar

The repository still had many old planning documents and mock-era references
from earlier 15-review, 47-product, and wave-by-wave development phases. Those
records made the current baseline hard to identify and increased the risk of
using stale assumptions.

## Final Data Baseline

The active local baseline is:

| Surface | Baseline |
|---|---:|
| `mockdata/review_triples_raw.json` | 906 reviews |
| `mockdata/product_catalog_es.json` | 517 products |
| `mockdata/shared_entities.json` brands | 38 brands |
| `mockdata/shared_entities.json` products | 517 products |
| `mockdata/shared_entities.json` users | 50 users |
| `product_master` local DB | 517 active products |
| `review_raw` local DB | 906 reviews |
| `review_catalog_link` local DB | 906 exact source-product links |
| `product_review_stats` local DB | 516 rows |
| `review_summary_sidecar` local DB | 516 clean rows |

Known source identity caveat:

- One product is marked `SOURCE_KEY_COLLISION`.
- Clean source joins must use `source_channel + source_key_type +
  source_product_id`, not `source_product_id` alone.
- Review summaries exclude collision rows from clean matching.

## Active Documentation Surface

Keep active docs focused on the final baseline:

- `README.md`
- `ARCHITECTURE.md`
- `CHANGELOG.md`
- `mockdata/README.md`
- `docs/architecture/db_consumer_contract.md`
- `docs/architecture/product_master_review_graph_linkage_2026_06_16.md`
- `docs/architecture/product_master_real_snapshot_2026_06_16.md`
- `docs/architecture/v260605_906_fixture_lineage.md`
- `docs/architecture/amoresim_handoff_2026_06_16.md`
- `docs/architecture/final_artifact_cleanup_2026_06_17.md`
- `docs/architecture/worktree_finalization_inventory_2026_06_17.md`

Keep only decision records that directly define the current final baseline or
current cleanup:

- `DECISIONS/2026-06-15_source_grounded_product_contract_plan.md`
- `DECISIONS/2026-06-15_source_id_matching_baseline_update.md`
- `DECISIONS/2026-06-17_product_source_identity_amoresim_integration.md`
- `DECISIONS/2026-06-17_review_summary_sidecar_final_output.md`
- `DECISIONS/2026-06-17_source_grounded_fixture_loader_contract.md`
- `DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`

## Removed From Active Surface

Remove old/unrelated documents from the active repository surface:

- `PLAN/`
- `FUTURE/`
- `PROJECT_OVERVIEW_KO.md`
- March/April/May decision records
- June wave implementation plans/reports that are superseded by the final
  product/source/review-summary contract

These were planning/history artifacts, not runtime contracts. The current final
contract is captured in the active docs listed above.

Remove or guard code paths that can recreate old data:

- Delete `scripts/sync_product_catalog.py`; it generated template/mock catalog
  rows and could overwrite the source-grounded 517-product catalog.
- Keep `scripts/synthesize_mock_from_v260605.py` for review lineage, but default
  it to write reviews only. Writing the derived non-source-grounded catalog
  requires an explicit opt-in flag.
- Load external demo review files as-is in `src/web/server.py`; do not rewrite
  `prod_nm`/`brnd_nm` or append the 906 fixture to external data.
- Require review-summary category exact matching. Do not attach summary docs by
  `source_product_id` alone.

## Kept Intentionally

These are not cleanup residue:

- `mockdata/review_triples_raw.json`
- `mockdata/product_catalog_es.json`
- `mockdata/shared_entities.json`
- `mockdata/review_kg_output.json`
- `mockdata/review_rs_samples.json`
- `mockdata/user_profiles_raw.json`
- `mockdata/user_profiles_normalized.json`
- `data/source_snapshots/*`
- `ERR_HIST/*`

`review_rs_samples.json` and `user_profiles_raw.json` remain small reference
fixtures covered by loader/user tests. `data/source_snapshots/*` remains the
local evidence for the 2026-06-16 product master/source identity refresh.

## Consequences

- New tests and docs should refer to this final decision or to the lineage/doc
  contract files, not to old wave plans.
- Baseline changes must update the active docs and add a new decision record.
- The final output includes review summaries as a sidecar, not as graph
  evidence.
