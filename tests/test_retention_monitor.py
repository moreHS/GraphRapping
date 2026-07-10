"""
Phase 1.3: retention monitoring unit + optional integration tests.

Three layers, matching the existing DB-contract test style:

- Fake pool/connection tests exercise the SQL-issuing `get_*` collectors
  (aggregation/grouping/skip-on-missing-table logic) without a real DB.
  The fake mirrors `_FakeUow`/`_FakePool` in `test_incremental_cleanup_wiring.py`,
  adapted to asyncpg's `pool.acquire() -> async with ... as conn` shape used
  by `src/db/contract_validator.py` and `src/db/retention_monitor.py`.
- Monkeypatched `get_*` stubs (mirrors `_stub_data` in
  `test_db_contract_validator.py`) verify `run_retention_monitor`'s
  threshold/warning decision logic in isolation, without touching SQL at all.
- Optional behavioural PG coverage mirrors `test_source_identity_collision.py`'s
  `pg_pool` fixture/skip gate (`GRAPHRAPPING_TEST_DATABASE_URL`).

No test in this file exercises DELETE/DROP/partition logic — retention_monitor
is read-only by design (see module docstring).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.db import retention_monitor
from src.db.migrate import migrate
from src.db.retention_monitor import (
    QUARANTINE_TABLES,
    RAW_APPEND_ONLY_TABLES,
    SIZE_MONITORED_TABLES,
    ActiveSplitCount,
    AggProductSignalWindowCount,
    RetentionMonitorResult,
    RetentionWarning,
    TableRowCount,
    TableSize,
    get_agg_product_signal_counts,
    get_agg_user_preference_count,
    get_quarantine_counts,
    get_raw_layer_counts,
    get_table_sizes,
    run_retention_monitor,
)

SQL_DIR = Path(__file__).parent.parent / "sql"


# ---------------------------------------------------------------------------
# Contract / shape checks (no pool needed)
# ---------------------------------------------------------------------------


def test_quarantine_tables_match_repo_mapping() -> None:
    """Codex-relevant regression guard: if a 6th quarantine table is ever
    added to quarantine_repo.py's routing table, this module must track it."""
    from src.db.repos.quarantine_repo import _TABLE_SQL

    assert set(QUARANTINE_TABLES) == set(_TABLE_SQL.keys())


def test_raw_append_only_tables_matches_documented_risk_3() -> None:
    assert RAW_APPEND_ONLY_TABLES == ("review_raw", "ner_raw", "bee_raw", "rel_raw")


def test_size_monitored_tables_is_superset_of_risk_tables() -> None:
    monitored = set(SIZE_MONITORED_TABLES)
    assert set(QUARANTINE_TABLES).issubset(monitored)
    assert set(RAW_APPEND_ONLY_TABLES).issubset(monitored)
    assert {"agg_product_signal", "agg_user_preference"}.issubset(monitored)


def test_all_monitored_tables_exist_in_ddl_files() -> None:
    """DB-free contract test: every hardcoded table name must appear in its
    DDL file, catching typos/renames without needing a real connection."""
    quarantine_ddl = (SQL_DIR / "ddl_quarantine.sql").read_text(encoding="utf-8")
    raw_ddl = (SQL_DIR / "ddl_raw.sql").read_text(encoding="utf-8")
    mart_ddl = (SQL_DIR / "ddl_mart.sql").read_text(encoding="utf-8")

    for table in QUARANTINE_TABLES:
        assert f"create table if not exists {table}" in quarantine_ddl, table
    for table in RAW_APPEND_ONLY_TABLES:
        assert f"create table if not exists {table}" in raw_ddl, table
    assert "create table if not exists agg_product_signal" in mart_ddl
    assert "create table if not exists agg_user_preference" in mart_ddl


def test_retention_monitor_result_is_frozen() -> None:
    result = RetentionMonitorResult()
    with pytest.raises(Exception):
        result.quarantine_total = 5  # type: ignore[misc]


def test_retention_monitor_result_defaults_are_empty() -> None:
    result = RetentionMonitorResult()
    assert result.quarantine_counts == ()
    assert result.quarantine_total == 0
    assert result.agg_product_signal_counts == ()
    assert result.agg_user_preference == ActiveSplitCount(total=0, active=0, inactive=0)
    assert result.raw_layer_counts == ()
    assert result.table_sizes == ()
    assert result.warnings == ()


def test_table_row_count_is_frozen() -> None:
    row = TableRowCount(table="quarantine_placeholder", row_count=1)
    with pytest.raises(Exception):
        row.row_count = 2  # type: ignore[misc]


def test_retention_warning_is_frozen() -> None:
    warning = RetentionWarning(metric="x", message="msg", actual=1, threshold=0)
    with pytest.raises(Exception):
        warning.actual = 2  # type: ignore[misc]


def test_default_raw_table_row_thresholds_cover_all_raw_tables() -> None:
    assert set(retention_monitor.DEFAULT_RAW_TABLE_ROW_THRESHOLDS.keys()) == set(
        RAW_APPEND_ONLY_TABLES
    )


# ---------------------------------------------------------------------------
# Fake pool/connection primitives for the SQL-issuing get_* collectors
# ---------------------------------------------------------------------------


class _FakeAcquireCtx:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeConn":
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


class _FakeConn:
    """Routes fetch/fetchval by SQL substring, fetchrow by the first bound
    arg (used for the per-table pg_relation_size query, where the table name
    is a bind parameter rather than embedded in the query text)."""

    def __init__(
        self,
        *,
        fetchval_by_table: dict[str, int] | None = None,
        fetch_rows_by_substring: dict[str, list[dict[str, Any]]] | None = None,
        fetchrow_by_table: dict[str, Any] | None = None,
    ) -> None:
        self._fetchval_by_table = fetchval_by_table or {}
        self._fetch_rows_by_substring = fetch_rows_by_substring or {}
        self._fetchrow_by_table = fetchrow_by_table or {}

    async def fetchval(self, query: str, *_args: Any) -> Any:
        for table, value in self._fetchval_by_table.items():
            if table in query:
                return value
        raise AssertionError(f"unexpected fetchval query: {query!r}")

    async def fetch(self, query: str, *_args: Any) -> list[dict[str, Any]]:
        for substring, rows in self._fetch_rows_by_substring.items():
            if substring in query:
                return rows
        raise AssertionError(f"unexpected fetch query: {query!r}")

    async def fetchrow(self, _query: str, *args: Any) -> dict[str, Any] | None:
        table = args[0] if args else None
        result = self._fetchrow_by_table.get(table)
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# get_quarantine_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quarantine_counts_returns_all_five_tables() -> None:
    conn = _FakeConn(
        fetchval_by_table={
            "quarantine_product_match": 3,
            "quarantine_placeholder": 1,
            "quarantine_unknown_keyword": 7,
            "quarantine_projection_miss": 0,
            "quarantine_untyped_entity": 2,
        }
    )
    pool = _FakePool(conn)

    result = await get_quarantine_counts(pool)  # type: ignore[arg-type]

    assert {c.table for c in result} == set(QUARANTINE_TABLES)
    by_table = {c.table: c.row_count for c in result}
    assert by_table == {
        "quarantine_product_match": 3,
        "quarantine_placeholder": 1,
        "quarantine_unknown_keyword": 7,
        "quarantine_projection_miss": 0,
        "quarantine_untyped_entity": 2,
    }


@pytest.mark.asyncio
async def test_get_quarantine_counts_treats_null_fetchval_as_zero() -> None:
    conn = _FakeConn(fetchval_by_table={t: None for t in QUARANTINE_TABLES})  # type: ignore[arg-type]
    pool = _FakePool(conn)

    result = await get_quarantine_counts(pool)  # type: ignore[arg-type]

    assert all(c.row_count == 0 for c in result)


# ---------------------------------------------------------------------------
# get_agg_product_signal_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agg_product_signal_counts_groups_by_window_and_active() -> None:
    conn = _FakeConn(
        fetch_rows_by_substring={
            "FROM agg_product_signal": [
                {"window_type": "all", "is_active": True, "row_count": 100},
                {"window_type": "all", "is_active": False, "row_count": 20},
                {"window_type": "30d", "is_active": True, "row_count": 5},
            ],
        }
    )
    pool = _FakePool(conn)

    result = await get_agg_product_signal_counts(pool)  # type: ignore[arg-type]

    by_window = {w.window_type: w for w in result}
    assert by_window["all"] == AggProductSignalWindowCount(
        window_type="all", total=120, active=100, inactive=20
    )
    assert by_window["30d"] == AggProductSignalWindowCount(
        window_type="30d", total=5, active=5, inactive=0
    )


@pytest.mark.asyncio
async def test_get_agg_product_signal_counts_empty_when_no_rows() -> None:
    conn = _FakeConn(fetch_rows_by_substring={"FROM agg_product_signal": []})
    pool = _FakePool(conn)

    result = await get_agg_product_signal_counts(pool)  # type: ignore[arg-type]

    assert result == ()


# ---------------------------------------------------------------------------
# get_agg_user_preference_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agg_user_preference_count_splits_active_inactive() -> None:
    conn = _FakeConn(
        fetch_rows_by_substring={
            "FROM agg_user_preference": [
                {"is_active": True, "row_count": 40},
                {"is_active": False, "row_count": 10},
            ],
        }
    )
    pool = _FakePool(conn)

    result = await get_agg_user_preference_count(pool)  # type: ignore[arg-type]

    assert result == ActiveSplitCount(total=50, active=40, inactive=10)


@pytest.mark.asyncio
async def test_get_agg_user_preference_count_all_active() -> None:
    conn = _FakeConn(
        fetch_rows_by_substring={
            "FROM agg_user_preference": [{"is_active": True, "row_count": 12}],
        }
    )
    pool = _FakePool(conn)

    result = await get_agg_user_preference_count(pool)  # type: ignore[arg-type]

    assert result == ActiveSplitCount(total=12, active=12, inactive=0)


# ---------------------------------------------------------------------------
# get_raw_layer_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_raw_layer_counts_returns_all_four_tables() -> None:
    conn = _FakeConn(
        fetchval_by_table={
            "review_raw": 906,
            "ner_raw": 4507,
            "bee_raw": 2783,
            "rel_raw": 20741,
        }
    )
    pool = _FakePool(conn)

    result = await get_raw_layer_counts(pool)  # type: ignore[arg-type]

    assert {c.table for c in result} == set(RAW_APPEND_ONLY_TABLES)
    by_table = {c.table: c.row_count for c in result}
    assert by_table["rel_raw"] == 20741
    assert by_table["review_raw"] == 906


# ---------------------------------------------------------------------------
# get_table_sizes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_table_sizes_skips_missing_tables() -> None:
    conn = _FakeConn(
        fetchrow_by_table={
            "quarantine_product_match": {
                "relation_bytes": 8192,
                "total_bytes": 16384,
                "pretty_total": "16 kB",
            },
            "ner_raw": asyncpg.exceptions.UndefinedTableError(
                'relation "ner_raw" does not exist'
            ),
        }
    )
    pool = _FakePool(conn)

    result = await get_table_sizes(
        pool,  # type: ignore[arg-type]
        tables=("quarantine_product_match", "ner_raw"),
    )

    assert len(result) == 1
    assert result[0] == TableSize(
        table="quarantine_product_match",
        relation_bytes=8192,
        total_bytes=16384,
        pretty_total="16 kB",
    )


@pytest.mark.asyncio
async def test_get_table_sizes_defaults_to_size_monitored_tables() -> None:
    conn = _FakeConn(
        fetchrow_by_table={
            table: {"relation_bytes": 0, "total_bytes": 0, "pretty_total": "0 bytes"}
            for table in SIZE_MONITORED_TABLES
        }
    )
    pool = _FakePool(conn)

    result = await get_table_sizes(pool)  # type: ignore[arg-type]

    assert {s.table for s in result} == set(SIZE_MONITORED_TABLES)


# ---------------------------------------------------------------------------
# run_retention_monitor — threshold/warning decision logic
# (monkeypatched get_* stubs, mirrors _stub_data in test_db_contract_validator.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_monitor(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the 5 collector functions with controllable stubs.

    Defaults are all clean (zero rows) so a test only moves the numbers it
    cares about, same approach as `_stub_data`/`_stub_collision` in the
    existing contract_validator test suite.
    """
    state: dict[str, Any] = {
        "quarantine": {table: 0 for table in QUARANTINE_TABLES},
        "agg_signal": [],  # list[tuple[window_type, active, inactive]]
        "agg_pref": ActiveSplitCount(total=0, active=0, inactive=0),
        "raw": {table: 0 for table in RAW_APPEND_ONLY_TABLES},
        "sizes": [],  # list[TableSize]
    }

    async def _quarantine(pool: Any) -> tuple[TableRowCount, ...]:
        return tuple(TableRowCount(table=t, row_count=n) for t, n in state["quarantine"].items())

    async def _agg_signal(pool: Any) -> tuple[AggProductSignalWindowCount, ...]:
        return tuple(
            AggProductSignalWindowCount(window_type=w, total=a + i, active=a, inactive=i)
            for w, a, i in state["agg_signal"]
        )

    async def _agg_pref(pool: Any) -> ActiveSplitCount:
        return state["agg_pref"]

    async def _raw(pool: Any) -> tuple[TableRowCount, ...]:
        return tuple(TableRowCount(table=t, row_count=n) for t, n in state["raw"].items())

    async def _sizes(pool: Any) -> tuple[TableSize, ...]:
        return tuple(state["sizes"])

    monkeypatch.setattr(retention_monitor, "get_quarantine_counts", _quarantine)
    monkeypatch.setattr(retention_monitor, "get_agg_product_signal_counts", _agg_signal)
    monkeypatch.setattr(retention_monitor, "get_agg_user_preference_count", _agg_pref)
    monkeypatch.setattr(retention_monitor, "get_raw_layer_counts", _raw)
    monkeypatch.setattr(retention_monitor, "get_table_sizes", _sizes)
    return state


@pytest.mark.asyncio
async def test_no_warnings_when_all_below_default_thresholds(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["quarantine"]["quarantine_product_match"] = 100
    _stub_monitor["agg_signal"] = [("all", 500, 50)]
    _stub_monitor["agg_pref"] = ActiveSplitCount(total=50, active=50, inactive=0)
    _stub_monitor["raw"]["review_raw"] = 906

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    assert result.warnings == ()
    assert result.quarantine_total == 100


@pytest.mark.asyncio
async def test_quarantine_total_threshold_breach_without_per_table_breach(
    _stub_monitor: dict[str, Any],
) -> None:
    # 5 x 4500 = 22500 > 20000 total default, but each table stays under the
    # 8000 per-table default so only the total-level warning should fire.
    for table in QUARANTINE_TABLES:
        _stub_monitor["quarantine"][table] = 4_500

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    assert result.quarantine_total == 22_500
    metrics = {w.metric for w in result.warnings}
    assert metrics == {"quarantine_total"}
    warning = result.warnings[0]
    assert warning.actual == 22_500
    assert warning.threshold == retention_monitor.DEFAULT_QUARANTINE_TOTAL_THRESHOLD


@pytest.mark.asyncio
async def test_quarantine_per_table_threshold_breach_without_total_breach(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["quarantine"]["quarantine_unknown_keyword"] = 9_000  # > 8000 per-table
    # total (9000) stays under the 20000 default total threshold.

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    metrics = {w.metric for w in result.warnings}
    assert metrics == {"quarantine.quarantine_unknown_keyword"}


@pytest.mark.asyncio
async def test_agg_product_signal_active_threshold_breach_per_window(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["agg_signal"] = [
        ("all", 11_000, 500),  # active > 10_000 default -> warning
        ("30d", 200, 10),  # under threshold -> no warning
    ]

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    warning = next(w for w in result.warnings if w.metric == "agg_product_signal.all.active")
    assert warning.actual == 11_000
    assert not any(w.metric.startswith("agg_product_signal.30d") for w in result.warnings)


@pytest.mark.asyncio
async def test_agg_user_preference_active_threshold_breach(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["agg_pref"] = ActiveSplitCount(total=6_000, active=6_000, inactive=0)

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    warning = next(w for w in result.warnings if w.metric == "agg_user_preference.active")
    assert warning.actual == 6_000
    assert warning.threshold == retention_monitor.DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD


@pytest.mark.asyncio
async def test_raw_layer_threshold_breach_uses_per_table_default(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["raw"]["rel_raw"] = 90_000  # > 80_000 default
    _stub_monitor["raw"]["review_raw"] = 100  # well under 5_000 default

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    metrics = {w.metric for w in result.warnings}
    assert metrics == {"raw_layer.rel_raw"}


@pytest.mark.asyncio
async def test_raw_table_row_thresholds_override_merges_with_defaults(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["raw"]["review_raw"] = 1_200  # under default 5000

    result = await run_retention_monitor(
        pool=None,  # type: ignore[arg-type]
        raw_table_row_thresholds={"review_raw": 1_000},
    )

    warning = next(w for w in result.warnings if w.metric == "raw_layer.review_raw")
    assert warning.threshold == 1_000
    # rel_raw/ner_raw/bee_raw must keep their unmodified defaults (additive override).
    assert not any(w.metric == "raw_layer.rel_raw" for w in result.warnings)


@pytest.mark.asyncio
async def test_table_size_threshold_breach(_stub_monitor: dict[str, Any]) -> None:
    big = 600 * 1024 * 1024
    _stub_monitor["sizes"] = [
        TableSize(table="rel_raw", relation_bytes=big, total_bytes=big, pretty_total="600 MB"),
    ]

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    warning = next(w for w in result.warnings if w.metric == "table_size.rel_raw")
    assert warning.actual == big
    assert warning.threshold == retention_monitor.DEFAULT_TABLE_SIZE_BYTES_THRESHOLD


@pytest.mark.asyncio
async def test_all_thresholds_are_overridable_via_kwargs(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["quarantine"]["quarantine_placeholder"] = 50

    result = await run_retention_monitor(
        pool=None,  # type: ignore[arg-type]
        quarantine_total_threshold=10,
        quarantine_per_table_threshold=10,
    )

    assert any(w.metric == "quarantine_total" for w in result.warnings)
    assert any(w.metric == "quarantine.quarantine_placeholder" for w in result.warnings)


@pytest.mark.asyncio
async def test_quarantine_total_is_sum_of_per_table_counts_in_result(
    _stub_monitor: dict[str, Any],
) -> None:
    _stub_monitor["quarantine"]["quarantine_product_match"] = 3
    _stub_monitor["quarantine"]["quarantine_placeholder"] = 4

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    assert result.quarantine_total == 7
    assert result.quarantine_counts == tuple(
        TableRowCount(table=t, row_count=n) for t, n in _stub_monitor["quarantine"].items()
    )


@pytest.mark.asyncio
async def test_run_retention_monitor_never_raises_on_breach(
    _stub_monitor: dict[str, Any],
) -> None:
    """A threshold breach is data, not an error — the function must return
    normally even when every check is breached simultaneously."""
    for table in QUARANTINE_TABLES:
        _stub_monitor["quarantine"][table] = 999_999
    _stub_monitor["agg_signal"] = [("all", 999_999, 0)]
    _stub_monitor["agg_pref"] = ActiveSplitCount(total=999_999, active=999_999, inactive=0)
    for table in RAW_APPEND_ONLY_TABLES:
        _stub_monitor["raw"][table] = 999_999
    _stub_monitor["sizes"] = [
        TableSize(table="rel_raw", relation_bytes=1, total_bytes=1_000_000_000_000, pretty_total="1 TB")
    ]

    result = await run_retention_monitor(pool=None)  # type: ignore[arg-type]

    assert len(result.warnings) >= 6  # total + 5 per-table quarantine, plus others


# ---------------------------------------------------------------------------
# Behavioural PG coverage (optional; skipped unless GRAPHRAPPING_TEST_DATABASE_URL set)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

pg_only = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)


@pytest_asyncio.fixture()
async def pg_pool() -> tuple[asyncpg.Pool, str]:
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_retention_{uuid.uuid4().hex}"

    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()

    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=1,
            server_settings={"search_path": schema},
        )
        await migrate(pool)
        yield pool, schema
    finally:
        if pool is not None:
            await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await admin.close()


@pg_only
@pytest.mark.asyncio
async def test_pg_fresh_schema_has_zero_counts_and_no_warnings(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _ = pg_pool

    result = await run_retention_monitor(pool)

    assert result.quarantine_total == 0
    assert all(c.row_count == 0 for c in result.quarantine_counts)
    assert all(c.row_count == 0 for c in result.raw_layer_counts)
    assert result.agg_user_preference == ActiveSplitCount(total=0, active=0, inactive=0)
    assert result.warnings == ()


@pg_only
@pytest.mark.asyncio
async def test_pg_quarantine_and_raw_row_counts_reflect_inserts(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _ = pg_pool
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO quarantine_unknown_keyword (review_id, surface_text, reason, status)
            VALUES ('r1', 'asdf', 'no dict match', 'PENDING')
            """
        )
        await conn.execute(
            """
            INSERT INTO review_raw (review_id, source, review_text, raw_payload)
            VALUES ('r1', 'test', 'text', '{}'::jsonb)
            """
        )

    quarantine_counts = await get_quarantine_counts(pool)
    raw_counts = await get_raw_layer_counts(pool)

    by_table = {c.table: c.row_count for c in quarantine_counts}
    assert by_table["quarantine_unknown_keyword"] == 1
    assert by_table["quarantine_product_match"] == 0

    raw_by_table = {c.table: c.row_count for c in raw_counts}
    assert raw_by_table["review_raw"] == 1
    assert raw_by_table["ner_raw"] == 0


@pg_only
@pytest.mark.asyncio
async def test_pg_table_sizes_returns_nonnegative_bytes_and_skips_missing_table(
    pg_pool: tuple[asyncpg.Pool, str],
) -> None:
    pool, _ = pg_pool

    sizes = await get_table_sizes(pool, tables=("quarantine_product_match", "does_not_exist_table"))

    assert len(sizes) == 1
    assert sizes[0].table == "quarantine_product_match"
    assert sizes[0].relation_bytes >= 0
    assert sizes[0].total_bytes >= 0
