"""
Database connection pool manager using asyncpg.

Wave 4 Task 1 (2026-06-08):
- URL precedence: `database_url` argument > `GRAPHRAPPING_DATABASE_URL` env
  > `DATABASE_URL` env > fail-closed `RuntimeError`.
- DSN normalization: `postgresql+asyncpg://...` is accepted (SQLAlchemy-style
  from adjacent projects) and rewritten to `postgresql://...` so asyncpg
  parses it.
- Default pool options favor consumer-friendly modest concurrency:
  `min_size=1, max_size=5, command_timeout=60`. Caller overrides via kwargs.
- Pool lifecycle: this module owns ONE shared cached `_pool` via
  `create_pool()` / `get_pool()` / `close_pool()`, intended for short-lived
  scripts and the demo server. For long-running services or tests that need
  isolation, call `asyncpg.create_pool(...)` directly (do NOT use this
  module's `create_pool`) and inject the resulting pool into entrypoints in
  `src/jobs/`. Those entrypoints must not close caller-owned pools.
- Concurrency: `create_pool()` uses an asyncio.Lock so concurrent first
  callers race-safely return the same cached pool instead of leaking one.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import asyncpg


_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock = asyncio.Lock()

DEFAULT_POOL_OPTIONS: dict[str, Any] = {
    "min_size": 1,
    "max_size": 5,
    "command_timeout": 60,
}

_SQLALCHEMY_DSN_PREFIX = "postgresql+asyncpg://"
_ASYNCPG_DSN_PREFIX = "postgresql://"


def normalize_dsn(url: str) -> str:
    """Rewrite SQLAlchemy-style `postgresql+asyncpg://` to plain `postgresql://`.

    asyncpg does not accept the `+asyncpg` driver suffix. Adjacent projects
    (e.g. SQLAlchemy-based services) often hand out DSNs in that form, so we
    normalize at the boundary instead of requiring callers to strip it.
    """
    if url.startswith(_SQLALCHEMY_DSN_PREFIX):
        return _ASYNCPG_DSN_PREFIX + url[len(_SQLALCHEMY_DSN_PREFIX):]
    return url


def _coalesce_url(*candidates: str | None) -> str | None:
    """Return the first non-blank candidate. Whitespace-only counts as blank."""
    for c in candidates:
        if c is None:
            continue
        stripped = c.strip()
        if stripped:
            return stripped
    return None


def resolve_database_url(
    database_url: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the effective DB URL with explicit precedence.

    Order:
      1. `database_url` argument (highest)
      2. `GRAPHRAPPING_DATABASE_URL` env
      3. `DATABASE_URL` env
      4. fail-closed `RuntimeError`

    Blank or whitespace-only values are treated as unset and fall through to
    the next candidate. Returned URL has `normalize_dsn` applied.
    """
    if env is None:
        env = os.environ
    candidate = _coalesce_url(
        database_url,
        env.get("GRAPHRAPPING_DATABASE_URL"),
        env.get("DATABASE_URL"),
    )
    if candidate is None:
        raise RuntimeError(
            "No database URL provided. Pass `database_url=` or set the "
            "GRAPHRAPPING_DATABASE_URL or DATABASE_URL environment variable "
            "(blank values are treated as unset)."
        )
    return normalize_dsn(candidate)


async def create_pool(database_url: str | None = None, **kwargs: Any) -> asyncpg.Pool:
    """Create and cache the module-level connection pool.

    Subsequent calls return the cached pool without re-opening. Use
    `close_pool()` to release.

    kwargs are merged onto `DEFAULT_POOL_OPTIONS`, so callers can override
    individual fields (e.g. `command_timeout=120`) without restating the rest.

    Concurrency-safe: double-checked locking ensures two simultaneous first
    callers share the same cached pool instead of leaking one.
    """
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        # Re-check after acquiring the lock — another coroutine may have
        # populated `_pool` while we waited.
        if _pool is not None:
            return _pool

        url = resolve_database_url(database_url)
        options = {**DEFAULT_POOL_OPTIONS, **kwargs}
        _pool = await asyncpg.create_pool(url, **options)
        return _pool


async def get_pool() -> asyncpg.Pool:
    """Get the current pool, raising if not created."""
    if _pool is None:
        raise RuntimeError("Connection pool not created. Call create_pool() first.")
    return _pool


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
