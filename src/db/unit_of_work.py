"""
Unit of Work: per-review atomic transaction wrapper.

Usage:
    async with UnitOfWork(pool) as uow:
        await uow.execute("INSERT INTO ...", ...)
        await uow.executemany("INSERT INTO ...", [...])
    # auto-commit on success, auto-rollback on exception
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg


class UnitOfWork:
    """Manages a single database transaction with auto-commit/rollback."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._txn: asyncpg.connection.transaction.Transaction | None = None
        self.as_of_ts: datetime = datetime.now(timezone.utc)

    async def __aenter__(self) -> UnitOfWork:
        self._conn = await self._pool.acquire()
        self._txn = self._conn.transaction()
        await self._txn.start()
        self.as_of_ts = datetime.now(timezone.utc)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        txn = self._txn
        conn = self._conn
        if txn is None or conn is None:
            return
        try:
            if exc_type is None:
                await txn.commit()
            else:
                await txn.rollback()
        finally:
            await self._pool.release(conn)
            self._conn = None
            self._txn = None

    async def execute(self, query: str, *args) -> str:
        return str(await self._require_conn().execute(query, *args))

    async def executemany(self, query: str, args: list) -> None:
        await self._require_conn().executemany(query, args)

    async def fetch(self, query: str, *args) -> list:
        return list(await self._require_conn().fetch(query, *args))

    async def fetchval(self, query: str, *args):
        return await self._require_conn().fetchval(query, *args)

    async def fetchrow(self, query: str, *args):
        return await self._require_conn().fetchrow(query, *args)

    def _require_conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("UnitOfWork connection is not active.")
        return self._conn
