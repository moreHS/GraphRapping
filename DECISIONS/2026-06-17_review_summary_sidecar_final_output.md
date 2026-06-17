# Review Summary Sidecar As Final Output

## Background

GraphRapping final product output must connect product master, source reviews,
graph relation output, source review stats, and ES review summaries by `prd_id`
/ source identity without losing source-quality fields.

Earlier review-summary handling lived in downstream/adjacent code and was not
materialized in GraphRapping's local final DB. That left the final GraphRapping
output incomplete: consumers could read product master, graph signals, and
source stats, but not the ES review-summary source text through the same
`product_id` contract.

## Options Considered

1. **Promote review summary text into graph facts/signals.**
   - Pro: one graph-only consumer surface.
   - Con: summary text is product-level derived text, not review evidence. It
     would pollute graph support counts and does not solve source rating/count
     needs.

2. **Keep review summaries only in downstream AmoreSimulation adapters.**
   - Pro: minimal GraphRapping change.
   - Con: GraphRapping final output remains incomplete and consumers repeat
     matching logic.

3. **Materialize a GraphRapping mart sidecar.**
   - Pro: final output includes review summaries; raw ES docs are preserved;
     graph evidence remains clean; consumers join by `product_id`.
   - Con: adds one mart table and one materialization job.

## Decision

Choose option 3.

Review summaries are part of GraphRapping's final consumer output, but not part
of graph promotion. They are stored in:

- `review_summary_sidecar`
- `review_summary_manifest`

The loader fetches ES aliases by alias-wide `match_all` scroll and performs the
source identity join locally. It must not send local GraphRapping product ids to
ES.

Clean join identity is:

```text
source_channel + source_key_type + source_product_id
```

For review summary category resolution:

| source_channel | category |
|---|---|
| `031` | `own-apmall` |
| `036` | `own-innisfree` |
| `039` | `own-osulloc` |
| `048` | `own-aritaum` |

`SOURCE_KEY_COLLISION` rows and `source_key_collision:<id>` markers are excluded
from clean review-summary matching.

## 2026-06-17 Local Result

Run:

- DB: local `graphrapping`
- Long alias: `summary-review-long`
- Short alias: `summary-review-short`
- Materialization label: `2026-06-17`

Counts:

| Metric | Count |
|---|---:|
| active products | 517 |
| clean lookup products | 516 |
| collision excluded | 1 |
| fetched long docs | 14,477 |
| fetched short docs | 3,695 |
| sidecar rows | 516 |
| matched | 495 |
| exact category | 495 |
| not found | 21 |
| ambiguous skipped | 0 |
| collision sidecar rows | 0 |
| rows with raw long ES hit | 495 |
| rows with raw short ES hit | 492 |

## Consequences

- Consumers should use `review_summary_sidecar.normalized_summary` for normal
  reads and raw `long_doc`/`short_doc` JSONB when they need source fields not in
  the projection.
- Graph support counts remain sourced only from review evidence/facts.
- Future ES refreshes can rerun the sidecar loader idempotently and inspect the
  latest `review_summary_manifest`.
