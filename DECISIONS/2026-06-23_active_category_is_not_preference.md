# Active Category Is Not Category Preference

## Background

Recommendation tester showed `PREFERS_CATEGORY` evidence for users whose
personal-agent source only had `purchase_analysis.active_product_category`.
That source field describes categories where the user has purchase/activity
history. It does not prove that the user explicitly prefers that category.

The mistaken projection made many recommendation rows look identical:

- active category became `PREFERS_CATEGORY`
- product category overlap became `PRODUCT_MASTER_TRUTH`
- candidates could qualify through category alone
- UI explanation displayed the signal as category preference

## Decision

Project `purchase_analysis.active_product_category` as `ACTIVE_IN_CATEGORY`,
persist it in `serving_user_profile.active_category_ids`, and keep it separate
from `preferred_category_ids`.

`ACTIVE_IN_CATEGORY` may contribute weak `active_category_affinity` under the
profile-fit layer when a candidate already has independent evidence, but it is
not first-class eligibility evidence and must not be displayed as
`PREFERS_CATEGORY`.

## Options Considered

1. Keep mapping to `PREFERS_CATEGORY`
   - Rejected. It fabricates an explicit preference from behavior context and
     overstates category matches.

2. Drop active category completely
   - Rejected. Activity category is still useful as weak context, especially
     for tie-breaking within a selected category tab.

3. Split to `ACTIVE_IN_CATEGORY`
   - Selected. It preserves source meaning, keeps future explicit
     `PREFERS_CATEGORY` available, and prevents active-category-only
     recommendations from passing evidence gates.

## Trade-offs

Recommendations may show fewer category-only matches. This is intentional:
absence of independent brand, ingredient, goal, review-graph, or purchase
behavior evidence should remain visible instead of being hidden by broad
category overlap.

The recommendation tester now needs to show two score concepts:

- `final_score`: recommendation fit score before diversity reranking.
- `rank_score`: sorting score after diversity bonus/penalty.
