# Changelog

## 2026-06-17 — Final 906-Review Source-Grounded Baseline

GraphRapping's active local baseline is now the source-grounded 906-review
fixture connected to the refreshed product master, source review stats, graph
outputs, and review-summary sidecar.

### Data Baseline

- `mockdata/review_triples_raw.json`: 906 reviews.
- `mockdata/product_catalog_es.json`: 517 products.
- `mockdata/shared_entities.json`: 38 brands, 517 products, 50 users.
- Local DB: 517 active `product_master` rows, 906 `review_raw` rows, 906 exact
  `review_catalog_link` rows.
- `product_review_stats`: 516 rows.
- `review_summary_sidecar`: 516 clean rows, with `SOURCE_KEY_COLLISION` product
  excluded from clean summary matching.

### Final Output Contract

- Product/source identity is carried as `source_channel + source_key_type +
  source_product_id`.
- Review-derived graph evidence remains separate from product master truth.
- Review summaries are materialized as a mart sidecar, not promoted into graph
  evidence.
- Source review counts/ratings are exposed through `product_review_stats` and
  `serving_product_profile.source_review_*`.

### Cleanup

- Generated cache residue removed: `.pytest_cache`, `.ruff_cache`,
  `.mypy_cache`, and `__pycache__`.
- Active documentation was reduced to the final 906-review baseline surface.
- Old wave plans, future/brief docs, and pre-baseline decision logs were removed
  from the active repository surface.

## Earlier History

Earlier March-May wave plans and implementation notes were superseded by the
2026-06-17 final baseline docs. Current code behavior is guarded by tests and by
the active contract documents under `DECISIONS/` and `docs/architecture/`.
