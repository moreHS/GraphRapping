"""
Database connection pool manager using asyncpg.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg


_pool: asyncpg.Pool | None = None


async def create_pool(database_url: str | None = None, **kwargs: Any) -> asyncpg.Pool:
    """Create and return the global connection pool."""
    global _pool
    if _pool is not None:
        return _pool

    url = database_url or os.environ.get("DATABASE_URL", "postgresql://localhost/graphrapping")
    _pool = await asyncpg.create_pool(url, min_size=2, max_size=10, **kwargs)
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
