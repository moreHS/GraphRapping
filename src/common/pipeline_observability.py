"""
Phase 2.3: structured, machine-parseable stage timing logs.

Every daily/incremental/full-load pipeline stage (load, review processing
loop, canonical/signal persist, aggregate, serving build, cleanup) emits
exactly one INFO log line on completion whose *message* is a JSON object,
e.g.::

    {"event": "pipeline_stage", "stage": "review_processing_loop",
     "run_type": "INCREMENTAL", "run_id": 42, "elapsed_s": 1.234,
     "row_count": 906, "status": "ok"}

The payload is embedded as the log message text (not passed via `extra=`).
This project has no JSON log formatter configured anywhere — the stdlib
`logging.Formatter` silently drops `extra=` fields unless the format string
explicitly names them, and `extra=` also risks a `KeyError` if a caller's
field name collides with a reserved `LogRecord` attribute. Encoding the
payload as the message text keeps every stage line grep/jq-able regardless
of handler/formatter configuration, with no collision risk.

Both entry points are defensive: a logging failure (non-JSON-serializable
field, broken handler, ...) must never be able to fail a pipeline run.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

_EVENT = "pipeline_stage"


def log_pipeline_stage(
    logger: logging.Logger,
    stage: str,
    elapsed_s: float,
    **fields: Any,
) -> None:
    """Emit one machine-parseable JSON log line for a completed pipeline stage.

    `stage` names the pipeline step (e.g. "review_processing_loop"). Extra
    keyword fields (row_count, signal_count, run_type, run_id, status, ...)
    are merged into the JSON payload as-is.

    Never raises: this must be safe to call from any pipeline code path,
    including error-handling paths, without risking a secondary failure.
    """
    payload: dict[str, Any] = {
        "event": _EVENT,
        "stage": stage,
        "elapsed_s": round(elapsed_s, 4),
    }
    payload.update(fields)
    try:
        message = json.dumps(payload, default=str)
    except Exception:
        # Circular references etc. survive `default=str` (it only handles
        # *unknown types*, not circularity) — fall back to a repr so the
        # stage completion is still observable even if a field is unusable.
        try:
            message = json.dumps(
                {"event": _EVENT, "stage": stage, "elapsed_s": round(elapsed_s, 4),
                 "fields_repr": repr(fields)}
            )
        except Exception:
            message = f'{{"event": "{_EVENT}", "stage": "{stage}", "log_error": "payload not serializable"}}'
    try:
        logger.info(message)
    except Exception:  # pragma: no cover - logging must never break a pipeline run
        pass


@dataclass
class StageTimer:
    """Mutable field bag a caller populates inside a `stage_timer` block."""

    fields: dict[str, Any] = field(default_factory=dict)

    def set(self, **fields: Any) -> None:
        """Merge additional fields (e.g. row_count) into the stage payload."""
        self.fields.update(fields)


@contextmanager
def stage_timer(
    logger: logging.Logger,
    stage: str,
    **initial_fields: Any,
) -> Iterator[StageTimer]:
    """Time a block of pipeline work and log exactly one JSON line on exit.

    Usage::

        with stage_timer(logger, "review_processing_loop", run_type="FULL") as t:
            for review in reviews:
                ...
            t.set(row_count=len(reviews))

    On exception, still logs (with `status="error"` and whatever fields were
    set before the failure) and then re-raises unchanged — a stage timer
    must never mask or swallow a real pipeline failure.
    """
    timer = StageTimer(fields=dict(initial_fields))
    start = time.monotonic()
    try:
        yield timer
    except BaseException:
        log_pipeline_stage(logger, stage, time.monotonic() - start, status="error", **timer.fields)
        raise
    else:
        log_pipeline_stage(logger, stage, time.monotonic() - start, status="ok", **timer.fields)
