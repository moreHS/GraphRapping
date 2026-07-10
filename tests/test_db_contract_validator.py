"""
Wave 4 Task 3: DB contract validator.

Unit-level tests use monkeypatched asyncpg helpers, so they verify the
status-decision logic without a real DB. Behavioural PG coverage lives in
`test_postgres_integration.py` alongside other DB-bound tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.db import contract_validator
from src.db.contract_validator import (
    ContractCheck,
    ContractStatus,
    ContractValidationResult,
    _max_status,
    validate_data,
    validate_schema,
)


# ---------------------------------------------------------------------------
# Status precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("a, b, winner", [
    (ContractStatus.OK, ContractStatus.OK, ContractStatus.OK),
    (ContractStatus.OK, ContractStatus.EMPTY, ContractStatus.EMPTY),
    (ContractStatus.EMPTY, ContractStatus.OK, ContractStatus.EMPTY),
    (ContractStatus.EMPTY, ContractStatus.INVALID, ContractStatus.INVALID),
    (ContractStatus.INVALID, ContractStatus.OK, ContractStatus.INVALID),
    (ContractStatus.INVALID, ContractStatus.EMPTY, ContractStatus.INVALID),
])
def test_max_status_picks_worst(a, b, winner) -> None:
    assert _max_status(a, b) == winner


# ---------------------------------------------------------------------------
# Dataclass shape contract
# ---------------------------------------------------------------------------


def test_contract_status_enum_values() -> None:
    assert ContractStatus.OK.value == "OK"
    assert ContractStatus.EMPTY.value == "EMPTY"
    assert ContractStatus.INVALID.value == "INVALID"


def test_contract_check_is_frozen() -> None:
    chk = ContractCheck(name="x", status=ContractStatus.OK, message="ok")
    with pytest.raises(Exception):
        chk.name = "y"  # type: ignore[misc]


def test_contract_validation_result_default_counts_is_empty_dict() -> None:
    r = ContractValidationResult(status=ContractStatus.OK, checks=())
    assert dict(r.counts) == {}


# ---------------------------------------------------------------------------
# validate_schema — uses _get_table_columns monkeypatch
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_schema(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str] | None]:
    """Replace _get_table_columns with a dict-backed stub.
    Test sets `stub[table] = {cols}` or `stub[table] = None` (missing)."""
    stub: dict[str, set[str] | None] = {}

    async def _fake(pool: Any, table_name: str) -> set[str] | None:
        return stub.get(table_name)

    monkeypatch.setattr(contract_validator, "_get_table_columns", _fake)
    return stub


@pytest.mark.asyncio
async def test_validate_schema_ok_when_all_tables_present(
    _stub_schema: dict[str, set[str] | None],
) -> None:
    # Stub every required table with all required cols
    for table, cols in contract_validator._REQUIRED_TABLES.items():
        _stub_schema[table] = set(cols) | {"updated_at"}  # extra cols OK

    result = await validate_schema(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.OK
    assert all(c.status == ContractStatus.OK for c in result.checks)


@pytest.mark.asyncio
async def test_validate_schema_invalid_when_table_missing(
    _stub_schema: dict[str, set[str] | None],
) -> None:
    for table, cols in contract_validator._REQUIRED_TABLES.items():
        _stub_schema[table] = set(cols)
    _stub_schema["product_master"] = None  # missing

    result = await validate_schema(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    bad = [c for c in result.checks if c.status == ContractStatus.INVALID]
    assert any("product_master" in c.message and "missing" in c.message for c in bad)


@pytest.mark.asyncio
async def test_validate_schema_invalid_when_column_missing(
    _stub_schema: dict[str, set[str] | None],
) -> None:
    for table, cols in contract_validator._REQUIRED_TABLES.items():
        _stub_schema[table] = set(cols)
    _stub_schema["agg_product_signal"] = (
        contract_validator._REQUIRED_TABLES["agg_product_signal"] - {"is_promoted"}
    )

    result = await validate_schema(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    assert any(
        "agg_product_signal" in c.message and "is_promoted" in c.message
        for c in result.checks if c.status == ContractStatus.INVALID
    )


# ---------------------------------------------------------------------------
# validate_data — uses count helper monkeypatches
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_data(monkeypatch: pytest.MonkeyPatch):
    """Replace count helpers with controllable stubs."""

    state = {
        "active_products": 0,
        "active_users": 0,
        "concepts": 0,
        "promoted_in_window": 0,
        "promotion_violations": 0,
        "stale": {"product_signals": 0, "user_preferences": 0},
        "mismatches": {
            "purchase_event_raw": 0,
            "agg_product_signal": 0,
            "serving_product_profile": 0,
        },
        "source_grounding": {
            "source_identity": 0,
            "promo_prefix_brand": 0,
            "source_stats_shape": 0,
        },
        "source_review_stats": {
            "positive_6m": 0,
            "avg_6m": 0,
        },
        # Collision helpers query the pool directly (no null-pool no-op), so
        # they must be stubbed here for pool=None unit tests to stay DB-free.
        "collision": {
            "unmarked_structural": 0,
            "unmarked_shared_source_id": 0,
            "marked": 0,
        },
        "clean_join_leaks": 0,
    }

    async def _active(pool: Any, table: str) -> int:
        return state["active_products"] if table == "product_master" else state["active_users"]

    async def _concepts(pool: Any) -> int:
        return state["concepts"]

    async def _promoted(pool: Any, window: str) -> int:
        return state["promoted_in_window"]

    async def _promo_viol(pool: Any) -> int:
        return state["promotion_violations"]

    async def _stale(pool: Any, days: int) -> dict[str, int]:
        return state["stale"]

    async def _mismatches(pool: Any) -> dict[str, int]:
        return state["mismatches"]

    async def _source_grounding(pool: Any) -> dict[str, int]:
        return state["source_grounding"]

    async def _source_stats(pool: Any) -> dict[str, int]:
        return state["source_review_stats"]

    async def _collision(pool: Any) -> dict[str, int]:
        return state["collision"]

    async def _clean_join(pool: Any) -> int:
        return state["clean_join_leaks"]

    monkeypatch.setattr(contract_validator, "_count_active_rows", _active)
    monkeypatch.setattr(contract_validator, "_count_concepts", _concepts)
    monkeypatch.setattr(contract_validator, "_count_promoted_signals_in_window", _promoted)
    monkeypatch.setattr(contract_validator, "_count_promotion_invariant_violations", _promo_viol)
    monkeypatch.setattr(contract_validator, "_count_stale_active_violations", _stale)
    monkeypatch.setattr(contract_validator, "_count_product_id_mismatches", _mismatches)
    monkeypatch.setattr(contract_validator, "_count_source_grounding_violations", _source_grounding)
    monkeypatch.setattr(contract_validator, "_count_source_review_stats_readiness", _source_stats)
    monkeypatch.setattr(
        contract_validator, "_count_source_identity_collision_violations", _collision
    )
    monkeypatch.setattr(contract_validator, "_count_collision_clean_join_leaks", _clean_join)
    return state


@pytest.mark.asyncio
async def test_validate_data_ok_when_minimums_met_and_no_violations(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["active_products"] = 10
    _stub_data["active_users"] = 5
    _stub_data["concepts"] = 100
    _stub_data["promoted_in_window"] = 47

    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        expected_min_active_products=1,
        expected_min_active_users=1,
        expected_min_concepts=1,
        expected_min_promoted_signals=1,
    )
    assert result.status == ContractStatus.OK
    assert result.counts["active_products"] == 10


@pytest.mark.asyncio
async def test_validate_data_empty_when_minimums_unmet(
    _stub_data: dict[str, Any],
) -> None:
    # Fresh DB, no data yet
    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        expected_min_active_products=1,
        expected_min_active_users=1,
    )
    assert result.status == ContractStatus.EMPTY
    empty_checks = [c for c in result.checks if c.status == ContractStatus.EMPTY]
    assert len(empty_checks) >= 2  # active_products + active_users


@pytest.mark.asyncio
async def test_validate_data_empty_when_source_review_stats_minimums_unmet(
    _stub_data: dict[str, Any],
) -> None:
    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        expected_min_source_review_count_6m=1,
        expected_min_source_avg_rating_6m=1,
    )

    assert result.status == ContractStatus.EMPTY
    assert result.counts["source_review_stats.positive_6m"] == 0
    assert result.counts["source_review_stats.avg_rating_6m"] == 0
    assert any(c.name == "data.source_review_stats.positive_6m" for c in result.checks)


@pytest.mark.asyncio
async def test_validate_data_ok_when_no_minimums_set_and_no_data(
    _stub_data: dict[str, Any],
) -> None:
    """Caller can pass expected_min=0 to skip count assertions."""
    result = await validate_data(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.OK


@pytest.mark.asyncio
async def test_validate_data_invalid_on_promotion_violation(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["promotion_violations"] = 3
    result = await validate_data(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    promo_check = next(c for c in result.checks if c.name == "invariant.promotion_gate")
    assert promo_check.status == ContractStatus.INVALID
    assert promo_check.actual == 3
    assert "Re-run aggregate_product_signals" in promo_check.message


@pytest.mark.asyncio
async def test_validate_data_invalid_on_stale_active_product_signals(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["stale"]["product_signals"] = 5
    result = await validate_data(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    stale_check = next(
        c for c in result.checks
        if c.name == "invariant.stale_active.agg_product_signal"
    )
    assert stale_check.status == ContractStatus.INVALID
    assert "mark_stale_agg_signals_inactive" in stale_check.message


@pytest.mark.asyncio
async def test_validate_data_invalid_on_stale_active_user_preferences(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["stale"]["user_preferences"] = 7
    result = await validate_data(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    assert any(
        c.name == "invariant.stale_active.agg_user_preference"
        and c.status == ContractStatus.INVALID
        for c in result.checks
    )


@pytest.mark.asyncio
async def test_validate_data_skips_stale_check_when_policy_disabled(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["stale"]["product_signals"] = 999
    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        enforce_stale_policy=False,
    )
    assert result.status == ContractStatus.OK


@pytest.mark.asyncio
async def test_validate_data_invalid_on_product_id_mismatch(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["mismatches"]["serving_product_profile"] = 2
    result = await validate_data(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    check = next(c for c in result.checks if c.name == "invariant.product_id_consistency")
    assert check.status == ContractStatus.INVALID
    assert "serving_product_profile" in check.message


@pytest.mark.asyncio
async def test_validate_data_skips_source_grounding_by_default(
    _stub_data: dict[str, Any],
) -> None:
    """Generic fixture DBs do not run production source-truth checks."""
    _stub_data["source_grounding"]["source_identity"] = 1
    _stub_data["source_grounding"]["promo_prefix_brand"] = 1

    result = await validate_data(pool=None)  # type: ignore[arg-type]

    assert result.status == ContractStatus.OK
    assert "source_grounding.source_identity" not in result.counts


@pytest.mark.asyncio
async def test_validate_data_invalid_on_source_identity_mismatch_in_production_mode(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["source_grounding"]["source_identity"] = 2

    result = await validate_data(  # type: ignore[arg-type]
        pool=None,
        enforce_source_grounding=True,
    )

    assert result.status == ContractStatus.INVALID
    check = next(c for c in result.checks if c.name == "invariant.source_grounding")
    assert check.status == ContractStatus.INVALID
    assert check.actual == 2
    assert result.counts["source_grounding.source_identity"] == 2


@pytest.mark.asyncio
async def test_validate_data_invalid_on_source_backed_promo_prefix_brand(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["source_grounding"]["promo_prefix_brand"] = 1

    result = await validate_data(  # type: ignore[arg-type]
        pool=None,
        enforce_source_grounding=True,
    )

    assert result.status == ContractStatus.INVALID
    check = next(c for c in result.checks if c.name == "invariant.source_grounding")
    assert check.status == ContractStatus.INVALID
    assert "promo-prefix brand" in check.message


@pytest.mark.asyncio
async def test_validate_data_invalid_on_source_stats_shape_in_production_mode(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["source_grounding"]["source_stats_shape"] = 1

    result = await validate_data(  # type: ignore[arg-type]
        pool=None,
        enforce_source_grounding=True,
    )

    assert result.status == ContractStatus.INVALID
    check = next(c for c in result.checks if c.name == "invariant.source_grounding")
    assert check.status == ContractStatus.INVALID
    assert "source stats shape" in check.message
    assert result.counts["source_grounding.source_stats_shape"] == 1


@pytest.mark.asyncio
async def test_validate_data_signal_window_count_per_window(
    _stub_data: dict[str, Any],
) -> None:
    _stub_data["promoted_in_window"] = 47
    result = await validate_data(
        pool=None,  # type: ignore[arg-type]
        signal_window="30d",
        expected_min_promoted_signals=1,
    )
    assert "promoted_signals.30d" in result.counts


# ---------------------------------------------------------------------------
# Contract / shape checks
# ---------------------------------------------------------------------------


def test_required_tables_covers_consumer_contract() -> None:
    """All consumer-facing tables documented in plan are in _REQUIRED_TABLES."""
    must_include = {
        "product_master",
        "product_review_stats",
        "user_master",
        "purchase_event_raw",
        "wrapped_signal",
        "signal_evidence",
        "agg_product_signal",
        "agg_user_preference",
        "serving_product_profile",
        "serving_user_profile",
        "concept_registry",
        "schema_migrations",
    }
    assert must_include.issubset(set(contract_validator._REQUIRED_TABLES.keys()))


def test_agg_user_preference_requires_confidence_column() -> None:
    """Codex 2nd review: agg_user_preference.confidence is consumer-facing."""
    assert "confidence" in contract_validator._REQUIRED_TABLES["agg_user_preference"]


def test_product_master_requires_source_truth_recency_column() -> None:
    assert "source_truth_updated_at" in contract_validator._REQUIRED_TABLES["product_master"]


def test_serving_profile_required_columns_match_schema_module() -> None:
    """Codex 2nd review: serving_*_profile must require every column in the
    serving_profile_schema single-source-of-truth, plus meta."""
    from src.mart.serving_profile_schema import (
        SERVING_PRODUCT_PROFILE_COLUMNS,
        SERVING_USER_PROFILE_COLUMNS,
    )
    expected_product = set(SERVING_PRODUCT_PROFILE_COLUMNS) | {"is_active", "updated_at"}
    expected_user = set(SERVING_USER_PROFILE_COLUMNS) | {"is_active", "updated_at"}
    assert contract_validator._REQUIRED_TABLES["serving_product_profile"] == expected_product
    assert contract_validator._REQUIRED_TABLES["serving_user_profile"] == expected_user


@pytest.mark.parametrize("null_field", ["distinct_review_count", "avg_confidence", "synthetic_ratio"])
def test_promotion_violation_treats_null_metrics_as_failure(null_field: str) -> None:
    """Codex 2nd review: a promoted row with NULL gate metric must be a
    violation (consumer cannot prove gate compliance from NULL)."""
    row = {
        "window_type": "all",
        "distinct_review_count": 5,
        "avg_confidence": 0.8,
        "synthetic_ratio": 0.0,
    }
    row[null_field] = None  # type: ignore[assignment]
    assert contract_validator._is_promotion_violation(row) is True


def test_promotion_violation_passes_when_all_metrics_above_threshold() -> None:
    row = {
        "window_type": "all",
        "distinct_review_count": 5,
        "avg_confidence": 0.8,
        "synthetic_ratio": 0.0,
    }
    assert contract_validator._is_promotion_violation(row) is False


@pytest.mark.parametrize("window, dist, expected_violation", [
    ("30d", 1, True),    # 30d needs >=2
    ("30d", 2, False),
    ("90d", 2, True),    # 90d needs >=3
    ("90d", 3, False),
    ("all", 2, True),
    ("all", 3, False),
    ("unknown", 2, True),  # fallback >=3
    ("unknown", 3, False),
])
def test_promotion_violation_window_threshold(
    window: str, dist: int, expected_violation: bool,
) -> None:
    row = {
        "window_type": window,
        "distinct_review_count": dist,
        "avg_confidence": 0.8,
        "synthetic_ratio": 0.0,
    }
    assert contract_validator._is_promotion_violation(row) is expected_violation


def test_promotion_min_reviews_matches_wave2_thresholds() -> None:
    """Wave 2.8 window-aware thresholds: 30d>=2, 90d>=3, all>=3."""
    assert contract_validator._PROMOTION_MIN_REVIEWS["30d"] == 2
    assert contract_validator._PROMOTION_MIN_REVIEWS["90d"] == 3
    assert contract_validator._PROMOTION_MIN_REVIEWS["all"] == 3
    assert contract_validator._PROMOTION_DEFAULT_MIN_REVIEWS == 3


@pytest.mark.asyncio
async def test_validate_all_short_circuits_on_schema_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If schema is INVALID, validate_all does NOT run data checks (which
    would error against missing tables)."""
    data_called = {"called": False}

    async def _fake_schema(pool: Any) -> ContractValidationResult:
        return ContractValidationResult(
            status=ContractStatus.INVALID,
            checks=(ContractCheck(name="schema.x", status=ContractStatus.INVALID, message="missing"),),
        )

    async def _fake_data(*a, **kw) -> ContractValidationResult:
        data_called["called"] = True
        return ContractValidationResult(status=ContractStatus.OK, checks=())

    monkeypatch.setattr(contract_validator, "validate_schema", _fake_schema)
    monkeypatch.setattr(contract_validator, "validate_data", _fake_data)

    result = await contract_validator.validate_all(pool=None)  # type: ignore[arg-type]
    assert result.status == ContractStatus.INVALID
    assert data_called["called"] is False
