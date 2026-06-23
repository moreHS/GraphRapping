# Catalog Keyword And Repurchase Category Matching

## Background

Dense recommendation checks showed a measured gap:

- user profile had scoped makeup keywords such as `틴트`, `파우더`, `매트`
- user profile also had `REPURCHASES_CATEGORY` values such as `틴트`
- product master names/categories contained values such as `립 틴트`, `쿠션`
- promoted review keywords for those products often contained only broad values
  such as `kw_moist`, or no keyword at all

The recommendation layer therefore collapsed to the same broad semantic BEE
match across many products.

## Decision

Add two explicit overlap types:

- `catalog_keyword`: user `PREFERS_KEYWORD` matched against product-master
  product/category text.
- `repurchase_category`: user `REPURCHASES_CATEGORY` matched against the same
  product-master product/category text.

`catalog_keyword` is product-master truth, not review-graph evidence.
`repurchase_category` is purchase behavior evidence.

## Boundaries

This is not a free-form ES query. The match only fires when the normalized user
concept value is directly present in product-master name/category text already
loaded into the serving profile.

Review graph keyword/BEE matching remains separate and should still dominate
when promoted review signals are available.

