# Scoped User Preference Projection

Date: 2026-06-22

## Background

Manual recommendation inspection showed that user preferences from
personal-agent were being flattened. A value like `매트` from makeup, `크림`
from skincare/body/hair, or a category-specific preferred brand could match any
product group once it reached `serving_user_profile`.

This undercuts the reason to use GraphRapping: product-master truth and
review-graph evidence can only be meaningful if user intent is projected with
its source context.

## Decision

Preserve `scope_group` from user profile adapter through canonical facts,
aggregate preferences, DB json metadata, serving profile, and recommendation
matching.

- Use `skincare`, `makeup`, `bodycare`, `haircare`, and `fragrance` as the
  product-group scope vocabulary.
- Keep generic/unknown preferences unscoped only when the source really is
  global.
- Add `agg_user_preference.scope_group` and include it in the aggregate
  conflict key. `source_mix` also carries the scope for audit readability, but
  it cannot be the only persistence path because the same destination id can
  appear in multiple scopes.
- Add `serving_user_profile.scoped_preference_ids` so DB consumers and the
  local UI share the same serving contract.
- When scoped entries are present, candidate generation and semantic
  compatibility must use them instead of treating legacy flat arrays as global.

## Tradeoffs

- This adds one jsonb serving column, but keeps old flat fields for backward
  compatibility.
- A sparse profile may produce fewer candidates because wrong-category matches
  are no longer counted. That is intentional.
- Basic profile fields remain global for now because the source does not always
  say whether they are face, scalp, or body specific.

## Reference

Implementation plan:
`docs/superpowers/plans/2026-06-22-scoped-user-preference-flow.md`

Design:
`docs/superpowers/specs/2026-06-22-scoped-user-preference-flow-design.md`
