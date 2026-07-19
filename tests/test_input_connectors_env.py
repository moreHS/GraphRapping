"""IC-1 connector env wiring for the demo pipeline (plan §2·§6).

Two opt-in envs resolved at CALL time inside pipeline_run:
``GRAPHRAPPING_REVIEW_TRIPLES_JSON`` and ``GRAPHRAPPING_PRODUCT_CATALOG_JSON``.
Priority: explicit request > new connector env > (review only) legacy
``GRAPHRAPPING_DEMO_REVIEW_PATH`` > fixture default. Unset → byte-identical to
the prior fixture path. Missing file → the existing 400.

The connector envs and the legacy demo-review env are cleared by the autouse
conftest fixture, so each test sets only what it exercises.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.web import server


# ---------------------------------------------------------------------------
# Resolver units — exact priority
# ---------------------------------------------------------------------------

def test_resolve_product_default_path_env_over_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = Path("/fx")
    assert server._resolve_product_default_path(fx) == fx / "product_catalog_es.json"
    monkeypatch.setenv("GRAPHRAPPING_PRODUCT_CATALOG_JSON", "/data/catalog.json")
    assert server._resolve_product_default_path(fx) == Path("/data/catalog.json")


def test_resolve_review_default_path_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = Path("/fx")
    # unset new env + no legacy constant → fixture
    monkeypatch.setattr(server, "_DEFAULT_REVIEW_PATH", None)
    assert server._resolve_review_default_path(fx) == fx / "review_triples_raw.json"

    # legacy constant set, new env unset → legacy
    monkeypatch.setattr(server, "_DEFAULT_REVIEW_PATH", "/legacy/reviews.json")
    assert server._resolve_review_default_path(fx) == Path("/legacy/reviews.json")

    # new connector env wins over the legacy constant
    monkeypatch.setenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", "/data/reviews.json")
    assert server._resolve_review_default_path(fx) == Path("/data/reviews.json")


# ---------------------------------------------------------------------------
# pipeline_run wiring
# ---------------------------------------------------------------------------

def _fake_loader(captured: dict) -> object:
    def fake_load_demo_data(**kwargs):
        captured.update(kwargs)
        server.demo_state.review_count = 0
        server.demo_state.product_count = 0
        server.demo_state.user_count = 0
        server.demo_state.batch_result = {"total_signals": 0}
        return server.demo_state
    return fake_load_demo_data


@pytest.fixture
def _enable_pipeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)


@pytest.mark.asyncio
async def test_unset_envs_are_byte_identical_to_fixture(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}
    monkeypatch.setattr(server, "load_demo_data", _fake_loader(captured))
    await server.pipeline_run(server.PipelineRunRequest())
    assert Path(captured["review_json_path"]).resolve() == (
        server._MOCKDATA_DIR / "review_triples_raw.json"
    ).resolve()
    # wide fixture product catalog is the byte-identical default.
    assert len(captured["product_es_records"]) == 517


@pytest.mark.asyncio
async def test_review_env_used_when_no_explicit_request(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_review = tmp_path / "env_reviews.json"
    env_review.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", str(env_review))
    captured: dict = {}
    monkeypatch.setattr(server, "load_demo_data", _fake_loader(captured))
    await server.pipeline_run(server.PipelineRunRequest())
    assert Path(captured["review_json_path"]).resolve() == env_review.resolve()


@pytest.mark.asyncio
async def test_explicit_request_wins_over_review_env(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_review = tmp_path / "env_reviews.json"
    env_review.write_text("[]", encoding="utf-8")
    explicit = tmp_path / "explicit_reviews.json"
    explicit.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", str(env_review))
    captured: dict = {}
    monkeypatch.setattr(server, "load_demo_data", _fake_loader(captured))
    await server.pipeline_run(server.PipelineRunRequest(review_json_path=str(explicit)))
    assert Path(captured["review_json_path"]).resolve() == explicit.resolve()


@pytest.mark.asyncio
async def test_product_env_used_when_no_explicit_request(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_catalog = tmp_path / "env_catalog.json"
    env_catalog.write_text('[{"marker": "from-env"}]', encoding="utf-8")
    monkeypatch.setenv("GRAPHRAPPING_PRODUCT_CATALOG_JSON", str(env_catalog))
    captured: dict = {}
    monkeypatch.setattr(server, "load_demo_data", _fake_loader(captured))
    await server.pipeline_run(server.PipelineRunRequest())
    assert captured["product_es_records"] == [{"marker": "from-env"}]


@pytest.mark.asyncio
async def test_missing_review_env_file_raises_400(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", "/nonexistent/reviews.json")
    monkeypatch.setattr(server, "load_demo_data", _fake_loader({}))
    with pytest.raises(HTTPException) as exc:
        await server.pipeline_run(server.PipelineRunRequest())
    assert exc.value.status_code == 400
    assert "review_json_path" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_product_env_file_raises_400(
    _enable_pipeline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAPHRAPPING_PRODUCT_CATALOG_JSON", "/nonexistent/catalog.json")
    monkeypatch.setattr(server, "load_demo_data", _fake_loader({}))
    with pytest.raises(HTTPException) as exc:
        await server.pipeline_run(server.PipelineRunRequest())
    assert exc.value.status_code == 400
    assert "product_json_path" in exc.value.detail
