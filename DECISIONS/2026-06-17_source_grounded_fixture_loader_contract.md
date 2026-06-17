# Source-Grounded Fixture Loader Contract

## Background

The checked-in `mockdata/product_catalog_es.json` is no longer the old
hand-curated selling-only mock catalog. It is a 2026-06-16 source-grounded
compat catalog with 517 source products:

- `SOURCE_GROUNDED`: 516
- `SOURCE_KEY_COLLISION`: 1

The local final `graphrapping` DB and downstream AmoreSimulation snapshot use
all 517 products. However, the product loader still defaulted to
`sale_status_filter="판매중"`, which loaded only 273 rows from the refreshed
catalog because many source products have `SALE_STATUS=NULL`, `전시종료`, or other
non-selling states.

That default caused pipeline baseline tests to process reviews against an
incomplete product universe, leading to product-match quarantine drift and lower
signal counts.

## Decision

Change the default product loader/full-load behavior to keep all source products:

- `load_products_from_es_records(..., sale_status_filter=None)`
- `load_products_from_json(..., sale_status_filter=None)`
- `FullLoadConfig.sale_status_filter = None`

The selling-only filter remains available when callers explicitly pass:

```python
sale_status_filter="판매중"
```

## Consequences

- Full pipeline baseline uses the same 517 product universe as the local final
  DB.
- Review target resolution does not drop valid source products just because
  their current catalog sale status is not `판매중`.
- Selling-only experiments must opt into filtering explicitly.
- `shared_entities.json` now carries source-grounded brands from the refreshed
  catalog instead of the old empty-brand mock contract.
