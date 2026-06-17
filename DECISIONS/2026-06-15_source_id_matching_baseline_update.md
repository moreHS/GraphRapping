# Source ID Matching Baseline Update

## Background

The source-grounded product contract added exact `source_product_id` matching
before brand/product-name matching. This intentionally changes target product
distribution for mock v260605 reviews whose names previously matched through
heuristics.

## Observed Change

`tests/test_corpus_promotion_baseline.py` moved:

- `top_bee_attr_ids` product count: `27 -> 26`
- `signal_count`: unchanged (`2801` for kg off, `2529` for kg on)
- `quarantine_count`: unchanged (`9255` for kg off, `6331` for kg on)

## Root Cause

Disabling exact source-id matching restores the old count of `27`, proving the
drift is caused by source-id matching rather than extraction, KG mode, or serving
profile filtering.

The old path concentrated reviews on name/brand matched products such as
`60069` and `104396`. The new path routes reviews to their explicit source
product ids, including `36725`, and diffuses enough support that the number of
products crossing the promotion threshold becomes `26`.

## Decision

Keep exact source-id matching. Update the mock corpus baseline to `26`.

This is aligned with the 2026-06-15 product contract: source product identity
must beat fuzzy/name matching, even when observable mock promotion distribution
changes.
