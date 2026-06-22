# Personal-Agent Profile Usage In GraphRapping

## Purpose

This document records how GraphRapping uses real-user personalization data from
`/Users/amore/workplace/agent-aibc/persnal-agent` after the evidence-first
recommendation redesign.

## Verified Personal-Agent Sources

Measured files:

- `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/data_store.py`
- `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/signal_builder.py`
- `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/recommend/service.py`
- `/Users/amore/workplace/agent-aibc/persnal-agent/PURCHASE_SUMMARY_RECOMMEND_USAGE.md`
- `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/real_sample.py`

`real_sample.py` contains DB-derived raw samples with:

- profile basics: age, sex, skin type, skin concern, skin tone.
- brand affinity: overall, skincare, makeup, bodycare, hair, perfume brands.
- purchase activity: active/current-use product categories and product summary.
- repurchase activity: representative product names, purchase cycle, recent date,
  repurchase intensity, main effect.
- seasonal affinity: season-to-category/product summaries.
- optional chat profile.

## Current GraphRapping Mapping

Implemented in `src/user/adapters/personal_agent_adapter.py`:

- `basic.skin_type` -> `HAS_SKIN_TYPE`
- `basic.skin_tone` -> `HAS_SKIN_TONE`
- `basic.skin_concerns` -> `HAS_CONCERN`
- purchase brand domains -> `PREFERS_BRAND`
  - `preferred_brand`
  - `preferred_skincare_brand`
  - `preferred_makeup_brand`
  - `preferred_bodycare_brand`
  - `preferred_hair_brand`
  - `preferred_perfume_brand`
- `purchase_analysis.active_product_category` -> `PREFERS_CATEGORY`
- `purchase_analysis.preferred_repurchase_category` -> `REPURCHASES_CATEGORY`
- chat ingredients -> `PREFERS_INGREDIENT` / `AVOIDS_INGREDIENT`
- chat face/hair/body/scalp/makeup concerns -> `HAS_CONCERN`
- chat face/hair/body/scalp/makeup goals -> `WANTS_GOAL`
- chat texture preferences -> `PREFERS_BEE_ATTR` + `PREFERS_KEYWORD`
- chat scent preferences -> `PREFERS_KEYWORD`
- event-level purchase features -> `OWNS_PRODUCT`, `OWNS_FAMILY`,
  `REPURCHASES_FAMILY`, `REPURCHASES_BRAND`, `RECENTLY_PURCHASED`
- high-confidence exact purchase summary matches -> the same product/family/
  brand behavior facts above.

## Recommendation Evidence Policy

Implemented in `src/rec/recommendation_evidence_index.py` and
`src/rec/candidate_generator.py`:

- First-class product master truth:
  - brand
  - category
  - ingredient
  - main benefit / goal
- First-class review graph relation evidence:
  - keyword
  - BEE attribute
  - context
  - concern
  - concern bridge
  - tool
  - co-used product
  - comparison
- First-class purchase behavior:
  - owned family
  - repurchased family
  - repurchase brand
  - recent purchase brand
- Source review stats and review summaries are not eligibility evidence. They are
  trust, tie-break, and display signals after a candidate is already qualified.

2026-06-22 dense-golden update:

- Product-master truth is overlaid onto local serving recommendation profiles
  for UI/audit only. The persisted `serving_product_profile` DB contract remains
  unchanged; DB consumers can get the same truth by joining `product_master`.
- Group-level category aliases are allowed only for broad personal-agent intent
  such as `perfume`, `fragrance`, `hair`, `bodycare`, `skincare`, and `makeup`.
  Detailed category ids such as `cat_cushion` still require exact category
  matching.
- Generic BEE axes such as `bee_attr_formulation` and
  `bee_attr_texture_feel` do not qualify recommendation candidates by exact
  BEE-attribute match. Review graph evidence must be value-compatible.
- Goal intent participates in semantic review matching. For example, `보습`
  can match promoted review evidence `kw_moist`, `kw_moisturizing`, and
  `bee_attr_moisturizing_power`; `지속력` can match
  `bee_attr_lasting_power`.

## API/UI Contract

`/api/recommend` now returns `eligibility` per result:

- `eligible`
- `evidence_families`
- `master_truth_paths`
- `review_graph_paths`
- `purchase_paths`
- `rejection_reasons`

The frontend displays the evidence families so a tester can tell whether a
recommendation is product-master-driven, review-graph-driven, purchase-driven,
or mixed.

## Purchase Summary Product Resolution

GraphRapping resolves product names/codes inside:

- `purchase_analysis.use_expected_product_summary`
- `purchase_analysis.preferred_repurchase_product_summary`
- `purchase_analysis.seasonal_product_summary`

The resolver only accepts high-confidence exact matches against GraphRapping
product master:

1. `rprs_prd_cd` / `prd_cd` / product id / source product id exact match;
2. normalized `rprs_prd_nm` / `prd_nm` exact product name match;
3. normalized representative product name match;
4. optional ES metadata codes when present.

Unresolved summary products do not create product/family/brand facts. Fuzzy name
matching remains intentionally excluded from the recommendation path.

## Known Remaining Work

- Broader per-stage review-relation coverage audit from raw relation extraction
  to promoted serving fields.
- More fragrance/scalp review-value rules after the underlying fixture contains
  enough source-backed scent/scalp review evidence.
- Optional persisted schema for rich profile context, if runtime-only context
  becomes insufficient.
