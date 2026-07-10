"""
Phase 2.3: unit tests for src/common/alerting.py.

No real network calls — `urllib.request.urlopen` is monkeypatched, mirroring
this codebase's existing style (fake collaborators + `caplog` for swallowed-
exception verification, e.g. test_incremental_cleanup_wiring.py).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
from urllib.request import Request

import pytest

from src.common import alerting
from src.db.retention_monitor import RetentionMonitorResult, RetentionWarning


# ---------------------------------------------------------------------------
# send_alert: no-op when GRAPHRAPPING_ALERT_WEBHOOK_URL is unset/blank
# ---------------------------------------------------------------------------


def test_send_alert_noop_when_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(alerting.ALERT_WEBHOOK_URL_ENV, raising=False)

    def _fail_if_called(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("urlopen must not be called when the webhook URL is unset")

    monkeypatch.setattr("urllib.request.urlopen", _fail_if_called)

    assert alerting.send_alert({"alert_type": "x"}) is False


def test_send_alert_noop_when_url_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alerting.ALERT_WEBHOOK_URL_ENV, "   ")

    def _fail_if_called(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("urlopen must not be called for a blank webhook URL")

    monkeypatch.setattr("urllib.request.urlopen", _fail_if_called)

    assert alerting.send_alert({"alert_type": "x"}) is False


# ---------------------------------------------------------------------------
# send_alert: posts JSON when the URL is set
# ---------------------------------------------------------------------------


class _FakeResponse:
    status = 200

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_send_alert_posts_json_payload_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alerting.ALERT_WEBHOOK_URL_ENV, "https://hooks.example.com/alert")
    captured: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: float | None = None) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = alerting.send_alert({"alert_type": "pipeline_failure", "run_id": 7})

    assert result is True
    assert captured["url"] == "https://hooks.example.com/alert"
    assert captured["method"] == "POST"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["body"]["alert_type"] == "pipeline_failure"
    assert captured["body"]["run_id"] == 7
    assert "emitted_at" in captured["body"]
    assert captured["timeout"] == alerting._TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# send_alert: failures are caught, logged, and swallowed (never raise)
# ---------------------------------------------------------------------------


def test_send_alert_swallows_network_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(alerting.ALERT_WEBHOOK_URL_ENV, "https://hooks.example.com/alert")

    def _raise_urlopen(*_a: Any, **_kw: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise_urlopen)

    with caplog.at_level(logging.WARNING):
        result = alerting.send_alert({"alert_type": "pipeline_failure"})  # must not raise

    assert result is False
    assert any("alert webhook POST failed" in r.message for r in caplog.records)


def test_send_alert_swallows_non_serializable_payload(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(alerting.ALERT_WEBHOOK_URL_ENV, "https://hooks.example.com/alert")
    called = {"n": 0}

    def _fail_if_called(*_a: Any, **_kw: Any) -> Any:
        called["n"] += 1
        raise AssertionError("must not attempt a network call for an unserializable payload")

    monkeypatch.setattr("urllib.request.urlopen", _fail_if_called)

    # `default=str` only rescues *unknown types* — circular refs still raise.
    circular: dict[str, Any] = {}
    circular["self"] = circular

    with caplog.at_level(logging.WARNING):
        result = alerting.send_alert({"alert_type": "x", "bad": circular})  # must not raise

    assert result is False
    assert called["n"] == 0
    assert any("not JSON-serializable" in r.message for r in caplog.records)


def test_send_alert_swallows_malformed_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A scheme-less/malformed URL raises ValueError at `Request(...)`
    construction — i.e. *before* urlopen — so it must be guarded inside the
    try and swallowed, not propagated into the caller's pipeline path."""
    monkeypatch.setenv(alerting.ALERT_WEBHOOK_URL_ENV, "hooks.example.com/no-scheme")

    def _fail_if_called(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("urlopen must not be reached when Request() itself raises")

    monkeypatch.setattr("urllib.request.urlopen", _fail_if_called)

    with caplog.at_level(logging.WARNING):
        result = alerting.send_alert({"alert_type": "pipeline_failure"})  # must not raise

    assert result is False
    assert any("alert webhook POST failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# is_retention_alert_enabled
# ---------------------------------------------------------------------------


def test_is_retention_alert_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(alerting.RETENTION_ALERT_ENABLED_ENV, raising=False)
    assert alerting.is_retention_alert_enabled() is False


def test_is_retention_alert_enabled_requires_exact_string_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "true")
    assert alerting.is_retention_alert_enabled() is False

    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "1")
    assert alerting.is_retention_alert_enabled() is True


# ---------------------------------------------------------------------------
# send_pipeline_failure_alert / send_retention_warning_alert: payload shape
# ---------------------------------------------------------------------------


def test_send_pipeline_failure_alert_builds_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_send_alert(payload: dict[str, Any]) -> bool:
        captured.update(payload)
        return True

    monkeypatch.setattr(alerting, "send_alert", _fake_send_alert)

    result = alerting.send_pipeline_failure_alert(
        run_type="INCREMENTAL", run_id=5, error_message="boom", extra_field="x",
    )

    assert result is True
    assert captured == {
        "alert_type": "pipeline_failure",
        "run_type": "INCREMENTAL",
        "run_id": 5,
        "error_message": "boom",
        "extra_field": "x",
    }


def test_send_pipeline_failure_alert_allows_none_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_id may be None (e.g. failure before a pipeline_run row exists)."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(alerting, "send_alert", lambda payload: captured.update(payload) or True)

    alerting.send_pipeline_failure_alert(run_type="FULL", run_id=None, error_message="boom")

    assert captured["run_id"] is None


def test_send_retention_warning_alert_noop_when_no_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def _fail_if_called(payload: dict[str, Any]) -> bool:
        called["n"] += 1
        return True

    monkeypatch.setattr(alerting, "send_alert", _fail_if_called)

    result = alerting.send_retention_warning_alert(run_type="FULL", run_id=1, warnings=[])

    assert result is False
    assert called["n"] == 0


def test_send_retention_warning_alert_builds_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(alerting, "send_alert", lambda payload: captured.update(payload) or True)

    warnings = [{"metric": "m", "message": "msg", "actual": 1, "threshold": 0}]
    result = alerting.send_retention_warning_alert(run_type="FULL", run_id=9, warnings=warnings)

    assert result is True
    assert captured["alert_type"] == "retention_warning"
    assert captured["run_type"] == "FULL"
    assert captured["run_id"] == 9
    assert captured["warning_count"] == 1
    assert captured["warnings"] == warnings


# ---------------------------------------------------------------------------
# send_pipeline_failure_alert_async: offloads the blocking send to a thread,
# never raises (safe to await from a FAILED-pipeline except-block).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_pipeline_failure_alert_async_offloads_to_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async variant must run the blocking sync send on a worker thread
    (via asyncio.to_thread), not inline on the event-loop thread — otherwise
    urlopen's timeout stalls the loop."""
    caller_thread = threading.current_thread()
    seen: dict[str, Any] = {}

    def _fake_sync(**kwargs: Any) -> bool:
        seen["thread"] = threading.current_thread()
        seen["kwargs"] = kwargs
        return True

    monkeypatch.setattr(alerting, "send_pipeline_failure_alert", _fake_sync)

    result = await alerting.send_pipeline_failure_alert_async(
        run_type="INCREMENTAL", run_id=7, error_message="boom", extra_field="x",
    )

    assert result is True
    assert seen["kwargs"] == {
        "run_type": "INCREMENTAL", "run_id": 7, "error_message": "boom", "extra_field": "x",
    }
    # Offloaded: the sync send ran on a different thread than the caller.
    assert seen["thread"] is not caller_thread


@pytest.mark.asyncio
async def test_send_pipeline_failure_alert_async_swallows_dispatch_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a to_thread scheduling failure must be swallowed — never propagate
    into the pipeline's except-block where it would mask the original error."""

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("executor down")

    monkeypatch.setattr(alerting.asyncio, "to_thread", _boom)

    with caplog.at_level(logging.WARNING):
        result = await alerting.send_pipeline_failure_alert_async(
            run_type="FULL", run_id=1, error_message="x",
        )  # must not raise

    assert result is False
    assert any("async pipeline-failure alert dispatch failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# check_and_alert_retention: gating + wiring + exception-swallowing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_alert_retention_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(alerting.RETENTION_ALERT_ENABLED_ENV, raising=False)
    calls = {"n": 0}

    async def _fake_run_retention_monitor(pool: Any, **_kw: Any) -> RetentionMonitorResult:
        calls["n"] += 1
        return RetentionMonitorResult()

    monkeypatch.setattr(
        "src.db.retention_monitor.run_retention_monitor", _fake_run_retention_monitor
    )

    await alerting.check_and_alert_retention(pool=None, run_type="FULL", run_id=1)

    assert calls["n"] == 0, "the monitor query must not run when the feature is disabled"


@pytest.mark.asyncio
async def test_check_and_alert_retention_noop_when_no_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "1")

    async def _fake_run_retention_monitor(pool: Any, **_kw: Any) -> RetentionMonitorResult:
        return RetentionMonitorResult()  # warnings=()

    monkeypatch.setattr(
        "src.db.retention_monitor.run_retention_monitor", _fake_run_retention_monitor
    )
    sent = {"n": 0}

    def _fail_if_called(**_kw: Any) -> bool:
        sent["n"] += 1
        return True

    monkeypatch.setattr(alerting, "send_retention_warning_alert", _fail_if_called)

    await alerting.check_and_alert_retention(pool=None, run_type="FULL", run_id=1)

    assert sent["n"] == 0


@pytest.mark.asyncio
async def test_check_and_alert_retention_sends_alert_on_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "1")
    warning = RetentionWarning(metric="quarantine_total", message="too many", actual=99, threshold=10)

    async def _fake_run_retention_monitor(pool: Any, **_kw: Any) -> RetentionMonitorResult:
        return RetentionMonitorResult(warnings=(warning,))

    monkeypatch.setattr(
        "src.db.retention_monitor.run_retention_monitor", _fake_run_retention_monitor
    )
    captured: dict[str, Any] = {}

    def _fake_send(**kwargs: Any) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(alerting, "send_retention_warning_alert", _fake_send)

    await alerting.check_and_alert_retention(pool=object(), run_type="FULL", run_id=42)

    assert captured["run_type"] == "FULL"
    assert captured["run_id"] == 42
    assert captured["warnings"] == [
        {"metric": "quarantine_total", "message": "too many", "actual": 99, "threshold": 10}
    ]


@pytest.mark.asyncio
async def test_check_and_alert_retention_offloads_send_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retention-warning send is a blocking urlopen under the hood, so it
    must be offloaded via asyncio.to_thread rather than run inline on the loop."""
    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "1")
    warning = RetentionWarning(metric="quarantine_total", message="too many", actual=99, threshold=10)

    async def _fake_run_retention_monitor(pool: Any, **_kw: Any) -> RetentionMonitorResult:
        return RetentionMonitorResult(warnings=(warning,))

    monkeypatch.setattr(
        "src.db.retention_monitor.run_retention_monitor", _fake_run_retention_monitor
    )

    caller_thread = threading.current_thread()
    seen: dict[str, Any] = {}

    def _fake_send(**kwargs: Any) -> bool:
        seen["thread"] = threading.current_thread()
        seen["kwargs"] = kwargs
        return True

    monkeypatch.setattr(alerting, "send_retention_warning_alert", _fake_send)

    await alerting.check_and_alert_retention(pool=object(), run_type="FULL", run_id=42)

    assert seen["kwargs"]["run_type"] == "FULL"
    assert seen["kwargs"]["run_id"] == 42
    # Offloaded: the send ran on a worker thread, not inline on the event loop.
    assert seen["thread"] is not caller_thread


@pytest.mark.asyncio
async def test_check_and_alert_retention_swallows_monitor_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(alerting.RETENTION_ALERT_ENABLED_ENV, "1")

    async def _raise(pool: Any, **_kw: Any) -> RetentionMonitorResult:
        raise RuntimeError("db exploded")

    monkeypatch.setattr("src.db.retention_monitor.run_retention_monitor", _raise)

    with caplog.at_level(logging.WARNING):
        await alerting.check_and_alert_retention(pool=None, run_type="FULL", run_id=1)  # must not raise

    assert any("retention monitor check failed" in r.message for r in caplog.records)
