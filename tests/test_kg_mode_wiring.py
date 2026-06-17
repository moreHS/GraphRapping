"""
Sub-task 2 (P0-3) kg_mode unification wiring tests.

Before this sub-task, kg_mode default was hardcoded to "off" in 3 functions
(process_review, build_review_persist_bundle, run_batch), unexposed in
run_full_load / run_incremental, and forced to "on" only in load_demo_data.

After P0-3, all 5 entry points resolve via get_kg_mode(arg, env, default).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = PROJECT_ROOT / "mockdata"


# ---------------------------------------------------------------------------
# TC1: get_kg_mode() — arg > env > default precedence + invalid fail-closed
# ---------------------------------------------------------------------------

def test_get_kg_mode_arg_wins_over_env(monkeypatch) -> None:
    from src.common.config_loader import get_kg_mode
    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "on")
    assert get_kg_mode("off") == "off"


def test_get_kg_mode_env_used_when_no_arg(monkeypatch) -> None:
    from src.common.config_loader import get_kg_mode
    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")
    assert get_kg_mode() == "shadow"
    assert get_kg_mode(None) == "shadow"


def test_get_kg_mode_default_used_when_no_arg_no_env(monkeypatch) -> None:
    from src.common.config_loader import get_kg_mode
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    assert get_kg_mode() == "off"
    assert get_kg_mode(default="on") == "on"
    assert get_kg_mode(default="shadow") == "shadow"


def test_get_kg_mode_invalid_arg_raises(monkeypatch) -> None:
    from src.common.config_loader import get_kg_mode
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    # Case-sensitive: "On" must fail
    with pytest.raises(ValueError, match="Invalid kg_mode"):
        get_kg_mode("On")
    with pytest.raises(ValueError, match="Invalid kg_mode"):
        get_kg_mode("true")


def test_get_kg_mode_invalid_env_raises(monkeypatch) -> None:
    from src.common.config_loader import get_kg_mode
    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "true")
    with pytest.raises(ValueError, match="Invalid kg_mode"):
        get_kg_mode()


def test_get_kg_mode_empty_env_raises(monkeypatch) -> None:
    """Explicit empty env must fail closed (not silently fall back to default)."""
    from src.common.config_loader import get_kg_mode
    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "")
    with pytest.raises(ValueError, match="Invalid kg_mode"):
        get_kg_mode()


def test_get_kg_mode_invalid_default_raises(monkeypatch) -> None:
    """A caller passing an invalid default also fails closed."""
    from src.common.config_loader import get_kg_mode
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    with pytest.raises(ValueError, match="Invalid kg_mode"):
        get_kg_mode(default="On")


# ---------------------------------------------------------------------------
# TC2: run_batch() resolves kg_mode from env when no arg
# ---------------------------------------------------------------------------

class _CapturedKgMode(Exception):
    """Sentinel: short-circuit a pipeline after capturing kg_mode."""


def test_process_review_resolves_kg_mode_from_env(monkeypatch) -> None:
    """process_review() default-resolves kg_mode via env."""
    captured: dict = {}

    def spy_get(arg=None, *, default="off"):
        captured["arg"] = arg
        captured["default"] = default
        import os
        env = os.environ.get("GRAPHRAPPING_KG_MODE")
        if arg is not None:
            return arg
        if env is not None:
            return env
        return default

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")
    monkeypatch.setattr("src.jobs.run_daily_pipeline.get_kg_mode", spy_get)

    from src.ingest.review_ingest import RawReviewRecord
    from src.jobs.run_daily_pipeline import process_review
    from src.link.product_matcher import ProductIndex
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.qa.quarantine_handler import QuarantineHandler
    from src.wrap.projection_registry import ProjectionRegistry

    bee = BEENormalizer()
    bee.load_dictionaries()
    rel = RelationCanonicalizer()
    rel.load()
    reg = ProjectionRegistry()
    reg.load()
    process_review(
        record=RawReviewRecord(brnd_nm="b", prod_nm="p", text="t"),
        source="t",
        product_index=ProductIndex.build([]),
        bee_normalizer=bee,
        relation_canonicalizer=rel,
        projection_registry=reg,
        quarantine=QuarantineHandler(),
        predicate_contracts={},
    )
    assert captured["arg"] is None  # no explicit arg
    # spy itself returned "shadow" from env; we just verify get_kg_mode was called.


def test_process_review_shadow_comparison_sees_legacy_facts(monkeypatch) -> None:
    """Shadow comparison must run after legacy BEE/REL facts are populated."""
    import src.jobs.run_daily_pipeline as daily
    from src.ingest.review_ingest import RawReviewRecord
    from src.kg.models import KGResult
    from src.link.product_matcher import ProductIndex
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.qa.quarantine_handler import QuarantineHandler
    from src.wrap.projection_registry import ProjectionRegistry

    seen: dict = {}
    original_shadow_comparison = daily._run_shadow_comparison

    def spy_shadow_comparison(shadow_builder_facts, production_builder_facts, review_id):
        seen["production_builder_facts_len"] = len(production_builder_facts)
        return original_shadow_comparison(
            shadow_builder_facts=shadow_builder_facts,
            production_builder_facts=production_builder_facts,
            review_id=review_id,
        )

    class _FakeKgPipeline:
        def process_review(self, **_kwargs):
            return KGResult(keyword_candidates=[{
                "surface_text": "shadow-new-keyword",
                "bee_attr_raw": "밀착력",
                "context_text": "착붙",
                "reason": "KG shadow mode keyword candidate",
            }])

    bee = BEENormalizer()
    bee.load_from_dicts(
        attr_dict={"밀착력": {"attr_id": "bee_attr_adhesion", "label_ko": "밀착력"}},
        keyword_map={},
    )

    monkeypatch.setattr(daily, "_run_shadow_comparison", spy_shadow_comparison)

    bundle = daily.process_review(
        record=RawReviewRecord(
            brnd_nm="Brand",
            prod_nm="Product",
            text="착붙",
            bee=[{
                "word": "착붙",
                "entity_group": "밀착력",
                "sentiment": "긍정",
                "start": 0,
                "end": 2,
            }],
            relation=[{
                "subject": {"word": "Product", "entity_group": "PRD"},
                "object": {
                    "word": "착붙",
                    "entity_group": "밀착력",
                    "start": 0,
                    "end": 2,
                },
                "relation": "has_attribute",
                "source_type": "NER-BeE",
            }],
        ),
        source="test",
        product_index=ProductIndex.build([{
            "product_id": "p1",
            "brand_name": "Brand",
            "product_name": "Product",
        }]),
        bee_normalizer=bee,
        relation_canonicalizer=RelationCanonicalizer(),
        projection_registry=ProjectionRegistry(),
        quarantine=QuarantineHandler(),
        predicate_contracts={},
        kg_mode="shadow",
        kg_pipeline_instance=_FakeKgPipeline(),
    )

    assert seen["production_builder_facts_len"] > 0
    assert bundle.canonical_facts
    assert any(
        entry.table == "quarantine_unknown_keyword"
        and entry.data["surface_text"] == "shadow-new-keyword"
        for entry in bundle.quarantine_entries
    )


def test_build_review_persist_bundle_resolves_kg_mode_from_env(monkeypatch) -> None:
    """build_review_persist_bundle() default-resolves kg_mode via env."""
    seen: list = []

    def spy_get(arg=None, *, default="off"):
        seen.append(arg)
        import os
        env = os.environ.get("GRAPHRAPPING_KG_MODE")
        if arg is not None:
            return arg
        if env is not None:
            return env
        return default

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")
    monkeypatch.setattr("src.jobs.run_daily_pipeline.get_kg_mode", spy_get)

    from src.ingest.review_ingest import RawReviewRecord
    from src.jobs.run_daily_pipeline import build_review_persist_bundle
    from src.link.product_matcher import ProductIndex
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.wrap.projection_registry import ProjectionRegistry

    bee = BEENormalizer()
    bee.load_dictionaries()
    rel = RelationCanonicalizer()
    rel.load()
    reg = ProjectionRegistry()
    reg.load()
    build_review_persist_bundle(
        record=RawReviewRecord(brnd_nm="b", prod_nm="p", text="t"),
        source="t",
        product_index=ProductIndex.build([]),
        bee_normalizer=bee,
        relation_canonicalizer=rel,
        projection_registry=reg,
        predicate_contracts={},
    )
    # build_review_persist_bundle calls get_kg_mode itself (arg=None → resolves via
    # env), then forwards resolved value to process_review which also calls
    # get_kg_mode (arg="shadow" → returns "shadow" unchanged).
    assert seen, "get_kg_mode must be invoked"
    assert seen[0] is None, "first call (from build_review_persist_bundle) must use env resolution"


def test_run_batch_resolves_kg_mode_from_env(monkeypatch) -> None:
    captured: dict = {}

    def spy_process_review(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        raise _CapturedKgMode()

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")
    monkeypatch.setattr("src.jobs.run_daily_pipeline.process_review", spy_process_review)

    from src.ingest.review_ingest import RawReviewRecord
    from src.jobs.run_daily_pipeline import run_batch
    from src.link.product_matcher import ProductIndex

    with pytest.raises(_CapturedKgMode):
        run_batch(
            reviews=[RawReviewRecord(brnd_nm="b", prod_nm="p", text="t")],
            source="t",
            product_index=ProductIndex.build([]),
            product_masters={}, concept_links={},
            user_masters={}, user_adapted_facts={},
            bee_normalizer=None,  # type: ignore[arg-type]
            relation_canonicalizer=None,  # type: ignore[arg-type]
            projection_registry=None,  # type: ignore[arg-type]
            quarantine=None,  # type: ignore[arg-type]
            predicate_contracts={},
        )
    assert captured["kg_mode"] == "shadow"


# ---------------------------------------------------------------------------
# TC3: run_full_load() — env resolution + arg override
# ---------------------------------------------------------------------------

def _fake_batch_result() -> dict:
    return {
        "review_results": [],
        "agg_signal_count": 0,
        "serving_products": [],
        "serving_users": [],
        "total_signals": 0,
        "total_quarantined": 0,
        "quarantine_by_table": {},
        "quarantine_entries": [],
    }


def test_run_full_load_resolves_kg_mode_from_env(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")
    monkeypatch.setattr("src.jobs.run_full_load.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    run_full_load(FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
    ))
    assert captured["kg_mode"] == "shadow"


def test_run_full_load_kg_mode_arg_overrides_env(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "on")
    monkeypatch.setattr("src.jobs.run_full_load.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    run_full_load(FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
        kg_mode="off",  # arg must override env="on"
    ))
    assert captured["kg_mode"] == "off"


def test_run_full_load_defaults_to_off_without_env(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    monkeypatch.setattr("src.jobs.run_full_load.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    run_full_load(FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
    ))
    assert captured["kg_mode"] == "off"


# ---------------------------------------------------------------------------
# TC4: load_demo_data() — demo default "on", env overrides demo default
# ---------------------------------------------------------------------------

def test_load_demo_data_defaults_to_on_without_env(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    # state.py imports run_batch inside the function — patch source module
    monkeypatch.setattr("src.jobs.run_daily_pipeline.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.web.state import load_demo_data
    load_demo_data(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
        max_reviews=0,
    )
    assert captured["kg_mode"] == "on"


def test_load_demo_data_env_overrides_demo_default(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "off")
    monkeypatch.setattr("src.jobs.run_daily_pipeline.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.web.state import load_demo_data
    load_demo_data(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
        max_reviews=0,
    )
    assert captured["kg_mode"] == "off"


def test_load_demo_data_arg_overrides_env_and_default(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        return _fake_batch_result()

    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "on")
    monkeypatch.setattr("src.jobs.run_daily_pipeline.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.web.state import load_demo_data
    load_demo_data(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
        max_reviews=0,
        kg_mode="shadow",
    )
    assert captured["kg_mode"] == "shadow"


# ---------------------------------------------------------------------------
# TC5: run_incremental() threads kg_mode to process_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_incremental_threads_kg_mode_to_process_review(monkeypatch) -> None:
    captured: dict = {}

    fake_review_row = {
        "review_id": "rv1",
        "is_active": True,
        "updated_at": "2026-04-01T00:00:00Z",
        "source": "test",
        "event_time_utc": None,
        "matched_product_id": None,
    }
    fake_snapshot = {
        "brnd_nm": "b", "prod_nm": "p", "text": "t", "clct_site_nm": "",
        "source_review_key": None, "ner": [], "bee": [], "relation": [],
    }

    async def fake_wm(_pool):
        return None, None

    async def fake_fetch(*_args, **_kwargs):
        return [fake_review_row]

    async def fake_start(*_args, **_kwargs):
        return 1

    async def fake_complete(*_args, **_kwargs):
        return None

    async def fake_snap(_uow, _rid):
        return fake_snapshot, True

    monkeypatch.setattr("src.jobs.run_incremental_pipeline.get_last_watermark", fake_wm)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.fetch_changed_reviews", fake_fetch)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.start_pipeline_run", fake_start)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.complete_pipeline_run", fake_complete)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.load_full_review_snapshot", fake_snap)

    class _NopUOW:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None

    monkeypatch.setattr("src.jobs.run_incremental_pipeline.UnitOfWork", lambda _p: _NopUOW())

    def spy_process(**kwargs):
        captured["kg_mode"] = kwargs.get("kg_mode")
        raise _CapturedKgMode()  # short-circuit before pool.acquire()

    monkeypatch.setattr("src.jobs.run_incremental_pipeline.process_review", spy_process)
    monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "shadow")

    from src.jobs.run_incremental_pipeline import run_incremental
    with pytest.raises(_CapturedKgMode):
        await run_incremental(
            pool=None,  # type: ignore[arg-type]
            product_index=None,  # type: ignore[arg-type]
            product_masters={},
            concept_links={},
            bee_normalizer=None,  # type: ignore[arg-type]
            relation_canonicalizer=None,  # type: ignore[arg-type]
            projection_registry=None,  # type: ignore[arg-type]
            predicate_contracts={},
        )
    assert captured["kg_mode"] == "shadow"


# ---------------------------------------------------------------------------
# TC6: mock data smoke — env switches pipeline behavior (signal/quarantine vary)
# ---------------------------------------------------------------------------

def test_mock_pipeline_env_switches_behavior(monkeypatch) -> None:
    """Smoke-level proof that env is honored end-to-end through run_full_load.

    Strict equality assertions on counts would be brittle (depend on mock fixtures,
    KG internals, predicate data). We only assert env makes SOME observable
    pipeline-level difference; precise wiring is covered by spy-based TC2/TC3.
    """
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    def _run_with_env(value: str | None) -> tuple[int, int]:
        if value is None:
            monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
        else:
            monkeypatch.setenv("GRAPHRAPPING_KG_MODE", value)
        cfg = FullLoadConfig(
            review_json_path=str(MOCK_DIR / "review_triples_raw.json"),
            product_es_records=json.loads(
                (MOCK_DIR / "product_catalog_es.json").read_text(encoding="utf-8")
            ),
            user_profiles=json.loads(
                (MOCK_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8")
            ),
        )
        r = run_full_load(cfg)
        return r.signal_count, r.quarantine_count

    sig_off, q_off = _run_with_env("off")
    sig_on, q_on = _run_with_env("on")

    # At least one axis must differ — env is observable through the pipeline.
    assert (sig_off, q_off) != (sig_on, q_on), (
        f"env did not change pipeline output: off=({sig_off},{q_off}) "
        f"vs on=({sig_on},{q_on})"
    )
