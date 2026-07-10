"""
CLI `monitor` subcommand tests.

No real DB: `asyncpg.create_pool` is monkeypatched on `src.cli.asyncpg`
(mirrors `_stub_asyncpg`/`monkeypatch.setattr(connection.asyncpg, ...)` in
`tests/test_db_connection_config.py`), and `src.cli.run_retention_monitor`
is monkeypatched to return a controlled `RetentionMonitorResult` (mirrors
the `_stub_monitor` monkeypatch-the-collector approach in
`tests/test_retention_monitor.py`), so these tests exercise only the CLI's
argument wiring / printing / exit-code logic.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import cli
from src.db.retention_monitor import (
    ActiveSplitCount,
    AggProductSignalWindowCount,
    RetentionMonitorResult,
    RetentionWarning,
    TableRowCount,
)


class _FakePool:
    """Stand-in for asyncpg.Pool; only `close()` is exercised by `_with_pool`."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def _stub_create_pool(monkeypatch: pytest.MonkeyPatch) -> list[_FakePool]:
    """Replace asyncpg.create_pool (as dotted in src.cli) with a fake pool
    factory, so `monitor` never opens a real connection."""
    created: list[_FakePool] = []

    async def _fake_create_pool(_url: str, **_kwargs: Any) -> _FakePool:
        pool = _FakePool()
        created.append(pool)
        return pool

    monkeypatch.setattr(cli.asyncpg, "create_pool", _fake_create_pool)
    return created


def _stub_run_retention_monitor(
    monkeypatch: pytest.MonkeyPatch, result: RetentionMonitorResult
) -> dict[str, Any]:
    """Replace src.cli.run_retention_monitor and capture the kwargs it was
    called with, mirroring the monkeypatched-collector style used for
    run_retention_monitor's own internals in test_retention_monitor.py."""
    captured: dict[str, Any] = {}

    async def _fake_run_retention_monitor(pool: Any, **kwargs: Any) -> RetentionMonitorResult:
        captured["pool"] = pool
        captured["kwargs"] = kwargs
        return result

    monkeypatch.setattr(cli, "run_retention_monitor", _fake_run_retention_monitor)
    return captured


_CLEAN_RESULT = RetentionMonitorResult(
    quarantine_counts=(TableRowCount(table="quarantine_placeholder", row_count=1),),
    quarantine_total=1,
    agg_product_signal_counts=(
        AggProductSignalWindowCount(window_type="all", total=10, active=8, inactive=2),
    ),
    agg_user_preference=ActiveSplitCount(total=5, active=5, inactive=0),
    raw_layer_counts=(TableRowCount(table="review_raw", row_count=906),),
    table_sizes=(),
    warnings=(),
)

_BREACHED_RESULT = RetentionMonitorResult(
    quarantine_counts=(TableRowCount(table="quarantine_placeholder", row_count=999_999),),
    quarantine_total=999_999,
    warnings=(
        RetentionWarning(
            metric="quarantine_total",
            message="quarantine_* combined row count exceeds threshold.",
            actual=999_999,
            threshold=20_000,
        ),
    ),
)


@pytest.mark.asyncio
async def test_monitor_returns_zero_and_prints_report_when_no_warnings(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_run_retention_monitor(monkeypatch, _CLEAN_RESULT)
    parser = cli.build_parser()
    args = parser.parse_args(["monitor", "--dsn", "postgresql://fake/db"])

    exit_code = await cli._run_monitor(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "quarantine_placeholder: 1" in out
    assert "total: 1" in out
    assert "warnings: none" in out
    assert _stub_create_pool[0].closed is True


@pytest.mark.asyncio
async def test_monitor_returns_nonzero_and_prints_warnings_on_breach(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_run_retention_monitor(monkeypatch, _BREACHED_RESULT)
    parser = cli.build_parser()
    args = parser.parse_args(["monitor", "--dsn", "postgresql://fake/db"])

    exit_code = await cli._run_monitor(args)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "warnings:" in out
    assert "quarantine_total" in out
    assert "(actual=999999, threshold=20000)" in out


@pytest.mark.asyncio
async def test_monitor_uses_retention_monitor_defaults_when_no_overrides_given(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_run_retention_monitor(monkeypatch, _CLEAN_RESULT)
    parser = cli.build_parser()
    args = parser.parse_args(["monitor", "--dsn", "postgresql://fake/db"])

    await cli._run_monitor(args)

    kwargs = captured["kwargs"]
    assert kwargs["quarantine_total_threshold"] == cli.DEFAULT_QUARANTINE_TOTAL_THRESHOLD
    assert kwargs["quarantine_per_table_threshold"] == cli.DEFAULT_QUARANTINE_PER_TABLE_THRESHOLD
    assert (
        kwargs["agg_product_signal_active_threshold"]
        == cli.DEFAULT_AGG_PRODUCT_SIGNAL_ACTIVE_THRESHOLD
    )
    assert (
        kwargs["agg_user_preference_active_threshold"]
        == cli.DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD
    )
    assert kwargs["table_size_bytes_threshold"] == cli.DEFAULT_TABLE_SIZE_BYTES_THRESHOLD
    # No per-table raw override flags passed -> None (retention_monitor keeps its own defaults).
    assert kwargs["raw_table_row_thresholds"] is None


@pytest.mark.asyncio
async def test_monitor_cli_overrides_thresholds(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_run_retention_monitor(monkeypatch, _CLEAN_RESULT)
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "monitor",
            "--dsn", "postgresql://fake/db",
            "--quarantine-total-threshold", "5",
            "--quarantine-per-table-threshold", "3",
            "--agg-product-signal-active-threshold", "7",
            "--agg-user-preference-active-threshold", "9",
            "--table-size-bytes-threshold", "1024",
            "--review-raw-threshold", "11",
            "--rel-raw-threshold", "13",
        ]
    )

    await cli._run_monitor(args)

    kwargs = captured["kwargs"]
    assert kwargs["quarantine_total_threshold"] == 5
    assert kwargs["quarantine_per_table_threshold"] == 3
    assert kwargs["agg_product_signal_active_threshold"] == 7
    assert kwargs["agg_user_preference_active_threshold"] == 9
    assert kwargs["table_size_bytes_threshold"] == 1024
    # Only the raw tables explicitly overridden appear (additive override).
    assert kwargs["raw_table_row_thresholds"] == {"review_raw": 11, "rel_raw": 13}


def test_monitor_subcommand_is_registered_in_dispatch() -> None:
    assert cli._DISPATCH["monitor"] is cli._run_monitor


def test_monitor_help_does_not_require_dsn_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """--help must work without GRAPHRAPPING_DATABASE_URL/DATABASE_URL set,
    matching every other DB subcommand (dsn resolution is deferred to
    execution time, not argparse parse time)."""
    monkeypatch.delenv("GRAPHRAPPING_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["monitor", "--help"])

    assert exc_info.value.code == 0
