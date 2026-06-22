# KG-On Source-Backed Keyword Repair

Date: 2026-06-22

## Background

The final 906-review fixture contains source-backed BEE text that can be
normalized into dictionary keywords. In `kg_mode=off`, the legacy BEE path
emitted both product attribute signals and keyword helper facts, so serving
products exposed `top_keyword_ids` on 5 products.

In `kg_mode=on`, the KG pipeline owned product attribution and BEE attribute
fact creation, but it did not reuse the existing BEE dictionary surface map to
create `BEEAttr -> HAS_KEYWORD -> Keyword` helper facts. As a result, KG-on
kept BEE attribute evidence but dropped source-backed keyword serving coverage.

Measured before repair:

| Fixture | Mode | Signals | Serving keyword coverage |
|---|---:|---:|---:|
| Wide 906 / 517 products | `kg_on` | 2,529 | 0 products |
| Dense golden / 32 products | `kg_on` | 2,529 | 0 products |

The adapter-level guard that prevents synthetic `AUTO_KEYWORD` promotion was
correct, but it was insufficient because the orchestration layer never created
source-backed dictionary keyword facts in KG-on mode.

## Decision

In `kg_mode=on`, keep KG as the owner of product attribution and
`Product -> BEEAttr` facts. Add only source-backed helper facts:

```text
BEEAttr -> HAS_KEYWORD -> Keyword
```

These helper facts are derived from target-linked BEE rows through the existing
`BEENormalizer` dictionary map. They use:

- `evidence_kind = "BEE_DICT"`
- BEE normalizer confidence
- confidence floor `>= 0.6`
- target-linked BEE rows only

The helper intentionally does not emit duplicate `Product -> BEEAttr` facts.
Synthetic or unknown KG keyword candidates remain quarantined as
`AUTO_KEYWORD`/unknown candidate output and do not become promoted serving
keyword evidence.

## Measured Result

After repair:

| Fixture | Mode | Signals | Serving keyword coverage |
|---|---:|---:|---:|
| Wide 906 / 517 products | `kg_on` | 2,767 | 5 products |
| Dense golden / 32 products | `kg_on` | 2,767 | 18 products / 22 items |

Wide promoted serving coverage now matches the legacy keyword coverage while
preserving the KG-on quarantine profile:

- `quarantine_count = 6,331`
- `top_bee_attr_ids` remains on 26 products
- `top_keyword_ids` increases from 0 to 5 products

Dense golden promoted evidence becomes materially more useful for
recommendation QA because keyword-level review evidence is available in KG-on
without lowering promotion thresholds.

## Tradeoffs

- This is a compatibility bridge around a KG orchestration gap, not a new
  independent extraction model.
- The graph still does not promote synthetic KG keyword guesses.
- Baseline signal count changes are expected and must stay pinned by
  `tests/test_corpus_promotion_baseline.py`.
