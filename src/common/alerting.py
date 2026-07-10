"""
Phase 2.3: pipeline failure / retention-warning alert hook.

Fires a best-effort webhook POST when `GRAPHRAPPING_ALERT_WEBHOOK_URL` is
set, for two alert kinds:

  - **pipeline failure** — called from the FAILED-recording code path in
    `run_incremental` (src/jobs/run_incremental_pipeline.py) and
    `run_full_load_to_db` (src/jobs/run_full_load_db.py), right before the
    original exception is re-raised.
  - **retention-threshold breach** — `check_and_alert_retention` runs
    `src.db.retention_monitor.run_retention_monitor` at the end of a
    successful DB pipeline run and alerts if any threshold was breached.
    Opt-in via `GRAPHRAPPING_RETENTION_ALERT_ENABLED` (default off), and
    only wired into the DB pipeline entrypoints
    (`run_full_load_to_db` / `run_incremental_to_db`) per Phase 2.3 scope.

Design contract (fable_doc/03_improvement_plan.md §2.3):
  - `GRAPHRAPPING_ALERT_WEBHOOK_URL` unset/blank -> no-op, no network attempt.
  - Any failure (bad URL, network/DNS/timeout, non-2xx response,
    non-JSON-serializable payload) is caught, logged at WARNING, and
    swallowed. Alerting must never be able to fail — or mask the failure
    of — a pipeline run.
  - Standard library only (`urllib.request`) — no new dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ALERT_WEBHOOK_URL_ENV = "GRAPHRAPPING_ALERT_WEBHOOK_URL"
RETENTION_ALERT_ENABLED_ENV = "GRAPHRAPPING_RETENTION_ALERT_ENABLED"
_TIMEOUT_SECONDS = 5


def _webhook_url() -> str | None:
    url = os.environ.get(ALERT_WEBHOOK_URL_ENV, "").strip()
    return url or None


def is_retention_alert_enabled() -> bool:
    """Opt-in gate for the post-pipeline retention-threshold alert (default off)."""
    return os.environ.get(RETENTION_ALERT_ENABLED_ENV) == "1"


def send_alert(payload: dict[str, Any]) -> bool:
    """POST `payload` as JSON to `GRAPHRAPPING_ALERT_WEBHOOK_URL`.

    No-op (returns False, no network attempt) when the env var is unset or
    blank. Never raises: every failure mode (bad URL, DNS/connection error,
    timeout, non-2xx response, non-JSON-serializable payload) is caught,
    logged at WARNING, and swallowed — callers may call this from a
    FAILED-pipeline code path without risking a second failure masking the
    first.

    Returns True only when the POST request completed without raising
    (fire-and-forget observability, not a delivery guarantee).
    """
    url = _webhook_url()
    if url is None:
        logger.debug("alert webhook disabled: %s not set", ALERT_WEBHOOK_URL_ENV)
        return False

    body = {"emitted_at": datetime.now(timezone.utc).isoformat(), **payload}
    try:
        data = json.dumps(body, default=str).encode("utf-8")
    except Exception:
        # e.g. a circular-referencing `extra` field — `default=str` only
        # covers unknown *types*, not circularity.
        logger.warning("alert payload not JSON-serializable; dropping alert", exc_info=True)
        return False

    try:
        # `Request(...)` is inside the try: a scheme-less/malformed URL raises
        # ValueError at *construction* (not at urlopen), so it must be guarded
        # here too for the never-raises contract to actually hold.
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            logger.info(
                "alert webhook sent (alert_type=%s status=%s)",
                payload.get("alert_type"), getattr(response, "status", None),
            )
        return True
    except Exception:
        # Broad by design: urllib.error.URLError/HTTPError, TimeoutError,
        # OSError, ValueError (malformed URL), ... — alerting must never
        # propagate into the caller's pipeline logic.
        logger.warning(
            "alert webhook POST failed (alert_type=%s)", payload.get("alert_type"),
            exc_info=True,
        )
        return False


def send_pipeline_failure_alert(
    *,
    run_type: str,
    run_id: int | None,
    error_message: str,
    **extra: Any,
) -> bool:
    """Build + send a pipeline-failure summary.

    See `send_alert` for the no-op / never-raises contract this relies on.
    """
    payload: dict[str, Any] = {
        "alert_type": "pipeline_failure",
        "run_type": run_type,
        "run_id": run_id,
        "error_message": error_message,
    }
    payload.update(extra)
    return send_alert(payload)


async def send_pipeline_failure_alert_async(
    *,
    run_type: str,
    run_id: int | None,
    error_message: str,
    **extra: Any,
) -> bool:
    """Async-safe variant of `send_pipeline_failure_alert`.

    `send_alert` posts synchronously via `urllib.request.urlopen`, which blocks
    for up to `_TIMEOUT_SECONDS`. Calling it directly from an async pipeline's
    except-block would stall the event loop for that whole window, so offload
    it to a worker thread. Never raises (upholds the same contract as the sync
    path): any failure — including a `to_thread` scheduling error — is caught,
    logged at WARNING, and swallowed, so this is safe to `await` on a
    FAILED-pipeline code path without masking the original exception.
    """
    try:
        return await asyncio.to_thread(
            send_pipeline_failure_alert,
            run_type=run_type,
            run_id=run_id,
            error_message=error_message,
            **extra,
        )
    except Exception:
        logger.warning(
            "async pipeline-failure alert dispatch failed (run_type=%s run_id=%s)",
            run_type, run_id, exc_info=True,
        )
        return False


def send_retention_warning_alert(
    *,
    run_type: str,
    run_id: int | None,
    warnings: list[dict[str, Any]],
    **extra: Any,
) -> bool:
    """Build + send a retention-threshold-breach summary.

    `warnings` are plain dicts (one per `src.db.retention_monitor.RetentionWarning`)
    so this module carries no import-time dependency on the DB layer.
    No-op (returns False, no network attempt) when `warnings` is empty.
    """
    if not warnings:
        return False
    payload: dict[str, Any] = {
        "alert_type": "retention_warning",
        "run_type": run_type,
        "run_id": run_id,
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    payload.update(extra)
    return send_alert(payload)


async def check_and_alert_retention(
    pool: Any,
    *,
    run_type: str,
    run_id: int | None,
    **retention_kwargs: Any,
) -> None:
    """Run the Phase 1.3 retention monitor and alert on threshold breaches.

    Opt-in via `is_retention_alert_enabled()` (env
    `GRAPHRAPPING_RETENTION_ALERT_ENABLED`, default off) — wired into the DB
    pipeline entrypoints only (`run_full_load_to_db` / `run_incremental_to_db`)
    per Phase 2.3 scope. `src.db.retention_monitor` is imported lazily so this
    module has no module-level dependency on the DB layer.

    Safe to call unconditionally: re-checks the gate internally and never
    raises — a monitor-query hiccup must never fail an otherwise-successful
    pipeline run.
    """
    if not is_retention_alert_enabled():
        return
    try:
        from src.db.retention_monitor import run_retention_monitor

        result = await run_retention_monitor(pool, **retention_kwargs)
        if not result.warnings:
            return
        warnings = [
            {
                "metric": w.metric,
                "message": w.message,
                "actual": w.actual,
                "threshold": w.threshold,
            }
            for w in result.warnings
        ]
        # Offload the blocking urlopen off the event loop (see
        # send_pipeline_failure_alert_async). Still inside this try/except, so a
        # dispatch error is swallowed like the rest of the monitor path.
        await asyncio.to_thread(
            send_retention_warning_alert,
            run_type=run_type, run_id=run_id, warnings=warnings,
        )
    except Exception:
        logger.warning(
            "retention monitor check failed (run_type=%s run_id=%s)",
            run_type, run_id, exc_info=True,
        )
