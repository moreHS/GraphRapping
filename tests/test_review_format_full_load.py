"""review_format (full-load) + CLI connector wiring (IC-1 / plan §2·§4·§5·§6).

- FullLoadConfig.review_format defaults to "relation" (byte-identical path);
  "rs_jsonl" reuses rs_jsonl_loader so the 20 raw samples load in-memory e2e.
- CLI full-load surfaces profile-embedded purchase_events into the config
  (codex #8) and forwards --review-format, with fixture-shaped users staying
  byte-identical (purchase_events_by_user=None).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src import cli
from src.jobs.run_full_load import FullLoadConfig, run_full_load

MOCK = Path("mockdata")


# ---------------------------------------------------------------------------
# run_full_load review_format
# ---------------------------------------------------------------------------

def test_default_review_format_is_relation() -> None:
    cfg = FullLoadConfig(review_json_path="x")
    assert cfg.review_format == "relation"


def test_full_load_rs_jsonl_consumes_20_samples() -> None:
    """rs_jsonl selection routes through rs_jsonl_loader; all 20 raw samples are
    consumed by the in-memory pipeline (unmatched products handled as before)."""
    result = run_full_load(
        FullLoadConfig(
            review_json_path=str(MOCK / "review_rs_samples.json"),
            product_es_records=[],  # empty catalog: unmatched reviews still load
            user_profiles={},
            review_format="rs_jsonl",
            kg_mode="off",
        )
    )
    assert result.review_count == 20


def test_full_load_relation_default_still_loads(tmp_path: Path) -> None:
    """A minimal relation file loads via the default path (no rs_jsonl branch)."""
    review = tmp_path / "rel.json"
    review.write_text(
        json.dumps([
            {"source_review_key": "K1", "drup_dt": "2026-01-01", "channel": "031",
             "text": "좋아요", "source_product_id": "100",
             "ner": [], "bee": [], "relation": []}
        ]),
        encoding="utf-8",
    )
    result = run_full_load(
        FullLoadConfig(
            review_json_path=str(review),
            product_es_records=[],
            user_profiles={},
            kg_mode="off",
        )
    )
    assert result.review_count == 1


# ---------------------------------------------------------------------------
# CLI full-load: purchase-event parity + --review-format forwarding
# ---------------------------------------------------------------------------

class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeInMemory:
    review_count = 0
    signal_count = 0
    quarantine_count = 0
    serving_product_count = 0


class _FakeResult:
    def __init__(self) -> None:
        self.run_id = 1
        self.in_memory = _FakeInMemory()
        self.persisted = {}
        self.validation = None


@pytest.fixture
def _capture_full_load(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def _fake_create_pool(_url: str, **_kwargs: Any) -> _FakePool:
        return _FakePool()

    monkeypatch.setattr(cli.asyncpg, "create_pool", _fake_create_pool)

    captured: dict[str, Any] = {}

    async def _fake_run(pool: Any, config: Any, **kwargs: Any) -> _FakeResult:
        captured["config"] = config
        return _FakeResult()

    monkeypatch.setattr(cli, "run_full_load_to_db", _fake_run)
    return captured


_DSN = ["--dsn", "postgresql://fake/db"]


@pytest.mark.asyncio
async def test_cli_full_load_surfaces_purchase_events_and_review_format(
    _capture_full_load: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    products = [{"ONLINE_PROD_SERIAL_NUMBER": "100"}]
    users = {
        "real_u": {
            "basic": {"gender": "F"}, "purchase_analysis": {}, "chat": None,
            "purchase_events": [{"product_id": "100", "purchased_at": "2025-03-01"}],
        }
    }

    def _fake_load_json(path: str) -> Any:
        return products if "product" in path else users

    monkeypatch.setattr(cli, "_load_json", _fake_load_json)
    args = cli.build_parser().parse_args(["full-load", *_DSN, "--review-format", "rs_jsonl"])
    exit_code = await cli._run_full_load(args)

    assert exit_code == 0
    config = _capture_full_load["config"]
    assert config.review_format == "rs_jsonl"
    assert config.purchase_events_by_user is not None
    assert "real_u" in config.purchase_events_by_user
    assert config.purchase_events_by_user["real_u"][0].product_id == "100"


@pytest.mark.asyncio
async def test_cli_full_load_fixture_users_stay_byte_identical(
    _capture_full_load: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fixture-shaped users carry no purchase_events → None (prior behaviour).
    users = {"plain": {"basic": {"gender": "M"}, "purchase_analysis": {}, "chat": None}}

    def _fake_load_json(path: str) -> Any:
        return [] if "product" in path else users

    monkeypatch.setattr(cli, "_load_json", _fake_load_json)
    args = cli.build_parser().parse_args(["full-load", *_DSN])
    await cli._run_full_load(args)

    config = _capture_full_load["config"]
    assert config.review_format == "relation"
    assert config.purchase_events_by_user is None


@pytest.mark.asyncio
async def test_cli_full_load_non_mapping_users_do_not_crash(
    _capture_full_load: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The existing stub returns [] for both loads; the Mapping guard must hold.
    monkeypatch.setattr(cli, "_load_json", lambda _path: [])
    args = cli.build_parser().parse_args(["full-load", *_DSN])
    exit_code = await cli._run_full_load(args)
    assert exit_code == 0
    assert _capture_full_load["config"].purchase_events_by_user is None
