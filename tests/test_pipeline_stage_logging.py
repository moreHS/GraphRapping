"""
Phase 2.3: pipeline observability wiring — stage-timing logs + failure/
retention alert hooks actually fire from the real pipeline entrypoints.

Three tiers, matching this codebase's established test style (see
test_incremental_cleanup_wiring.py / test_incremental_watermark_safety.py):

  1. Fully behavioral, no DB: `run_batch` (src/jobs/run_daily_pipeline.py) is
     pure in-memory, so its 4 compute-stage logs are verified against a real
     call with `caplog` (fixtures mirror test_quarantine_batch_summary.py).
  2. Fake-pool behavioral: `_maybe_run_stale_cleanup` and `run_incremental`'s
     failure-alert path are exercised with the same fake `UnitOfWork`/pool
     doubles already used by test_incremental_cleanup_wiring.py.
  3. `inspect.getsource` contract checks for the deep DB entrypoints
     (`run_full_load_to_db`, `run_incremental_to_db`). These are a SECONDARY
     signal only: they prove the `stage_timer` / alert / retention call *text*
     is present in the source, not that it fires. Comments are stripped first
     (`_strip_comments`) so a stage/call name mentioned only in a comment can
     no longer satisfy the check — the false positive that made these tests
     pass on documentation alone.

     The CANONICAL execution proof lives in the PG-gated suites, which assert
     the structured `pipeline_stage` JSON lines are actually EMITTED during a
     real load (Phase 2.6):
       - full load     → test_full_load_db.py
         ::test_run_full_load_to_db_matches_in_memory_baseline
       - incremental   → test_incremental_pipeline_db.py
         ::test_incremental_processes_newly_inserted_review
     Those run against real Postgres (skipped without
     GRAPHRAPPING_TEST_DATABASE_URL); this file runs in the non-PG quality job,
     so it must NOT add PG-execution assertions here (they would silently never
     run in CI).
"""

from __future__ import annotations

import inspect
import io
import json
import logging
import tokenize
from datetime import datetime, timezone
from typing import Any

import pytest

from src.ingest.review_ingest import RawReviewRecord
from src.jobs import run_full_load_db, run_incremental_pipeline, run_incremental_pipeline_db
from src.jobs.run_daily_pipeline import run_batch
from src.loaders.product_loader import load_products_from_json
from src.loaders.user_loader import load_users_from_profiles
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.qa.quarantine_handler import QuarantineHandler
from src.wrap.projection_registry import ProjectionRegistry


def _parse_stage_logs(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
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
# Tier 1: run_batch (in-memory, no DB) — real call, caplog verification
# ---------------------------------------------------------------------------


def _pipeline_deps():
    product_result = load_products_from_json("mockdata/product_catalog_es.json")
    users = json.load(open("mockdata/user_profiles_normalized.json", encoding="utf-8"))
    user_result = load_users_from_profiles(users)

    bee_norm = BEENormalizer()
    bee_norm.load_dictionaries()

    rel_canon = RelationCanonicalizer()
    rel_canon.load()

    proj_registry = ProjectionRegistry()
    proj_registry.load()

    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()

    return product_result, user_result, bee_norm, rel_canon, proj_registry, deriver


def test_run_batch_emits_all_four_compute_stage_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    product_result, user_result, bee_norm, rel_canon, proj_registry, deriver = _pipeline_deps()
    reviews = [
        RawReviewRecord(
            brnd_nm="없는브랜드",
            clct_site_nm="test",
            prod_nm="없는상품",
            text="상품 매칭 실패를 의도한 테스트 리뷰",
            source_review_key="stage-log-test-1",
        )
    ]

    with caplog.at_level(logging.INFO, logger="src.jobs.run_daily_pipeline"):
        result = run_batch(
            reviews=reviews,
            source="test_stage_logging",
            product_index=product_result.product_index,
            product_masters=product_result.product_masters,
            concept_links=product_result.concept_links,
            user_masters=user_result.user_masters,
            user_adapted_facts=user_result.user_adapted_facts,
            bee_normalizer=bee_norm,
            relation_canonicalizer=rel_canon,
            projection_registry=proj_registry,
            quarantine=QuarantineHandler(),
            deriver=deriver,
        )

    # Sanity: this fixture is the same shape as test_quarantine_batch_summary.py
    # (unmatched brand/product) so the pipeline actually did work worth timing.
    assert result["total_quarantined"] > 0

    stages = _parse_stage_logs(caplog)
    stage_names = {s["stage"] for s in stages}
    assert {
        "review_processing_loop",
        "aggregate_product_signals",
        "user_preference_build",
        "serving_product_build",
    }.issubset(stage_names), f"missing stages, got: {stage_names}"

    by_stage = {s["stage"]: s for s in stages}
    for name, payload in by_stage.items():
        assert payload["run_type"] == "test_stage_logging", name
        assert payload["status"] == "ok", name
        assert payload["elapsed_s"] >= 0, name

    assert by_stage["review_processing_loop"]["row_count"] == 1
    assert by_stage["review_processing_loop"]["quarantine_count"] > 0


def test_run_batch_stage_logs_survive_when_no_reviews(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Zero-row batches must still emit stage lines with row_count=0, not skip
    logging entirely (a quiet day must still be observable)."""
    product_result, user_result, bee_norm, rel_canon, proj_registry, deriver = _pipeline_deps()

    with caplog.at_level(logging.INFO, logger="src.jobs.run_daily_pipeline"):
        run_batch(
            reviews=[],
            source="test_empty_batch",
            product_index=product_result.product_index,
            product_masters={},
            concept_links={},
            user_masters={},
            user_adapted_facts={},
            bee_normalizer=bee_norm,
            relation_canonicalizer=rel_canon,
            projection_registry=proj_registry,
            quarantine=QuarantineHandler(),
            deriver=deriver,
        )

    stages = _parse_stage_logs(caplog)
    by_stage = {s["stage"]: s for s in stages}
    assert by_stage["review_processing_loop"]["row_count"] == 0
    assert by_stage["review_processing_loop"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Tier 2a: _maybe_run_stale_cleanup — fake UnitOfWork, mirrors
# test_incremental_cleanup_wiring.py's existing fakes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_run_stale_cleanup_emits_stage_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    class _FakeUow:
        async def __aenter__(self) -> "_FakeUow":
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    class _FakePool:
        pass

    async def _fake_mark(uow: Any, threshold_days: int = 90, include_ids: bool = False) -> dict[str, Any]:
        assert include_ids is True
        return {"product_signals": 2, "user_preferences": 1, "product_ids": [], "user_ids": []}

    monkeypatch.setenv("GRAPHRAPPING_AGG_CLEANUP_ENABLED", "1")
    monkeypatch.delenv("GRAPHRAPPING_AGG_CLEANUP_DAYS", raising=False)
    monkeypatch.setattr("src.db.repos.mart_repo.mark_stale_agg_signals_inactive", _fake_mark)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.UnitOfWork", lambda _pool: _FakeUow())

    with caplog.at_level(logging.INFO, logger="src.jobs.run_incremental_pipeline"):
        result = await run_incremental_pipeline._maybe_run_stale_cleanup(
            _FakePool(),  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
        )

    assert result == {"product_signals": 2, "user_preferences": 1}

    stages = [s for s in _parse_stage_logs(caplog) if s["stage"] == "stale_cleanup"]
    assert len(stages) == 1
    payload = stages[0]
    assert payload["status"] == "ok"
    assert payload["run_type"] == "INCREMENTAL"
    assert payload["product_signals"] == 2
    assert payload["user_preferences"] == 1
    assert payload["threshold_days"] == 90


@pytest.mark.asyncio
async def test_maybe_run_stale_cleanup_disabled_emits_no_stage_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The common case (feature off) must stay quiet — no stale_cleanup line
    on every single incremental run."""
    monkeypatch.delenv("GRAPHRAPPING_AGG_CLEANUP_ENABLED", raising=False)

    with caplog.at_level(logging.INFO, logger="src.jobs.run_incremental_pipeline"):
        result = await run_incremental_pipeline._maybe_run_stale_cleanup(
            pool=None,  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
        )

    assert result is None
    assert not [s for s in _parse_stage_logs(caplog) if s["stage"] == "stale_cleanup"]


# ---------------------------------------------------------------------------
# Tier 2b: run_incremental's failure-alert path — fake pool (bare
# `async with pool.acquire() as conn`, matching test_retention_monitor.py's
# fake pool shape).
# ---------------------------------------------------------------------------


class _FakeConn:
    async def fetchval(self, query: str, *_args: Any) -> Any:
        if "INSERT INTO pipeline_run" in query:
            return 42
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetchrow(self, query: str, *_args: Any) -> Any:
        if "FROM pipeline_run" in query:
            return None  # no prior COMPLETED run -> get_last_watermark returns (None, None)
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *_args: Any) -> str:
        return "UPDATE 1"


class _FakeAcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePoolForAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


@pytest.mark.asyncio
async def test_run_incremental_sends_failure_alert_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FAILED-recording except-block must call send_pipeline_failure_alert
    with (run_type, run_id, error_message) before re-raising the original
    exception — forced by making fetch_changed_reviews raise after the
    pipeline_run row is created and the watermark is read.

    See test_run_incremental_completes_failed_and_alerts_when_watermark_lookup_raises
    below for the same guarantee when the failure happens earlier, at
    `get_last_watermark` itself (the first statement inside `try:`).
    """

    async def _raise_fetch_changed(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(run_incremental_pipeline, "fetch_changed_reviews", _raise_fetch_changed)

    captured: dict[str, Any] = {}

    async def _fake_alert(**kwargs: Any) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(run_incremental_pipeline, "send_pipeline_failure_alert_async", _fake_alert)

    with pytest.raises(RuntimeError, match="boom"):
        await run_incremental_pipeline.run_incremental(
            pool=_FakePoolForAcquire(_FakeConn()),  # type: ignore[arg-type]
            product_index=None,  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
            bee_normalizer=None,  # type: ignore[arg-type]
            relation_canonicalizer=None,  # type: ignore[arg-type]
            projection_registry=None,  # type: ignore[arg-type]
            predicate_contracts={},
        )

    assert captured["run_type"] == "INCREMENTAL"
    assert captured["run_id"] == 42
    assert "boom" in captured["error_message"]


@pytest.mark.asyncio
async def test_run_incremental_completes_failed_and_alerts_when_watermark_lookup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `get_last_watermark` is the FIRST statement inside `try:`.

    Before the fix, `wm_ts`/`wm_rid` were only ever assigned by that call, so
    a raise there left them unbound; the except-block's
    `complete_pipeline_run(..., wm_ts or run_start, wm_rid or "", ...)` then
    raised `UnboundLocalError` instead of recording the failure — masking the
    original exception, skipping `send_pipeline_failure_alert` entirely, and
    leaving the `pipeline_run` row stuck at RUNNING forever.

    This forces the failure at `get_last_watermark` itself and asserts the
    original exception still propagates, `complete_pipeline_run` still runs
    (with the FAILED watermark fallback: ts=run_start, rid=""), and the
    failure alert still fires.
    """

    async def _raise_get_watermark(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("watermark lookup boom")

    monkeypatch.setattr(run_incremental_pipeline, "get_last_watermark", _raise_get_watermark)

    completed: dict[str, Any] = {}

    async def _fake_complete(
        _pool: Any,
        run_id: int,
        watermark_ts: Any,
        watermark_rid: Any,
        *_args: Any,
        **kwargs: Any,
    ) -> None:
        completed["run_id"] = run_id
        completed["watermark_ts"] = watermark_ts
        completed["watermark_rid"] = watermark_rid
        completed["error_message"] = kwargs.get("error_message")

    monkeypatch.setattr(run_incremental_pipeline, "complete_pipeline_run", _fake_complete)

    alerted: dict[str, Any] = {}

    async def _fake_alert(**kwargs: Any) -> bool:
        alerted.update(kwargs)
        return True

    monkeypatch.setattr(run_incremental_pipeline, "send_pipeline_failure_alert_async", _fake_alert)

    before = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match="watermark lookup boom"):
        await run_incremental_pipeline.run_incremental(
            pool=_FakePoolForAcquire(_FakeConn()),  # type: ignore[arg-type]
            product_index=None,  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
            bee_normalizer=None,  # type: ignore[arg-type]
            relation_canonicalizer=None,  # type: ignore[arg-type]
            projection_registry=None,  # type: ignore[arg-type]
            predicate_contracts={},
        )
    after = datetime.now(timezone.utc)

    assert completed["run_id"] == 42
    assert completed["error_message"] == "watermark lookup boom"
    # wm_ts is None (lookup never completed) -> falls back to run_start.
    assert isinstance(completed["watermark_ts"], datetime)
    assert before <= completed["watermark_ts"] <= after
    # wm_rid is None -> falls back to "".
    assert completed["watermark_rid"] == ""

    assert alerted["run_type"] == "INCREMENTAL"
    assert alerted["run_id"] == 42
    assert "watermark lookup boom" in alerted["error_message"]


@pytest.mark.asyncio
async def test_run_incremental_alerts_before_completion_write_and_preserves_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect-1: the failure alert fires BEFORE the FAILED-recording DB write,
    and a raise from that write is swallowed so the ORIGINAL exception still
    propagates.

    Simulates infra-down: the pipeline fails (original error), and the
    except-block's `complete_pipeline_run` DB UPDATE then fails too (e.g. the
    same lost connection). The webhook — which has no DB dependency — must
    still fire, and the caller must see the ORIGINAL exception, not the
    secondary DB error that would otherwise mask it.
    """

    async def _raise_fetch_changed(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("original boom")

    monkeypatch.setattr(run_incremental_pipeline, "fetch_changed_reviews", _raise_fetch_changed)

    async def _raise_complete(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("db connection lost")

    monkeypatch.setattr(run_incremental_pipeline, "complete_pipeline_run", _raise_complete)

    alerted: dict[str, Any] = {}

    async def _fake_alert(**kwargs: Any) -> bool:
        alerted.update(kwargs)
        return True

    monkeypatch.setattr(run_incremental_pipeline, "send_pipeline_failure_alert_async", _fake_alert)

    # The ORIGINAL exception propagates, not the secondary "db connection lost".
    with pytest.raises(RuntimeError, match="original boom"):
        await run_incremental_pipeline.run_incremental(
            pool=_FakePoolForAcquire(_FakeConn()),  # type: ignore[arg-type]
            product_index=None,  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
            bee_normalizer=None,  # type: ignore[arg-type]
            relation_canonicalizer=None,  # type: ignore[arg-type]
            projection_registry=None,  # type: ignore[arg-type]
            predicate_contracts={},
        )

    # The alert fired even though the FAILED-recording DB write raised.
    assert alerted["run_type"] == "INCREMENTAL"
    assert alerted["run_id"] == 42
    assert "original boom" in alerted["error_message"]


# ---------------------------------------------------------------------------
# Tier 3: inspect.getsource contract checks for the deep DB entrypoints.
#
# SECONDARY signal only (see module docstring): these assert the call *text* is
# present, not that it fires. Comments are stripped first so a stage/call name
# that appears only in a comment can no longer satisfy the check. The canonical
# execution proof is the PG-gated caplog assertions in test_full_load_db.py /
# test_incremental_pipeline_db.py.
# ---------------------------------------------------------------------------


def _strip_comments(source: str) -> str:
    """Blank out `#` comments so a Tier-3 getsource check can't be satisfied by
    a comment that merely *names* a stage or call — the false positive that let
    these checks pass on documentation alone.

    Uses `tokenize`, so a `#` inside a string literal is preserved (it is a
    STRING token, not a COMMENT). Docstrings are string literals and are left
    intact; since Tier-3 is only a secondary signal, a residual docstring match
    is acceptable — the execution proof lives in the PG-gated caplog tests.
    """
    lines = source.splitlines(keepends=True)
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            (srow, scol), (_erow, ecol) = tok.start, tok.end
            line = lines[srow - 1]
            lines[srow - 1] = line[:scol] + line[ecol:]
    return "".join(lines)


def test_strip_comments_removes_comment_only_matches_but_keeps_string_literals() -> None:
    """Guard for the guard: a stage name in a comment is dropped, the same name
    in a real string literal is kept."""
    stripped = _strip_comments(
        'x = "review_processing_loop"  # mentions aggregate_product_signals\n'
    )
    assert '"review_processing_loop"' in stripped  # real string literal kept
    assert "aggregate_product_signals" not in stripped  # comment-only text gone


def test_run_incremental_source_has_all_named_stages() -> None:
    src = _strip_comments(inspect.getsource(run_incremental_pipeline.run_incremental))
    for stage_name in (
        "load_changed_reviews",
        "review_processing_loop",
        "canonical_signal_persist",
        "aggregate_product_signals",
        "reaggregate_serving_persist",
    ):
        assert f'"{stage_name}"' in src, f"missing stage timing call for {stage_name!r}"


def test_run_full_load_to_db_source_has_all_named_stages() -> None:
    src = _strip_comments(inspect.getsource(run_full_load_db.run_full_load_to_db))
    for stage_name in (
        "in_memory_full_load",
        "layer0_persist",
        "canonical_signal_persist",
        "aggregate_serving_persist",
        "full_load_to_db",
    ):
        assert f'"{stage_name}"' in src, f"missing stage timing call for {stage_name!r}"


def test_run_full_load_to_db_sends_failure_alert_before_reraise() -> None:
    src = _strip_comments(inspect.getsource(run_full_load_db.run_full_load_to_db))
    assert "send_pipeline_failure_alert_async(" in src
    alert_pos = src.rfind("send_pipeline_failure_alert_async(")
    # Defect-1 fix: the FAILED-recording DB write (`_complete_full_run`) now
    # runs AFTER the alert — the webhook has no DB dependency, so it must fire
    # first — and both precede the bare re-raise of the original exception.
    complete_pos = src.rfind("_complete_full_run(")
    raise_pos = src.rfind("raise")
    assert alert_pos != -1 and complete_pos != -1 and raise_pos != -1
    assert alert_pos < complete_pos, "alert must be sent before the FAILED-recording DB write"
    assert alert_pos < raise_pos, "alert must be sent before the exception is re-raised"


def test_run_full_load_to_db_calls_retention_check_with_full_run_type() -> None:
    src = _strip_comments(inspect.getsource(run_full_load_db.run_full_load_to_db))
    assert "check_and_alert_retention(" in src
    assert 'run_type="FULL"' in src


def test_run_incremental_to_db_wraps_inner_call_with_stage_timer() -> None:
    src = _strip_comments(inspect.getsource(run_incremental_pipeline_db.run_incremental_to_db))
    assert 'stage_timer(logger, "incremental_to_db"' in src


def test_run_incremental_to_db_calls_retention_check_outside_the_advisory_lock() -> None:
    """The retention check is a read-only report — it must run after the
    `acquire_pipeline_lock` critical section closes, not inside it (holding
    the mutually-exclusive pipeline lock any longer than necessary is
    unnecessary contention for a report that doesn't need it)."""
    src = _strip_comments(inspect.getsource(run_incremental_pipeline_db.run_incremental_to_db))
    assert "check_and_alert_retention(" in src
    assert 'run_type="INCREMENTAL"' in src

    lines = src.splitlines()
    retention_lines = [line for line in lines if "check_and_alert_retention(" in line]
    assert len(retention_lines) == 1
    indent = len(retention_lines[0]) - len(retention_lines[0].lstrip(" "))
    # Function-body statements sit at indent 4; anything inside the
    # `async with acquire_pipeline_lock(...)` block is indented >= 8.
    assert indent == 4, (
        f"check_and_alert_retention must sit at function-body indentation "
        f"(outside the lock's `async with`), got indent={indent}"
    )
