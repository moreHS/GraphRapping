# Source-Grounded Fixture Legacy Contract Failure

## Error

During full pytest after review-summary sidecar finalization, repeated failures
appeared in mock/pipeline tests:

- `TypeError: normalize() argument 2 must be str, not None`
- pipeline baseline drift:
  - product count 273 instead of 517
  - kg-off signal count 1322 instead of 2801
  - kg-on signal count 1185 instead of 2529
- mock integrity failures expecting empty brands / null review stats while the
  checked-in catalog had source-grounded product truth.

## Root Cause

The checked-in product catalog had already been refreshed to the 2026-06-16
source-grounded 517-product universe, but some code/tests still encoded the old
mock-era contract:

- product loader defaulted to `sale_status_filter="판매중"`, so only 273 source
  products were loaded from the refreshed catalog;
- reviews whose `source_product_id` was excluded by that filter fell through to
  name matching with `brnd_nm=None`;
- `product_matcher` assumed fallback brand/product inputs were strings;
- mock guard tests still expected checked-in catalog brand/review stats to be
  missing.

## Fix

- Made product matcher robust to `None` fallback inputs.
- Changed default product loading/full-load behavior to keep all source products
  unless a caller explicitly passes `sale_status_filter="판매중"`.
- Updated `shared_entities.json` to include 38 source-grounded catalog brands.
- Updated stale mock guard tests and README text to distinguish:
  - synthetic mock generation must not invent missing brands/stats;
  - the current checked-in catalog is source-grounded and intentionally contains
    brand/review stats.

## Prevention

- Do not treat current source-grounded fixture as the old "missing brand"
  synthetic mock.
- When refreshing catalog fixtures, update shared cross-fixture anchors and
  guard tests in the same change.
- Keep selling-only experiments explicit; default pipeline loads should preserve
  the full source product universe used by final DB materialization.
