"""
Phase 2.3: unit tests for src/common/pipeline_observability.py.

`log_pipeline_stage` / `stage_timer` are pure logging helpers — tests use
`caplog` to parse the emitted JSON message, matching this codebase's existing
style for verifying log output (e.g. test_incremental_cleanup_wiring.py).
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from src.common.pipeline_observability import log_pipeline_stage, stage_timer

logger = logging.getLogger("test_pipeline_observability")


def _stage_records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Parse every caplog record whose message is a pipeline_stage JSON line."""
    parsed = []
    for record in caplog.records:
        try:
            payload = json.loads(record.message)
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("event") == "pipeline_stage":
            parsed.append(payload)
    return parsed


# ---------------------------------------------------------------------------
# log_pipeline_stage
# ---------------------------------------------------------------------------


def test_log_pipeline_stage_emits_parseable_json_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        log_pipeline_stage(logger, "my_stage", 1.23456, row_count=10, run_type="FULL")

    stages = _stage_records(caplog)
    assert len(stages) == 1
    payload = stages[0]
    assert payload["event"] == "pipeline_stage"
    assert payload["stage"] == "my_stage"
    assert payload["elapsed_s"] == 1.2346  # rounded to 4 decimals
    assert payload["row_count"] == 10
    assert payload["run_type"] == "FULL"


def test_log_pipeline_stage_never_raises_on_circular_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    circular: dict = {}
    circular["self"] = circular

    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        log_pipeline_stage(logger, "bad_stage", 0.5, bad_field=circular)  # must not raise

    # Still observable — falls back to a repr-based payload rather than
    # silently dropping the stage-completion signal.
    assert any("bad_stage" in r.message for r in caplog.records)


def test_log_pipeline_stage_rounds_elapsed_to_four_decimals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        log_pipeline_stage(logger, "s", 0.123456789)

    payload = _stage_records(caplog)[0]
    assert payload["elapsed_s"] == 0.1235


# ---------------------------------------------------------------------------
# stage_timer
# ---------------------------------------------------------------------------


def test_stage_timer_logs_status_ok_on_success(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        with stage_timer(logger, "s", run_type="X") as t:
            t.set(row_count=5)

    stages = _stage_records(caplog)
    assert len(stages) == 1
    payload = stages[0]
    assert payload["stage"] == "s"
    assert payload["status"] == "ok"
    assert payload["run_type"] == "X"
    assert payload["row_count"] == 5
    assert payload["elapsed_s"] >= 0


def test_stage_timer_reflects_elapsed_wall_time(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        with stage_timer(logger, "slow_stage"):
            time.sleep(0.01)

    payload = _stage_records(caplog)[0]
    assert payload["elapsed_s"] >= 0.01


def test_stage_timer_logs_status_error_and_reraises_on_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        with pytest.raises(ValueError, match="boom"):
            with stage_timer(logger, "failing_stage", run_type="X") as t:
                t.set(row_count=3)
                raise ValueError("boom")

    stages = _stage_records(caplog)
    assert len(stages) == 1
    payload = stages[0]
    assert payload["stage"] == "failing_stage"
    assert payload["status"] == "error"
    assert payload["row_count"] == 3  # fields set before the failure are preserved


def test_stage_timer_initial_fields_merge_with_set_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        with stage_timer(logger, "s", run_type="FULL", run_id=7) as t:
            t.set(row_count=1)

    payload = _stage_records(caplog)[0]
    assert payload["run_type"] == "FULL"
    assert payload["run_id"] == 7
    assert payload["row_count"] == 1


def test_stage_timer_without_set_call_still_logs(caplog: pytest.LogCaptureFixture) -> None:
    """A stage that never calls `.set(...)` still logs a valid completion line."""
    with caplog.at_level(logging.INFO, logger="test_pipeline_observability"):
        with stage_timer(logger, "empty_stage"):
            pass

    stages = _stage_records(caplog)
    assert len(stages) == 1
    assert stages[0]["status"] == "ok"
