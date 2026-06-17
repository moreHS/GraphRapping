"""
Sub-task 1B (P0-2) wiring tests.

Before this sub-task, configs/predicate_contracts.csv existed but was loaded
only by test_predicate_contracts.py. No operational entry point passed
predicate_contracts to process_review(), so CanonicalFactBuilder's contract
guard was always False in production wiring.

This test module verifies:
- The new load_predicate_contracts() helper.
- All four entry points (run_full_load, load_demo_data, run_incremental,
  run_batch) forward / self-load contracts.
- Real contract violations get routed to quarantine_projection_miss with
  PREDICATE_CONTRACT_VIOLATION marker (KG-on e2e).
- Mock baseline signal count is preserved (KG-off regression net).

This behavior is retained in the final 906-review baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.common.config_loader import load_predicate_contracts


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOCK_DIR = PROJECT_ROOT / "mockdata"


# ---------------------------------------------------------------------------
# TC1: helper returns dict + cached
# ---------------------------------------------------------------------------

def test_load_predicate_contracts_returns_dict_keyed_by_predicate() -> None:
    """Helper returns {predicate: row} dict and caches the result."""
    contracts = load_predicate_contracts()
    assert isinstance(contracts, dict)
    assert "used_by" in contracts
    assert contracts["used_by"]["allowed_subject_types"] == "ReviewerProxy"
    # Cached: subsequent call returns the same instance.
    assert load_predicate_contracts() is contracts


# ---------------------------------------------------------------------------
# TC2: run_full_load() forwards contracts to run_batch
# ---------------------------------------------------------------------------

def test_run_full_load_forwards_predicate_contracts(monkeypatch, tmp_path: Path) -> None:
    """run_full_load() must load CSV and pass non-empty dict to run_batch()."""
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["predicate_contracts"] = kwargs.get("predicate_contracts")
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

    # run_full_load imports run_batch at module scope from run_daily_pipeline.
    monkeypatch.setattr("src.jobs.run_full_load.run_batch", fake_run_batch)

    review_path = tmp_path / "empty.json"
    review_path.write_text("[]", encoding="utf-8")

    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    run_full_load(FullLoadConfig(
        review_json_path=str(review_path),
        product_es_records=[],
        user_profiles={},
    ))

    assert captured["predicate_contracts"] is not None
    assert "used_by" in captured["predicate_contracts"]


# ---------------------------------------------------------------------------
# TC3: load_demo_data() forwards contracts (state.py uses function-scope import)
# ---------------------------------------------------------------------------

def test_load_demo_data_forwards_predicate_contracts(monkeypatch, tmp_path: Path) -> None:
    """load_demo_data() must load CSV and pass dict to run_batch().

    state.py imports run_batch inside the function — patch the source module
    (src.jobs.run_daily_pipeline) instead of src.web.state.
    """
    captured: dict = {}

    def fake_run_batch(**kwargs):
        captured["predicate_contracts"] = kwargs.get("predicate_contracts")
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

    assert captured["predicate_contracts"] is not None
    assert "used_by" in captured["predicate_contracts"]


# ---------------------------------------------------------------------------
# TC4: run_incremental() self-loads when None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_incremental_self_loads_contracts_when_none(monkeypatch) -> None:
    """run_incremental(predicate_contracts=None) → load_predicate_contracts() fires."""
    call_count = {"n": 0}

    def spy_load():
        call_count["n"] += 1
        return {"used_by": {"allowed_subject_types": "ReviewerProxy"}}

    # Module-scope import in run_incremental_pipeline.py is mandatory for this patch
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.load_predicate_contracts", spy_load)

    async def fake_wm(_pool):
        return None, None

    async def fake_fetch(*_args, **_kwargs):
        return []

    async def fake_start(*_args, **_kwargs):
        return 1

    async def fake_complete(*_args, **_kwargs):
        return None

    monkeypatch.setattr("src.jobs.run_incremental_pipeline.get_last_watermark", fake_wm)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.fetch_changed_reviews", fake_fetch)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.start_pipeline_run", fake_start)
    monkeypatch.setattr("src.jobs.run_incremental_pipeline.complete_pipeline_run", fake_complete)

    from src.jobs.run_incremental_pipeline import run_incremental
    await run_incremental(
        pool=None,  # type: ignore[arg-type]
        product_index=None,  # type: ignore[arg-type]
        product_masters={},
        concept_links={},
        bee_normalizer=None,  # type: ignore[arg-type]
        relation_canonicalizer=None,  # type: ignore[arg-type]
        projection_registry=None,  # type: ignore[arg-type]
        predicate_contracts=None,
    )
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# TC5: KG-on e2e — load_demo_data() routes violations to quarantine
# ---------------------------------------------------------------------------

def test_load_demo_data_kg_on_routes_violations_to_quarantine() -> None:
    """KG-on path through load_demo_data() must produce PREDICATE_CONTRACT_VIOLATION
    entries in demo_state.quarantine_entries. v260605 refresh: mock fixture is
    now 906 reviews; test reads only the first 15 via max_reviews and asserts ≥1
    violation surfaces (exact count varies with subsampled subset).
    """
    from src.web.state import demo_state, load_demo_data

    load_demo_data(
        review_json_path=str(MOCK_DIR / "review_triples_raw.json"),
        product_es_records=json.loads(
            (MOCK_DIR / "product_catalog_es.json").read_text(encoding="utf-8")
        ),
        user_profiles=json.loads(
            (MOCK_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8")
        ),
        max_reviews=15,
        kg_mode="on",  # P0-3: explicit arg insulates against ambient env
    )

    violations = [
        e for e in demo_state.quarantine_entries
        if e.get("table") == "quarantine_projection_miss"
        and "PREDICATE_CONTRACT_VIOLATION" in (e.get("reason") or "")
    ]
    assert len(violations) >= 1, (
        f"Expected ≥1 PREDICATE_CONTRACT_VIOLATION entry from KG-on path; "
        f"got {len(violations)} of {len(demo_state.quarantine_entries)} total."
    )


# ---------------------------------------------------------------------------
# TC6: KG-off mock baseline — signal count holds
# ---------------------------------------------------------------------------

def test_kg_off_signal_count_baseline_holds() -> None:
    """KG-off baseline: mock 906 reviews (v260605 refresh) → ≥2520 signals.

    Floor = measured value (2801) × 0.9. If contracts ever start silently
    rejecting valid facts, signals would drop below the floor.
    """
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    config = FullLoadConfig(
        review_json_path=str(MOCK_DIR / "review_triples_raw.json"),
        product_es_records=json.loads(
            (MOCK_DIR / "product_catalog_es.json").read_text(encoding="utf-8")
        ),
        user_profiles=json.loads(
            (MOCK_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8")
        ),
        kg_mode="off",  # P0-3: explicit arg insulates against ambient env
    )
    result = run_full_load(config)
    assert result.signal_count >= 2520, (
        f"KG-off signal count regressed: {result.signal_count} < 2520 floor."
    )


# ---------------------------------------------------------------------------
# TC7: run_batch(predicate_contracts=None) self-loads
# ---------------------------------------------------------------------------

def test_run_batch_self_loads_contracts_when_none(monkeypatch) -> None:
    """run_batch with predicate_contracts=None must call load_predicate_contracts()
    via the module-scope import in run_daily_pipeline.py."""
    call_count = {"n": 0}

    def spy_load():
        call_count["n"] += 1
        return {}

    monkeypatch.setattr("src.jobs.run_daily_pipeline.load_predicate_contracts", spy_load)

    from src.jobs.run_daily_pipeline import run_batch
    from src.link.product_matcher import ProductIndex

    # reviews=[] short-circuits the loop; no normalizers needed.
    run_batch(
        reviews=[],
        source="test",
        product_index=ProductIndex.build([]),
        product_masters={},
        concept_links={},
        user_masters={},
        user_adapted_facts={},
        bee_normalizer=None,  # type: ignore[arg-type]
        relation_canonicalizer=None,  # type: ignore[arg-type]
        projection_registry=None,  # type: ignore[arg-type]
        quarantine=None,  # type: ignore[arg-type]
    )
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# TC8: build_review_persist_bundle(predicate_contracts=None) self-loads
# ---------------------------------------------------------------------------

def test_build_review_persist_bundle_self_loads_contracts_when_none(monkeypatch) -> None:
    """build_review_persist_bundle with predicate_contracts=None must self-load
    contracts AND forward them so the underlying CanonicalFactBuilder receives them.

    Strengthened (Codex code review #2): verify the dict actually reaches the
    builder by spying on CanonicalFactBuilder.__init__.
    """
    spy_load_count = {"n": 0}
    builder_contracts_seen: list[dict | None] = []

    sentinel_contracts = {"_marker": {"sentinel": "yes"}}

    def spy_load():
        spy_load_count["n"] += 1
        return sentinel_contracts

    monkeypatch.setattr("src.jobs.run_daily_pipeline.load_predicate_contracts", spy_load)

    from src.canonical import canonical_fact_builder as cfb_module
    orig_init = cfb_module.CanonicalFactBuilder.__init__

    def spy_init(self, predicate_contracts=None):
        builder_contracts_seen.append(predicate_contracts)
        orig_init(self, predicate_contracts=predicate_contracts)

    monkeypatch.setattr(cfb_module.CanonicalFactBuilder, "__init__", spy_init)

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

    # Also spy on process_review to verify build_review_persist_bundle FORWARDS
    # contracts rather than relying on process_review's own self-load fallback.
    process_review_calls: list[dict | None] = []
    import src.jobs.run_daily_pipeline as daily
    orig_process = daily.process_review

    def spy_process(**kwargs):
        process_review_calls.append(kwargs.get("predicate_contracts"))
        return orig_process(**kwargs)

    monkeypatch.setattr(daily, "process_review", spy_process)

    build_review_persist_bundle(
        record=RawReviewRecord(brnd_nm="b", prod_nm="p", text="t"),
        source="test",
        product_index=ProductIndex.build([]),
        bee_normalizer=bee,
        relation_canonicalizer=rel,
        projection_registry=reg,
    )
    assert spy_load_count["n"] >= 1, "load_predicate_contracts must self-load"
    # At least one builder instance must have received the sentinel dict.
    assert any(c is sentinel_contracts for c in builder_contracts_seen), (
        "self-loaded contracts did not reach CanonicalFactBuilder"
    )
    # build_review_persist_bundle must FORWARD the sentinel to process_review
    # (not rely on process_review's own fallback re-load).
    assert process_review_calls, "process_review must be called"
    assert process_review_calls[0] is sentinel_contracts, (
        "build_review_persist_bundle should forward the self-loaded contracts to "
        "process_review, not let process_review re-load them"
    )


# ---------------------------------------------------------------------------
# TC bonus: real contracts catch a Category → BEEAttr violation
# (kept for symmetry with the existing builder-only test, but uses live CSV)
# ---------------------------------------------------------------------------

def test_load_predicate_contracts_fails_closed_on_missing_required_columns(
    monkeypatch,
) -> None:
    """Codex code review fix: missing required header columns must raise,
    not silently disable validation."""
    import src.common.config_loader as cl

    # Reset cache so the loader actually runs
    monkeypatch.setattr(cl, "_predicate_contracts", None)

    bad_rows = [{"predicate": "used_by", "allowed_subject_types": "ReviewerProxy"}]
    monkeypatch.setattr(cl, "load_csv", lambda _filename: bad_rows)

    with pytest.raises(ValueError, match="allowed_object_types"):
        cl.load_predicate_contracts()


def test_load_predicate_contracts_fails_closed_on_empty_csv(monkeypatch) -> None:
    """Empty or header-only CSV must raise — otherwise all validation is silently
    disabled."""
    import src.common.config_loader as cl

    monkeypatch.setattr(cl, "_predicate_contracts", None)
    monkeypatch.setattr(cl, "load_csv", lambda _filename: [])

    with pytest.raises(ValueError, match="empty or header-only"):
        cl.load_predicate_contracts()


def test_load_predicate_contracts_fails_closed_on_blank_predicate(monkeypatch) -> None:
    """Blank predicate cell must raise — ambiguous contract."""
    import src.common.config_loader as cl

    monkeypatch.setattr(cl, "_predicate_contracts", None)
    monkeypatch.setattr(cl, "load_csv", lambda _filename: [
        {"predicate": "", "allowed_subject_types": "X", "allowed_object_types": "Y"},
    ])

    with pytest.raises(ValueError, match="blank predicate"):
        cl.load_predicate_contracts()


def test_load_predicate_contracts_fails_closed_on_blank_type_cells_non_special(
    monkeypatch,
) -> None:
    """Blank allowed_subject/object_types are only permitted for preprocess-only
    or drop predicates. Any other blank must raise."""
    import src.common.config_loader as cl

    monkeypatch.setattr(cl, "_predicate_contracts", None)
    monkeypatch.setattr(cl, "load_csv", lambda _filename: [
        # 'used_by' is a normal predicate — blank cells must NOT be allowed.
        {"predicate": "used_by", "allowed_subject_types": "", "allowed_object_types": ""},
    ])

    with pytest.raises(ValueError, match="blank allowed_subject_types"):
        cl.load_predicate_contracts()


def test_load_predicate_contracts_allows_blank_for_preprocess_only_predicates(
    monkeypatch,
) -> None:
    """same_entity / no_relationship are intentionally blank — must load OK."""
    import src.common.config_loader as cl

    monkeypatch.setattr(cl, "_predicate_contracts", None)
    monkeypatch.setattr(cl, "load_csv", lambda _filename: [
        {"predicate": "used_by", "allowed_subject_types": "ReviewerProxy",
         "allowed_object_types": "Product"},
        {"predicate": "same_entity", "allowed_subject_types": "",
         "allowed_object_types": ""},
        {"predicate": "no_relationship", "allowed_subject_types": "",
         "allowed_object_types": ""},
    ])

    contracts = cl.load_predicate_contracts()
    assert "used_by" in contracts
    assert "same_entity" in contracts
    assert "no_relationship" in contracts


def test_load_predicate_contracts_fails_closed_on_duplicate_predicate(
    monkeypatch,
) -> None:
    """Duplicate predicate row must raise — otherwise second silently shadows first."""
    import src.common.config_loader as cl

    monkeypatch.setattr(cl, "_predicate_contracts", None)
    dup_rows = [
        {"predicate": "used_by", "allowed_subject_types": "ReviewerProxy",
         "allowed_object_types": "Product"},
        {"predicate": "used_by", "allowed_subject_types": "Other",
         "allowed_object_types": "Product"},
    ]
    monkeypatch.setattr(cl, "load_csv", lambda _filename: dup_rows)

    with pytest.raises(ValueError, match="duplicate predicate"):
        cl.load_predicate_contracts()


def test_real_contracts_reject_category_has_attribute_bee_attr() -> None:
    """Live CSV contracts must reject the same Category/Ingredient → BEEAttr
    pattern that KG-on path produces on mock data (§CSV measurement)."""
    contracts = load_predicate_contracts()
    builder = CanonicalFactBuilder(predicate_contracts=contracts)

    result = builder.add_fact(
        review_id="rv1",
        subject_iri="concept:Category:cat1",
        predicate="has_attribute",
        object_iri="concept:BEEAttr:moisture",
        subject_type="Category",
        object_type="BEEAttr",
    )
    assert result is None
    assert len(builder.invalid_facts) == 1
    assert "subject_type" in builder.invalid_facts[0]["reason"]
