"""
Wave 4 Task 1: DB URL resolution + DSN normalization + pool option defaults.

Tests are unit-level and DO NOT open a real DB connection:
- `resolve_database_url` and `normalize_dsn` are pure.
- `create_pool` is exercised with `asyncpg.create_pool` monkeypatched, so we
  verify URL + options forwarding without a live Postgres.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.db import connection
from src.db.connection import (
    DEFAULT_POOL_OPTIONS,
    create_pool,
    normalize_dsn,
    resolve_database_url,
)


# ---------------------------------------------------------------------------
# normalize_dsn
# ---------------------------------------------------------------------------


def test_normalize_dsn_rewrites_sqlalchemy_prefix() -> None:
    assert (
        normalize_dsn("postgresql+asyncpg://user:pw@host:5432/db")
        == "postgresql://user:pw@host:5432/db"
    )


def test_normalize_dsn_passes_plain_postgresql_unchanged() -> None:
    plain = "postgresql://user:pw@host:5432/db"
    assert normalize_dsn(plain) == plain


def test_normalize_dsn_preserves_query_string() -> None:
    sa = "postgresql+asyncpg://user:pw@host:5432/db?sslmode=require"
    assert normalize_dsn(sa) == "postgresql://user:pw@host:5432/db?sslmode=require"


def test_normalize_dsn_does_not_touch_other_schemes() -> None:
    # Defensive: anything not starting with postgresql+asyncpg:// stays.
    assert normalize_dsn("postgres://x") == "postgres://x"


# ---------------------------------------------------------------------------
# resolve_database_url precedence
# ---------------------------------------------------------------------------


def test_resolve_argument_wins_over_env() -> None:
    env = {
        "GRAPHRAPPING_DATABASE_URL": "postgresql://from-gr-env/db",
        "DATABASE_URL": "postgresql://from-database-url/db",
    }
    assert (
        resolve_database_url("postgresql://from-arg/db", env=env)
        == "postgresql://from-arg/db"
    )


def test_resolve_graphrapping_env_wins_over_database_url() -> None:
    env = {
        "GRAPHRAPPING_DATABASE_URL": "postgresql://from-gr-env/db",
        "DATABASE_URL": "postgresql://from-database-url/db",
    }
    assert (
        resolve_database_url(env=env)
        == "postgresql://from-gr-env/db"
    )


def test_resolve_falls_back_to_database_url() -> None:
    env = {"DATABASE_URL": "postgresql://fallback/db"}
    assert resolve_database_url(env=env) == "postgresql://fallback/db"


def test_resolve_fail_closed_when_none_set() -> None:
    with pytest.raises(RuntimeError) as exc:
        resolve_database_url(env={})
    assert "No database URL provided" in str(exc.value)


def test_resolve_treats_empty_string_as_unset() -> None:
    """Empty env values fall through to the next candidate."""
    env = {
        "GRAPHRAPPING_DATABASE_URL": "",
        "DATABASE_URL": "postgresql://fallback/db",
    }
    assert resolve_database_url(env=env) == "postgresql://fallback/db"


def test_resolve_normalizes_sqlalchemy_prefix_from_env() -> None:
    env = {"GRAPHRAPPING_DATABASE_URL": "postgresql+asyncpg://host/db"}
    assert resolve_database_url(env=env) == "postgresql://host/db"


def test_resolve_normalizes_sqlalchemy_prefix_from_argument() -> None:
    assert (
        resolve_database_url("postgresql+asyncpg://host/db", env={})
        == "postgresql://host/db"
    )


# ---------------------------------------------------------------------------
# Default pool options
# ---------------------------------------------------------------------------


def test_default_pool_options_are_conservative() -> None:
    assert DEFAULT_POOL_OPTIONS == {
        "min_size": 1,
        "max_size": 5,
        "command_timeout": 60,
    }


# ---------------------------------------------------------------------------
# create_pool — forwarding via monkeypatched asyncpg.create_pool
# ---------------------------------------------------------------------------


class _RecordingPool:
    """Stand-in for asyncpg.Pool; records the call args used to create it."""

    def __init__(self, url: str, options: dict[str, Any]) -> None:
        self.url = url
        self.options = options
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_module_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure module-level _pool is None at the start of every test, and
    cleared at the end so cross-test bleed cannot happen."""
    monkeypatch.setattr(connection, "_pool", None)
    yield
    # cleanup
    monkeypatch.setattr(connection, "_pool", None)


@pytest.fixture
def _stub_asyncpg(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingPool]:
    """Replace asyncpg.create_pool with a recorder. Returns the list of
    pools constructed during the test."""
    captured: list[_RecordingPool] = []

    async def _fake_create_pool(url: str, **kwargs: Any) -> _RecordingPool:
        pool = _RecordingPool(url, dict(kwargs))
        captured.append(pool)
        return pool

    # asyncpg.create_pool is dotted in the module
    monkeypatch.setattr(connection.asyncpg, "create_pool", _fake_create_pool)
    return captured


@pytest.mark.asyncio
async def test_create_pool_forwards_defaults(
    _stub_asyncpg: list[_RecordingPool],
) -> None:
    pool = await create_pool("postgresql://host/db")
    assert _stub_asyncpg[0] is pool
    assert pool.url == "postgresql://host/db"
    assert pool.options == {"min_size": 1, "max_size": 5, "command_timeout": 60}


@pytest.mark.asyncio
async def test_create_pool_caller_override_takes_priority(
    _stub_asyncpg: list[_RecordingPool],
) -> None:
    pool = await create_pool("postgresql://host/db", command_timeout=120, max_size=20)
    assert pool.options == {"min_size": 1, "max_size": 20, "command_timeout": 120}


@pytest.mark.asyncio
async def test_create_pool_normalizes_sqlalchemy_dsn(
    _stub_asyncpg: list[_RecordingPool],
) -> None:
    pool = await create_pool("postgresql+asyncpg://host/db")
    assert pool.url == "postgresql://host/db"


@pytest.mark.asyncio
async def test_create_pool_caches_pool_across_calls(
    _stub_asyncpg: list[_RecordingPool],
) -> None:
    p1 = await create_pool("postgresql://host/db")
    p2 = await create_pool("postgresql://other/db")  # ignored — cached
    assert p1 is p2
    assert len(_stub_asyncpg) == 1


@pytest.mark.asyncio
async def test_create_pool_fails_closed_without_url(
    _stub_asyncpg: list[_RecordingPool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Clear both env vars
    monkeypatch.delenv("GRAPHRAPPING_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc:
        await create_pool()
    assert "No database URL provided" in str(exc.value)
    assert _stub_asyncpg == []  # asyncpg never called


@pytest.mark.asyncio
async def test_close_pool_releases_cached_pool(
    _stub_asyncpg: list[_RecordingPool],
) -> None:
    pool = await create_pool("postgresql://host/db")
    assert not pool.closed
    await connection.close_pool()
    assert pool.closed
    # After close, get_pool raises
    with pytest.raises(RuntimeError):
        await connection.get_pool()


# ---------------------------------------------------------------------------
# Codex review hardening (Wave 4 Task 1 2nd-review feedback)
# ---------------------------------------------------------------------------


def test_resolve_treats_whitespace_only_as_unset() -> None:
    """Whitespace-only values should fall through, never reach asyncpg."""
    env = {
        "GRAPHRAPPING_DATABASE_URL": "   ",
        "DATABASE_URL": "postgresql://fallback/db",
    }
    assert resolve_database_url(env=env) == "postgresql://fallback/db"


def test_resolve_argument_whitespace_falls_through_to_env() -> None:
    env = {"GRAPHRAPPING_DATABASE_URL": "postgresql://from-env/db"}
    assert (
        resolve_database_url("  \t  ", env=env)
        == "postgresql://from-env/db"
    )


def test_resolve_fail_closed_when_all_whitespace() -> None:
    with pytest.raises(RuntimeError) as exc:
        resolve_database_url("   ", env={"DATABASE_URL": ""})
    assert "No database URL provided" in str(exc.value)


def test_resolve_strips_surrounding_whitespace() -> None:
    """Real-looking URL with stray padding is honored but trimmed."""
    assert (
        resolve_database_url("  postgresql://host/db\n", env={})
        == "postgresql://host/db"
    )


@pytest.mark.asyncio
async def test_create_pool_concurrent_first_call_shares_one_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent first callers must share the same pool (no leak).

    Uses a yielding fake `asyncpg.create_pool` so the scheduler can switch
    contexts inside the critical section — without the lock, this test would
    record multiple pool creations.
    """
    import asyncio as _asyncio

    captured: list[_RecordingPool] = []

    async def _yielding_create_pool(url: str, **kwargs: Any) -> _RecordingPool:
        await _asyncio.sleep(0)  # forced context switch — exposes race if no lock
        pool = _RecordingPool(url, dict(kwargs))
        captured.append(pool)
        return pool

    monkeypatch.setattr(connection.asyncpg, "create_pool", _yielding_create_pool)

    coros = [create_pool("postgresql://host/db") for _ in range(5)]
    pools = await _asyncio.gather(*coros)
    # All five callers receive the same pool object
    assert all(p is pools[0] for p in pools)
    # Only one underlying asyncpg.create_pool call happened
    assert len(captured) == 1
