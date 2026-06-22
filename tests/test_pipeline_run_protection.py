"""
`/api/pipeline/run` is gated by env flag + optional token.

Tests target the guard helpers directly (`_check_pipeline_run_allowed`,
`_extract_pipeline_token`) — these encode the security policy. The endpoint
wiring is verified by inspect-based contract tests plus a monkeypatched load
call.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.web import server


def test_disabled_by_default_raises_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        server._check_pipeline_run_allowed(None)
    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail


def test_enable_flag_alone_grants_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    server._check_pipeline_run_allowed(None)  # no raise


def test_token_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.setenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", "secret123")
    with pytest.raises(HTTPException) as exc:
        server._check_pipeline_run_allowed(None)
    assert exc.value.status_code == 403
    assert "missing" in exc.value.detail


def test_wrong_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.setenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", "secret123")
    with pytest.raises(HTTPException) as exc:
        server._check_pipeline_run_allowed("wrong")
    assert exc.value.status_code == 403
    assert "invalid" in exc.value.detail


def test_correct_token_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.setenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", "secret123")
    server._check_pipeline_run_allowed("secret123")  # no raise


def test_bearer_token_parsed() -> None:
    assert server._extract_pipeline_token("Bearer abc", None) == "abc"


def test_x_pipeline_token_header_parsed() -> None:
    assert server._extract_pipeline_token(None, "xyz") == "xyz"


def test_x_pipeline_token_wins_over_authorization() -> None:
    """When both provided, prefer the explicit X-Pipeline-Token (less ambiguous)."""
    assert server._extract_pipeline_token("Bearer abc", "xyz") == "xyz"


def test_missing_token_returns_none() -> None:
    assert server._extract_pipeline_token(None, None) is None


def test_non_bearer_authorization_ignored() -> None:
    """Only `Bearer <token>` is honored on Authorization header."""
    assert server._extract_pipeline_token("Basic abc", None) is None


def test_constant_time_token_compare_used() -> None:
    """Contract: token comparison must use `hmac.compare_digest`."""
    src = inspect.getsource(server._check_pipeline_run_allowed)
    assert "hmac.compare_digest" in src


def test_endpoint_calls_guard_before_body() -> None:
    """Contract: the `pipeline_run` endpoint must invoke the guard helper at
    the top so the body never runs without the env flag set."""
    src = inspect.getsource(server.pipeline_run)
    guard_pos = src.find("_check_pipeline_run_allowed")
    body_marker_pos = src.find("import json as _json")
    assert guard_pos != -1, "guard not invoked"
    assert body_marker_pos == -1 or guard_pos < body_marker_pos, (
        "guard must run before any pipeline body execution"
    )


def test_endpoint_signature_accepts_token_headers() -> None:
    """Endpoint must expose `authorization` and `x_pipeline_token` headers."""
    sig = inspect.signature(server.pipeline_run)
    assert "authorization" in sig.parameters
    assert "x_pipeline_token" in sig.parameters


@pytest.mark.asyncio
async def test_default_mock_fixture_is_loaded_without_remap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The checked-in mock reviews already carry source_product_id alignment.

    The demo endpoint must not random-remap prod_nm/brnd_nm when the request
    uses the default fixture, because that would make source text disagree with
    the source product id contract.
    """
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    captured: dict[str, object] = {}

    def fake_load_demo_data(**kwargs):
        captured.update(kwargs)
        server.demo_state.review_count = 0
        server.demo_state.product_count = 0
        server.demo_state.user_count = 0
        server.demo_state.batch_result = {"total_signals": 0}
        return server.demo_state

    monkeypatch.setattr(server, "load_demo_data", fake_load_demo_data)

    await server.pipeline_run(server.PipelineRunRequest())

    mock_review_path = server._MOCKDATA_DIR / "review_triples_raw.json"
    assert Path(captured["review_json_path"]).resolve() == mock_review_path.resolve()


@pytest.mark.asyncio
async def test_dense_golden_fixture_loads_matching_product_user_and_review_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dense recommendation QA must load review/product/user files together.

    Loading only the dense review file with the wide product/user fixtures would
    silently put the demo back into the 517-product/50-user baseline.
    """
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    captured: dict[str, object] = {}

    def fake_load_demo_data(**kwargs):
        captured.update(kwargs)
        server.demo_state.review_count = 0
        server.demo_state.product_count = 0
        server.demo_state.user_count = 0
        server.demo_state.batch_result = {"total_signals": 0}
        return server.demo_state

    monkeypatch.setattr(server, "load_demo_data", fake_load_demo_data)

    await server.pipeline_run(server.PipelineRunRequest(fixture="dense_golden"))

    dense_dir = server._MOCKDATA_DIR / "dense_golden"
    assert Path(captured["review_json_path"]).resolve() == (dense_dir / "review_triples_raw.json").resolve()
    assert len(captured["product_es_records"]) == 32
    assert len(captured["user_profiles"]) == 6


@pytest.mark.asyncio
async def test_external_review_file_is_loaded_as_is(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    external_reviews = tmp_path / "external_reviews.json"
    external_reviews.write_text("[]", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_load_demo_data(**kwargs):
        captured.update(kwargs)
        server.demo_state.review_count = 0
        server.demo_state.product_count = 0
        server.demo_state.user_count = 0
        server.demo_state.batch_result = {"total_signals": 0}
        return server.demo_state

    monkeypatch.setattr(server, "load_demo_data", fake_load_demo_data)

    await server.pipeline_run(server.PipelineRunRequest(review_json_path=str(external_reviews)))

    assert Path(captured["review_json_path"]).resolve() == external_reviews.resolve()
