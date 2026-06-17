# Incremental Cleanup Fake UOW Fixture

## Error

`tests/test_incremental_cleanup_wiring.py::test_helper_rebuilds_affected_serving_profiles`
failed twice after adding product review stats lookup to incremental serving
rebuild.

1. The fake UOW did not handle `FROM product_review_stats`.
2. The first fixture patch accidentally nested `fetchrow()` inside `fetch()`,
   leaving `_FakeUow` without a real `fetchrow` method.

## Cause

The production code now calls `product_repo.load_product_review_stats()` during
serving profile rebuild. The existing test double only modeled aggregate/user
queries. The follow-up patch was applied at the wrong indentation level.

## Fix

Define `_FakeUow.fetchrow()` as a class method at the same indentation level as
`fetch()`, and handle both `product_review_stats` and `user_master` queries.

## Prevention

When updating nested test fixtures, inspect the edited region before rerunning.
For async fakes, verify the object actually exposes every method called by the
new production path.
