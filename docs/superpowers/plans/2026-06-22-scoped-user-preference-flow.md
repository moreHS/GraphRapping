# Scoped User Preference Flow Implementation Plan

Date: 2026-06-22

## Goal

Repair the recommendation user-profile flow so category-specific user
preferences are not flattened into global matches. Keep product master truth and
review graph relation evidence as peer evidence families.

## Confirmed Baseline

- Dense golden fixture should be the manual recommendation baseline:
  906 reviews, 32 products, 6 users.
- Current 8000 issue was caused by the demo run defaulting to the wide fixture
  and product/user paths fixed to `mockdata/`. That was already corrected by
  fixture-aware `/api/pipeline/run`.
- Current user preference issue is independent: scoped personal-agent data is
  still flattened before recommendation.

## Implementation Tasks

- [x] Document the scoped flow and matching rule.
- [x] Add `scope_group` and `source_section` metadata in
  `personal_agent_adapter`.
- [x] Preserve scope metadata in `canonical_user_fact.provenance` and top-level
  canonical fields.
- [x] Aggregate user preferences by `(predicate, dst_id, scope_group)` and store
  scope/source sections in `source_mix`.
- [x] Add `agg_user_preference.scope_group` so DB persistence cannot collapse
  same-id preferences across product groups.
- [x] Add `scoped_preference_ids` to serving user profiles, DDL, repo upsert,
  schema constants, and frontend/debug graph output.
- [x] Update candidate generation to prefer scoped entries when present and
  apply brand/keyword/BEE/concern/goal/context/ingredient matches only when the
  preference scope matches the candidate product group.
- [x] Update semantic compatibility to use the same scoped user entries.
- [x] Add regression tests for adapter scope, aggregate persistence, serving
  contract, scoped candidate matching, and scoped semantic matching.
- [x] Run focused tests, full pytest, `git diff --check`, and reload 8000 with
  dense golden for manual verification.

## Implementation Result

Implemented on 2026-06-22.

- Dense golden API reload: 906 reviews, 32 products, 6 users, 2,767 signals.
- Focused scope tests: 56 passed.
- Full suite: 771 passed, 36 skipped.
- `git diff --check`: no output.
- 8000 is running with `GRAPHRAPPING_DEMO_FIXTURE=dense_golden`.
- `user_makeup_matte_50m` now has makeup-scoped `매트`, `파우더`, `틴트`,
  and skincare recommendation output no longer receives those keyword/matte
  overlaps.

## Out Of Scope

- No fake graph evidence.
- No promotion-threshold changes.
- No AmoreSimulation changes.
- No change to source review stats semantics.
- No attempt to force fragrance/scalp graph evidence when the fixture lacks it.

## Validation Commands

```bash
python -m pytest tests/test_user_adapter_semantics.py \
  tests/test_user_preference_weighting.py \
  tests/test_serving_profile_columns_align.py \
  tests/test_recommendation_semantic_compatibility.py -q

python -m pytest -q

git diff --check

GRAPHRAPPING_ENABLE_PIPELINE_RUN=1 \
GRAPHRAPPING_DEMO_FIXTURE=dense_golden \
python -m uvicorn src.web.server:app --host 127.0.0.1 --port 8000
```

## Review Checklist

- Does each scoped source section keep its scope through serving output?
- Does a scoped preference stop scoring outside its product group?
- Do global hard filters still apply across product groups?
- Do flat legacy arrays remain compatible?
- Does the frontend display the dense golden baseline, not stale 517/50 data?
