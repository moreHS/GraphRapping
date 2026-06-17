"""
P3-8 / Wave 3.8: `run_incremental` invokes `mark_stale_agg_signals_inactive`
on BOTH exit paths (early no-changes return AND normal completion) when
`GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`.

Tests are skip-proof — they don't require a real PG fixture. Behavioral PG
coverage is added in `test_postgres_integration.py` alongside the rest of
the asyncpg-bound tests.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import pytest

from src.jobs import run_incremental_pipeline


def test_cleanup_helper_exists_and_is_async() -> None:
    fn = getattr(run_incremental_pipeline, "_maybe_run_stale_cleanup", None)
    assert fn is not None
    assert inspect.iscoroutinefunction(fn)


def test_env_flag_gates_cleanup() -> None:
    """Without `GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`, helper returns None
    without touching the pool."""
    src = inspect.getsource(run_incremental_pipeline._maybe_run_stale_cleanup)
    assert 'GRAPHRAPPING_AGG_CLEANUP_ENABLED' in src
    assert "!= \"1\"" in src or '== "1"' in src or "'1'" in src


def test_threshold_env_var_default_and_parse_guard() -> None:
    src = inspect.getsource(run_incremental_pipeline._maybe_run_stale_cleanup)
    assert "GRAPHRAPPING_AGG_CLEANUP_DAYS" in src
    assert '"90"' in src or "= 90" in src or "= 90)" in src
    # Bad input must warn and fall back, not raise.
    assert "logger.warning" in src


def test_run_incremental_calls_cleanup_on_both_paths() -> None:
    """Contract: cleanup helper is invoked at both run_incremental exits.

    Codex-required: the early `not changed` path must also call cleanup so
    quiet days still trim stale rows.
    """
    src = inspect.getsource(run_incremental_pipeline.run_incremental)
    occurrences = src.count("_maybe_run_stale_cleanup(")
    assert occurrences >= 2, (
        f"Expected ≥2 cleanup calls (early-return + normal completion); "
        f"got {occurrences}. Both exit paths must invoke cleanup."
    )
    assert "product_masters" in src
    assert "concept_links" in src


def test_summary_includes_cleanup_counts() -> None:
    """Both return dicts must surface `cleanup_counts`."""
    src = inspect.getsource(run_incremental_pipeline.run_incremental)
    assert src.count('"cleanup_counts"') >= 2, (
        "Both run_incremental return dicts must include `cleanup_counts`"
    )


def test_ddl_partial_indexes_present() -> None:
    """sql/ddl_mart.sql must declare cleanup-shaped partial indexes."""
    from pathlib import Path
    ddl = (Path(__file__).parent.parent / "sql" / "ddl_mart.sql").read_text(encoding="utf-8")
    assert "idx_aps_active_lastseen" in ddl
    assert "idx_aup_active_updated" in ddl
    assert "WHERE is_active = true" in ddl, (
        "Partial indexes must have `WHERE is_active = true` clause"
    )


def test_cleanup_helper_uses_include_ids_and_rebuild_helper() -> None:
    src = inspect.getsource(run_incremental_pipeline._maybe_run_stale_cleanup)
    assert "include_ids=True" in src
    assert "_rebuild_serving_profiles_after_cleanup" in src


def test_rebuild_helper_reads_active_aggregates_and_upserts_serving_profiles() -> None:
    src = inspect.getsource(run_incremental_pipeline._rebuild_serving_profiles_after_cleanup)
    assert "FROM agg_product_signal" in src
    assert "is_active = true" in src
    assert "FROM user_master" in src
    assert "FROM agg_user_preference" in src
    assert "build_serving_product_profile" in src
    assert "build_serving_user_profile" in src
    assert "upsert_serving_product_profile" in src
    assert "upsert_serving_user_profile" in src


@pytest.mark.asyncio
async def test_helper_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behaviour: helper short-circuits when env flag absent."""
    monkeypatch.delenv("GRAPHRAPPING_AGG_CLEANUP_ENABLED", raising=False)
    result = await run_incremental_pipeline._maybe_run_stale_cleanup(  # type: ignore[arg-type]
        pool=None,
        product_masters={},
        concept_links={},
    )
    assert result is None


@pytest.mark.asyncio
async def test_helper_falls_back_on_invalid_threshold(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid threshold env value → logger.warning + fallback to 90, not raise."""

    class _FakeUow:
        async def __aenter__(self): return self
        async def __aexit__(self, *a: Any) -> None: return None
        async def execute(self, *_a: Any, **_kw: Any) -> str: return "UPDATE 0"

    class _FakePool:
        pass

    captured_threshold: dict[str, int] = {}

    async def _fake_mark(
        uow: Any,
        threshold_days: int = 90,
        include_ids: bool = False,
    ) -> dict[str, int]:  # noqa: ARG001
        assert include_ids is True
        captured_threshold["t"] = threshold_days
        return {"product_signals": 0, "user_preferences": 0}

    monkeypatch.setenv("GRAPHRAPPING_AGG_CLEANUP_ENABLED", "1")
    monkeypatch.setenv("GRAPHRAPPING_AGG_CLEANUP_DAYS", "not-an-int")
    monkeypatch.setattr(
        "src.db.repos.mart_repo.mark_stale_agg_signals_inactive", _fake_mark
    )
    monkeypatch.setattr(
        "src.jobs.run_incremental_pipeline.UnitOfWork", lambda _pool: _FakeUow()
    )

    with caplog.at_level(logging.WARNING):
        result = await run_incremental_pipeline._maybe_run_stale_cleanup(  # type: ignore[arg-type]
            _FakePool(),
            product_masters={},
            concept_links={},
        )

    assert result == {"product_signals": 0, "user_preferences": 0}
    assert captured_threshold["t"] == 90, "must fall back to 90 on parse failure"
    assert any("GRAPHRAPPING_AGG_CLEANUP_DAYS" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_helper_rebuilds_affected_serving_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_ids=True output must drive product and user serving upserts."""
    events: list[str] = []

    class _FakeUow:
        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, exc_type: Any, *_a: Any) -> None:
            events.append("exit:rollback" if exc_type else "exit:commit")

        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            if "FROM agg_product_signal" in query:
                events.append("fetch_product_aggs")
                return [{
                    "target_product_id": "p1",
                    "canonical_edge_type": "HAS_BEE_ATTR_SIGNAL",
                    "dst_node_type": "BEEAttr",
                    "dst_node_id": "moisture",
                    "window_type": "all",
                    "review_cnt": 2,
                    "score": 0.9,
                    "last_seen_at": "2026-01-01T00:00:00+00:00",
                    "is_promoted": True,
                    "is_active": True,
                }]
            if "FROM wrapped_signal" in query:
                events.append("fetch_review_ids")
                return [{"review_id": "r1"}, {"review_id": "r2"}]
            if "FROM agg_user_preference" in query:
                events.append("fetch_user_prefs")
                return [{
                    "user_id": "u1",
                    "preference_edge_type": "PREFERS_BRAND",
                    "dst_node_id": "brand-a",
                    "weight": 0.8,
                    "is_active": True,
                }]
            raise AssertionError(f"unexpected fetch: {query}")

        async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
            if "FROM product_master" in query:
                events.append("fetch_product_master")
                return {
                    "product_id": "p1",
                    "product_name": "DB Product",
                    "brand_id": "b1",
                    "brand_name": "DB Brand",
                    "source_product_id": "p1",
                }
            if "FROM product_review_stats" in query:
                events.append("fetch_source_stats")
                return None
            if "FROM user_master" in query:
                events.append("fetch_user_master")
                return {"user_id": "u1", "age_band": "30s"}
            raise AssertionError(f"unexpected fetchrow: {query}")

    class _FakePool:
        pass

    captured_mark: dict[str, Any] = {}
    product_profiles: list[dict[str, Any]] = []
    user_profiles: list[dict[str, Any]] = []

    async def _fake_mark(
        uow: Any,
        threshold_days: int = 90,
        include_ids: bool = False,
    ) -> dict[str, Any]:  # noqa: ARG001
        events.append("mark")
        captured_mark["threshold_days"] = threshold_days
        captured_mark["include_ids"] = include_ids
        return {
            "product_signals": 1,
            "user_preferences": 1,
            "product_ids": ["p1"],
            "user_ids": ["u1"],
        }

    async def _fake_upsert_product(uow: Any, row: dict[str, Any]) -> None:  # noqa: ARG001
        events.append("upsert_product")
        product_profiles.append(row)

    async def _fake_upsert_user(uow: Any, row: dict[str, Any]) -> None:  # noqa: ARG001
        events.append("upsert_user")
        user_profiles.append(row)

    real_build_product_profile = run_incremental_pipeline.build_serving_product_profile
    real_build_user_profile = run_incremental_pipeline.build_serving_user_profile

    def _record_build_product_profile(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("build_product")
        return real_build_product_profile(*args, **kwargs)

    def _record_build_user_profile(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("build_user")
        return real_build_user_profile(*args, **kwargs)

    monkeypatch.setenv("GRAPHRAPPING_AGG_CLEANUP_ENABLED", "1")
    monkeypatch.delenv("GRAPHRAPPING_AGG_CLEANUP_DAYS", raising=False)
    monkeypatch.setattr(
        "src.db.repos.mart_repo.mark_stale_agg_signals_inactive",
        _fake_mark,
    )
    monkeypatch.setattr(
        "src.db.repos.mart_repo.upsert_serving_product_profile",
        _fake_upsert_product,
    )
    monkeypatch.setattr(
        "src.db.repos.mart_repo.upsert_serving_user_profile",
        _fake_upsert_user,
    )
    monkeypatch.setattr(
        "src.jobs.run_incremental_pipeline.build_serving_product_profile",
        _record_build_product_profile,
    )
    monkeypatch.setattr(
        "src.jobs.run_incremental_pipeline.build_serving_user_profile",
        _record_build_user_profile,
    )
    monkeypatch.setattr(
        "src.jobs.run_incremental_pipeline.UnitOfWork",
        lambda _pool: _FakeUow(),
    )

    result = await run_incremental_pipeline._maybe_run_stale_cleanup(
        _FakePool(),  # type: ignore[arg-type]
        product_masters={"p1": {"product_id": "p1", "brand_id": "b1"}},
        concept_links={},
    )

    assert result == {"product_signals": 1, "user_preferences": 1}
    assert captured_mark == {"threshold_days": 90, "include_ids": True}
    assert product_profiles[0]["top_bee_attr_ids"][0]["id"] == "moisture"
    assert product_profiles[0]["review_count_all"] == 2
    assert user_profiles[0]["preferred_brand_ids"][0]["id"] == "brand-a"
    assert events.count("enter") == 1
    assert events.count("exit:commit") == 1
    assert "exit:rollback" not in events
    commit_index = events.index("exit:commit")
    assert events[:commit_index] == [
        "enter",
        "mark",
        "fetch_product_master",
        "fetch_product_aggs",
        "fetch_review_ids",
        "fetch_source_stats",
        "build_product",
        "upsert_product",
        "fetch_user_master",
        "fetch_user_prefs",
        "build_user",
        "upsert_user",
    ]


@pytest.mark.asyncio
async def test_rebuild_helper_uses_db_product_master_over_stale_caller_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_master: dict[str, Any] = {}

    class _FakeUow:
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            if "FROM agg_product_signal" in query:
                return []
            if "FROM wrapped_signal" in query:
                return []
            raise AssertionError(f"unexpected fetch: {query}")

        async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
            if "FROM product_master" in query:
                return {
                    "product_id": "p1",
                    "product_name": "DB Product",
                    "brand_id": "db-brand",
                    "brand_name": "DB Brand",
                    "source_product_id": "p1",
                    "source_channel": "031",
                    "source_key_type": "ecp_onln_prd_srno",
                }
            if "FROM product_review_stats" in query:
                return None
            raise AssertionError(f"unexpected fetchrow: {query}")

    async def _fake_upsert_product(uow: Any, row: dict[str, Any]) -> None:  # noqa: ARG001
        return None

    def _fake_build_product_profile(
        master: dict[str, Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_master.update(master)
        return {"product_id": master["product_id"]}

    monkeypatch.setattr(
        "src.db.repos.mart_repo.upsert_serving_product_profile",
        _fake_upsert_product,
    )
    monkeypatch.setattr(
        "src.jobs.run_incremental_pipeline.build_serving_product_profile",
        _fake_build_product_profile,
    )

    result = await run_incremental_pipeline._rebuild_serving_profiles_after_cleanup(
        _FakeUow(),  # type: ignore[arg-type]
        {"product_ids": ["p1"], "user_ids": []},
        product_masters={
            "p1": {
                "product_id": "p1",
                "product_name": "Stale Caller Product",
                "brand_id": "stale-brand",
                "brand_name": "Stale Caller Brand",
            }
        },
        concept_links={},
    )

    assert result["serving_products"] == 1
    assert captured_master["brand_id"] == "db-brand"
    assert captured_master["brand_name"] == "DB Brand"
