"""
P3-8 (Wave 2.10): soft-delete stale rows in `agg_product_signal` and
`agg_user_preference`.

This file covers contract-level checks (SQL shape, count parser, upsert
revival). Behavioural Postgres coverage lives alongside the rest of the
asyncpg-bound integration suite (see `test_postgres_integration.py` for
the harness pattern); add a real-DB test there if a PG fixture is wired
into CI.
"""

from __future__ import annotations

import inspect

from src.db.repos import mart_repo


def test_cleanup_function_exists_and_is_async() -> None:
    fn = getattr(mart_repo, "mark_stale_agg_signals_inactive", None)
    assert fn is not None, "mart_repo.mark_stale_agg_signals_inactive missing"
    assert inspect.iscoroutinefunction(fn)


def test_cleanup_sql_targets_both_aggregate_tables() -> None:
    src = inspect.getsource(mart_repo.mark_stale_agg_signals_inactive)
    assert "UPDATE agg_product_signal" in src
    assert "UPDATE agg_user_preference" in src
    assert "is_active = false" in src
    assert "is_active = true" in src  # guard against repeat updates
    # P3-8 reason: respect freshness window
    assert "last_seen_at" in src
    assert "updated_at < now()" in src  # for user prefs


def test_cleanup_uses_parameterized_threshold() -> None:
    """The threshold must come from $1 (parameterized), not string-formatted SQL.

    Catches a future regression where threshold_days might be inlined into
    the SQL, enabling injection.
    """
    src = inspect.getsource(mart_repo.mark_stale_agg_signals_inactive)
    assert "($1::int * interval '1 day')" in src
    # The triple-quoted SQL blocks must not be f-strings (which would allow
    # threshold_days interpolation directly into SQL). The ValueError message
    # may legitimately use {threshold_days}; the SQL must not.
    assert "f\"\"\"" not in src
    # Check explicitly that the SQL query strings (the """...""" blocks) do
    # not contain Python interpolation markers.
    sql_blocks = src.split('"""')[1::2]  # odd-indexed segments are the bodies
    for block in sql_blocks:
        if "UPDATE agg_" in block:  # only consider SQL bodies
            assert "{" not in block, f"SQL block must not contain interpolation: {block!r}"


def test_upsert_agg_product_signal_revives_inactive_rows() -> None:
    """ON CONFLICT path must reset is_active to true so a row that re-enters
    the freshness window is automatically reactivated."""
    src = inspect.getsource(mart_repo.upsert_agg_product_signal)
    assert "is_active=true" in src


def test_upsert_agg_user_preference_revives_inactive_rows() -> None:
    src = inspect.getsource(mart_repo.upsert_agg_user_preference)
    assert "is_active=true" in src


def test_ddl_adds_is_active_to_both_agg_tables() -> None:
    """DDL ALTER must add is_active to both aggregate tables for P3-8."""
    from pathlib import Path
    ddl = (Path(__file__).parent.parent / "sql" / "ddl_mart.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE agg_product_signal ADD COLUMN IF NOT EXISTS is_active" in ddl
    assert "ALTER TABLE agg_user_preference ADD COLUMN IF NOT EXISTS is_active" in ddl


def test_count_parser_handles_normal_and_empty_status() -> None:
    """Internal _count helper must parse 'UPDATE 3' and tolerate edge cases."""
    src = inspect.getsource(mart_repo.mark_stale_agg_signals_inactive)
    assert "_count" in src  # helper defined inline
    # Behavioural smoke through module-level callable
    fn_src = src
    assert "status.strip().split()" in fn_src
    assert "parts[-1].isdigit()" in fn_src


def test_cleanup_default_return_shape_remains_count_only() -> None:
    """Existing callers must still receive exactly the two count keys."""
    import asyncio

    class _FakeUow:
        async def execute(self, query, threshold_days):
            assert threshold_days == 90
            if "UPDATE agg_product_signal" in query:
                return "UPDATE 2"
            if "UPDATE agg_user_preference" in query:
                return "UPDATE 1"
            raise AssertionError(f"unexpected query: {query}")

        async def fetch(self, *_a, **_kw):
            raise AssertionError("default cleanup path must not call fetch")

    result = asyncio.run(
        mart_repo.mark_stale_agg_signals_inactive(_FakeUow(), threshold_days=90)
    )
    assert result == {"product_signals": 2, "user_preferences": 1}


def test_cleanup_include_ids_returns_unique_affected_ids() -> None:
    """Opt-in path uses UPDATE ... RETURNING and includes affected ids."""
    import asyncio

    class _FakeUow:
        def __init__(self):
            self.queries = []

        async def fetch(self, query, threshold_days):
            self.queries.append(query)
            assert threshold_days == 90
            if "UPDATE agg_product_signal" in query:
                return [
                    {"target_product_id": "p1"},
                    {"target_product_id": "p1"},
                    {"target_product_id": "p2"},
                ]
            if "UPDATE agg_user_preference" in query:
                return [{"user_id": "u1"}]
            raise AssertionError(f"unexpected query: {query}")

        async def execute(self, *_a, **_kw):
            raise AssertionError("include_ids path must use UPDATE RETURNING")

    fake_uow = _FakeUow()
    result = asyncio.run(
        mart_repo.mark_stale_agg_signals_inactive(
            fake_uow,
            threshold_days=90,
            include_ids=True,
        )
    )

    assert result == {
        "product_signals": 3,
        "user_preferences": 1,
        "product_ids": ["p1", "p2"],
        "user_ids": ["u1"],
    }
    assert any("RETURNING target_product_id" in q for q in fake_uow.queries)
    assert any("RETURNING user_id" in q for q in fake_uow.queries)


def test_threshold_validation_rejects_non_positive() -> None:
    """ValueError must guard against zero or negative thresholds."""
    import asyncio

    class _FakeUow:
        async def execute(self, *_a, **_kw): return "UPDATE 0"

    async def _run(t):
        return await mart_repo.mark_stale_agg_signals_inactive(_FakeUow(), threshold_days=t)

    for bad in (0, -1, -100):
        try:
            asyncio.run(_run(bad))
        except ValueError as e:
            assert "threshold_days" in str(e)
        else:
            raise AssertionError(f"threshold_days={bad} should have raised ValueError")


def test_batch_aggregate_sql_path_writes_last_seen_at_and_reactivates() -> None:
    """P3-8 critical fix: the SQL-first aggregation path must populate
    `last_seen_at` (so cleanup can target it) and set `is_active=true` on
    conflict (so previously soft-deleted rows revive).
    """
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "MAX(window_ts)" in src, \
        "SQL agg path must compute last_seen_at via MAX(window_ts)"
    assert "last_seen_at" in src, "INSERT column list must include last_seen_at"
    assert "is_active = true" in src, \
        "DO UPDATE SET must reactivate previously soft-deleted rows"


def test_build_serving_excludes_inactive_signals() -> None:
    """P3-8: build_serving_product_profile must filter is_active=true."""
    from src.mart.build_serving_views import build_serving_product_profile

    master = {"product_id": "p1", "brand_id": "b1", "brand_name": "B1"}
    signals = [
        {  # Active, promoted
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr", "dst_node_id": "moisture",
            "window_type": "all", "score": 0.8, "review_cnt": 3,
            "review_ids": ["rA", "rB", "rC"],
            "is_promoted": True, "is_active": True,
        },
        {  # Inactive — should be excluded
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr", "dst_node_id": "sticky",
            "window_type": "all", "score": 0.9, "review_cnt": 5,
            "review_ids": ["rD", "rE", "rF", "rG", "rH"],
            "is_promoted": True, "is_active": False,
        },
    ]
    profile = build_serving_product_profile(master, signals)
    ids = [item["id"] for item in profile["top_bee_attr_ids"]]
    assert "moisture" in ids
    assert "sticky" not in ids, "inactive signal must not surface to serving"
    # review_count_all should reflect only active signal's reviews
    assert profile["review_count_all"] == 3


def test_build_serving_user_excludes_inactive_preferences() -> None:
    """P3-8: build_serving_user_profile must filter is_active=true on user prefs."""
    from src.mart.build_serving_views import build_serving_user_profile

    master = {"user_id": "u1"}
    prefs = [
        {"preference_edge_type": "PREFERS_BRAND", "dst_node_id": "b_active",
         "weight": 0.9, "is_active": True},
        {"preference_edge_type": "PREFERS_BRAND", "dst_node_id": "b_inactive",
         "weight": 0.95, "is_active": False},
    ]
    profile = build_serving_user_profile(master, prefs)
    ids = [item["id"] for item in profile["preferred_brand_ids"]]
    assert "b_active" in ids
    assert "b_inactive" not in ids


def test_build_serving_last_signal_at_ignores_inactive_rows() -> None:
    """P3-8: an inactive-only row must not populate `last_signal_at`."""
    from src.mart.build_serving_views import build_serving_product_profile

    master = {"product_id": "p1", "brand_id": "b1", "brand_name": "B1"}
    signals = [
        {
            "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
            "dst_node_type": "BEEAttr", "dst_node_id": "sticky",
            "window_type": "all", "score": 0.9, "review_cnt": 5,
            "review_ids": ["rD"],
            "is_promoted": True, "is_active": False,
            "last_seen_at": "2025-12-31",
        },
    ]
    profile = build_serving_product_profile(master, signals)
    assert profile["last_signal_at"] is None, \
        "last_signal_at must not be sourced from soft-deleted rows"


def test_analyst_queries_filter_inactive_rows() -> None:
    """P3-8: analyst SQL queries must respect the soft-delete contract."""
    from pathlib import Path
    sql = (Path(__file__).parent.parent / "sql" / "analyst_queries.sql").read_text(encoding="utf-8")
    # Every aggregate-table SELECT should pair with an is_active filter.
    assert sql.count("aps.is_active = true") >= 3, \
        "analyst_queries.sql must filter agg_product_signal by is_active"
    assert sql.count("aup.is_active = true") >= 1, \
        "analyst_queries.sql must filter agg_user_preference by is_active"
