# Recommendation Master And Graph Evidence Usage Repair

Date: 2026-06-22

## Background

The first dense golden recommendation audit showed that recommendation quality
could still collapse into simple product-master/category matching or, worse,
generic graph-axis matching. The specific issues were:

- serving recommendation paths saw less product truth than `product_master`
  because `serving_product_profile` intentionally omits `product_name` and ES
  category hierarchy fields.
- `concept:Category:perfume` from personal-agent profiles did not join to
  product master fragrance products, so `user_fragrance_60f` had zero
  candidates.
- generic BEE axes such as `bee_attr_formulation` could qualify products as
  review-graph matches even when no compatible value was present.
- after blocking that unsafe exact match, review graph usage became too sparse
  because goal intent such as `보습` and `지속력` did not participate in semantic
  review-evidence matching.

## Decision

Use product-master truth and review-graph evidence as peers, but only when each
signal is semantically specific enough:

- Local recommendation/audit/UI paths overlay `product_master` truth onto the
  serving product profile through `src/rec/product_profile_enrichment.py`.
  This keeps the persisted DB mart contract unchanged while giving the local
  tester the same product name/category truth that a DB consumer gets by
  joining `serving_product_profile` to `product_master`.
- Recommendation category group aliases are allowed only for group-level user
  category intent such as `perfume`, `fragrance`, `hair`, `bodycare`,
  `skincare`, and `makeup`. Detailed categories such as `cat_cushion` still
  require exact matching and must not drift to all makeup products.
- Generic BEE axes (`bee_attr_formulation`, `bee_attr_texture_feel`) are blocked
  from exact BEE-attribute candidate matching. They can contribute only through
  value-and-polarity semantic rules.
- Semantic compatibility now reads `goal_ids` in addition to preferred keywords
  and BEE attrs. This lets goals such as `보습` and `지속력` match review graph
  evidence such as `kw_moist`, `kw_moisturizing`,
  `bee_attr_moisturizing_power`, and `bee_attr_lasting_power`.
- Source review stats remain scoring/trust features only; they still do not
  make an otherwise unrelated product eligible.

## Measured Dense Golden Audit

After the repair, dense golden `kg_on` audit has:

- category counts: skincare 11, makeup 6, bodycare 5, haircare 5, fragrance 5.
- `user_fragrance_60f`: 5 fragrance candidates, top products qualified by
  product-master category group truth.
- `user_dry_30f`: top-k contains both product-master truth and review graph
  relation evidence through moisture and lasting semantic matches.
- `user_sensitive_40f`: top-k contains both product-master truth and review
  graph relation evidence through moisture semantic matches.
- `user_makeup_matte_50m`: graph evidence appears for lasting when present;
  matte is not faked when the fixture has no matte review evidence.

## Tradeoffs

- The local in-memory UI receives richer product fields than the persisted mart
  row, but the persisted DB contract remains stable.
- Group-level category aliases improve meaningful test coverage, but detailed
  category ids are kept exact to avoid broad category drift.
- Some profiles still rely mostly on product-master truth when the current 906
  review fixture lacks matching review graph values. That is an honest data
  limitation, not a reason to lower promotion thresholds or fabricate graph
  evidence.
