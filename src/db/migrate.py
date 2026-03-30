"""
Database migration: executes all DDL files in dependency order.

Idempotent: all DDL uses CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

SQL_DIR = Path(__file__).resolve().parent.parent.parent / "sql"

# DDL execution order (dependency-safe)
DDL_ORDER = [
    "ddl_raw.sql",
    "ddl_concept.sql",
    "ddl_canonical.sql",
    "ddl_signal.sql",
    "ddl_mart.sql",
    "ddl_quarantine.sql",
    "ddl_ops.sql",
    "indexes.sql",
]


async def migrate(pool: asyncpg.Pool) -> list[str]:
    """Execute all DDL files in order. Returns list of applied files."""
    applied = []
    async with pool.acquire() as conn:
        for filename in DDL_ORDER:
            path = SQL_DIR / filename
            if not path.exists():
                continue
            sql = path.read_text(encoding="utf-8")
            await conn.execute(sql)

            # Record migration
            await conn.execute("""
                INSERT INTO schema_migrations (version, applied_at)
                VALUES ($1, now())
                ON CONFLICT (version) DO NOTHING
            """, filename)
            applied.append(filename)
    return applied


async def check_migration_status(pool: asyncpg.Pool) -> dict[str, bool]:
    """Check which DDL files have been applied."""
    async with pool.acquire() as conn:
        # schema_migrations may not exist yet
        try:
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            applied = {r["version"] for r in rows}
        except asyncpg.UndefinedTableError:
            applied = set()

    return {f: f in applied for f in DDL_ORDER}
