"""
Wave 4 Task 3: DB contract validator for downstream consumers.

Answers the question: "Is this GraphRapping DB ready for a downstream
consumer to read from?" Splits into:

- `validate_schema(pool)`: schema validity only (tables/columns/migrations).
  Returns INVALID only if the DDL contract is broken.

- `validate_data(pool, ...)`: data readiness against caller-provided expected
  minimums. Returns OK / EMPTY / INVALID. EMPTY means "schema is fine but
  expected minimums are not met yet" — typically a freshly-migrated DB before
  the first load.

Output is a typed `ContractValidationResult` (frozen dataclass) so consumers
can introspect each individual `ContractCheck`.

Invariants validated (when applicable, i.e. when data exists):
- `agg_product_signal.is_promoted=true` rows must meet the corpus-promotion
  gate (distinct_review_count ≥ 2 for 30d, ≥ 3 for 90d/all/unknown windows;
  avg_confidence ≥ 0.6; synthetic_ratio ≤ 0.5).
- When `enforce_stale_policy=True`, no `is_active=true` aggregate row may
  have its freshness timestamp older than `stale_threshold_days`.
  `agg_product_signal` uses `last_seen_at`; `agg_user_preference` uses
  `updated_at` (no last_seen_at column).
- Product ID consistency: every product_id appearing in
  `purchase_event_raw`, `agg_product_signal`, or `serving_product_profile`
  must also exist in `product_master`.
- Optional production source-grounding: serving rows with explicit source
  identity must match `product_master.product_id`, and source-backed rows must
  not expose promo-prefix brands as product truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

import asyncpg

from src.mart.serving_profile_schema import (
    SERVING_PRODUCT_PROFILE_COLUMNS,
    SERVING_USER_PROFILE_COLUMNS,
)


class ContractStatus(str, Enum):
    OK = "OK"
    EMPTY = "EMPTY"
    INVALID = "INVALID"


def _max_status(a: ContractStatus, b: ContractStatus) -> ContractStatus:
    """INVALID > EMPTY > OK ordering (the worse one wins)."""
    order = {ContractStatus.OK: 0, ContractStatus.EMPTY: 1, ContractStatus.INVALID: 2}
    return a if order[a] >= order[b] else b


@dataclass(frozen=True)
class ContractCheck:
    name: str
    status: ContractStatus
    message: str
    actual: int | None = None
    expected_min: int | None = None


@dataclass(frozen=True)
class ContractValidationResult:
    status: ContractStatus
    checks: tuple[ContractCheck, ...]
    counts: Mapping[str, int] = field(default_factory=dict)


# Tables and columns required by downstream consumers. Validated structurally
# by `validate_schema`.
_REQUIRED_TABLES: dict[str, set[str]] = {
    "product_master": {
        "product_id", "product_name", "brand_id", "brand_name",
        "category_id", "category_name", "source_product_id",
        "source_channel", "source_key_type", "representative_product_name",
        "source_truth_source", "source_truth_quality", "source_review_count",
        "source_truth_updated_at", "source_review_score", "is_active",
    },
    "product_review_stats": {
        "product_id", "source_channel", "source_key_type",
        "source_review_count_6m", "source_review_score_count_6m",
        "source_avg_rating_6m", "source_review_min_date_6m",
        "source_review_max_date_6m", "source_review_count_all",
        "source_review_score_count_all", "source_avg_rating_all",
        "source_review_min_date_all", "source_review_max_date_all",
        "source", "updated_at",
    },
    "user_master": {"user_id", "is_active"},
    "purchase_event_raw": {
        "purchase_event_id", "user_id", "product_id", "purchased_at", "channel",
    },
    "wrapped_signal": {
        "signal_id", "target_product_id", "edge_type", "dst_id",
        "review_id", "source_confidence",
    },
    "signal_evidence": {"signal_id", "fact_id", "evidence_rank"},
    "agg_product_signal": {
        "target_product_id", "canonical_edge_type", "dst_node_id",
        "window_type", "distinct_review_count", "avg_confidence",
        "synthetic_ratio", "is_promoted", "is_active", "last_seen_at",
    },
    "agg_user_preference": {
        "user_id", "preference_edge_type", "dst_node_id",
        "weight", "confidence", "is_active", "updated_at",
    },
    # Wave 4 Task 3 (2nd review): serving profiles' consumer-facing columns
    # are the single source of truth in src/mart/serving_profile_schema.py.
    # All listed columns plus the two meta columns must exist for consumer
    # reads to succeed.
    "serving_product_profile": (
        set(SERVING_PRODUCT_PROFILE_COLUMNS) | {"is_active", "updated_at"}
    ),
    "serving_user_profile": (
        set(SERVING_USER_PROFILE_COLUMNS) | {"is_active", "updated_at"}
    ),
    "concept_registry": {"concept_id", "concept_type", "canonical_name"},
    "schema_migrations": {"version", "applied_at"},
}


async def _get_table_columns(pool: asyncpg.Pool, table_name: str) -> set[str] | None:
    """Returns the column name set for a table, or None if the table does not exist."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = $1
              AND table_schema = ANY(current_schemas(false))
            """,
            table_name,
        )
    if not rows:
        return None
    return {r["column_name"] for r in rows}


async def validate_schema(pool: asyncpg.Pool) -> ContractValidationResult:
    """Validate required tables exist with required columns.

    Returns INVALID if any required table is missing or any required column
    is absent; otherwise OK. Empty tables are still OK at the schema level.
    """
    checks: list[ContractCheck] = []
    overall = ContractStatus.OK

    for table, required_cols in _REQUIRED_TABLES.items():
        actual_cols = await _get_table_columns(pool, table)
        if actual_cols is None:
            checks.append(ContractCheck(
                name=f"schema.{table}",
                status=ContractStatus.INVALID,
                message=f"required table '{table}' is missing",
            ))
            overall = ContractStatus.INVALID
            continue

        missing = required_cols - actual_cols
        if missing:
            checks.append(ContractCheck(
                name=f"schema.{table}",
                status=ContractStatus.INVALID,
                message=f"table '{table}' missing required columns: {sorted(missing)}",
            ))
            overall = ContractStatus.INVALID
        else:
            checks.append(ContractCheck(
                name=f"schema.{table}",
                status=ContractStatus.OK,
                message=f"table '{table}' OK ({len(actual_cols)} cols)",
            ))

    return ContractValidationResult(status=overall, checks=tuple(checks))


# Window → minimum distinct_review_count for is_corpus_promoted (Wave 2.8).
_PROMOTION_MIN_REVIEWS: dict[str, int] = {
    "30d": 2,
    "90d": 3,
    "all": 3,
}
# Unknown windows fall back to the strictest bar.
_PROMOTION_DEFAULT_MIN_REVIEWS = 3


def _is_promotion_violation(row: Mapping[str, object]) -> bool:
    """A promoted row violates the gate if any required metric is NULL or
    falls outside the Wave 2.8 thresholds.

    NULL metric -> violation (consumer cannot prove gate compliance).
    """
    distinct = row["distinct_review_count"]
    avg_conf = row["avg_confidence"]
    syn_ratio = row["synthetic_ratio"]
    if distinct is None or avg_conf is None or syn_ratio is None:
        return True
    window = str(row["window_type"]) if row["window_type"] is not None else ""
    min_reviews = _PROMOTION_MIN_REVIEWS.get(window, _PROMOTION_DEFAULT_MIN_REVIEWS)
    # asyncpg rows hand back numeric columns as int/float; the Mapping[str, object]
    # annotation just keeps the helper monkeypatch-friendly.
    return (
        int(distinct) < min_reviews  # type: ignore[call-overload]
        or float(avg_conf) < 0.6  # type: ignore[arg-type]
        or float(syn_ratio) > 0.5  # type: ignore[arg-type]
    )


async def _count_promotion_invariant_violations(pool: asyncpg.Pool) -> int:
    """Count agg_product_signal rows where is_promoted=true but gate predicate fails."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT window_type, distinct_review_count, avg_confidence, synthetic_ratio
            FROM agg_product_signal
            WHERE is_promoted = true
            """,
        )
    return sum(1 for r in rows if _is_promotion_violation(r))


async def _count_stale_active_violations(pool: asyncpg.Pool, threshold_days: int) -> dict[str, int]:
    """Count active aggregate rows past the freshness threshold."""
    async with pool.acquire() as conn:
        product_count = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM agg_product_signal
            WHERE is_active = true
              AND last_seen_at IS NOT NULL
              AND last_seen_at < now() - INTERVAL '{threshold_days} days'
            """
        )
        # agg_user_preference has no last_seen_at — use updated_at.
        user_count = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM agg_user_preference
            WHERE is_active = true
              AND updated_at < now() - INTERVAL '{threshold_days} days'
            """
        )
    return {"product_signals": product_count or 0, "user_preferences": user_count or 0}


async def _count_product_id_mismatches(pool: asyncpg.Pool) -> dict[str, int]:
    """For each child table, count product_ids absent from product_master."""
    async with pool.acquire() as conn:
        purchase = await conn.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT product_id FROM purchase_event_raw WHERE product_id IS NOT NULL
                EXCEPT
                SELECT product_id FROM product_master
            ) AS missing
            """
        )
        agg = await conn.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT target_product_id FROM agg_product_signal
                EXCEPT
                SELECT product_id FROM product_master
            ) AS missing
            """
        )
        serving = await conn.fetchval(
            """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT product_id FROM serving_product_profile
                EXCEPT
                SELECT product_id FROM product_master
            ) AS missing
            """
        )
    return {
        "purchase_event_raw": purchase or 0,
        "agg_product_signal": agg or 0,
        "serving_product_profile": serving or 0,
    }


async def _count_source_grounding_violations(pool: asyncpg.Pool) -> dict[str, int]:
    """Count precise production-readiness violations for source truth.

    `source_product_id` may be NULL in generic fixtures, so missing values only
    fail when source review stats exist. A non-NULL, different source_product_id
    is always a mismatch under the current one-source contract.
    """
    async with pool.acquire() as conn:
        identity = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM serving_product_profile spp
            JOIN product_master pm USING(product_id)
            WHERE pm.is_active = true
              AND spp.is_active = true
              AND (
                  (
                      spp.source_product_id IS NOT NULL
                      AND spp.source_product_id <> pm.product_id
                  )
                  OR (
                      NULLIF(BTRIM(spp.source_review_stats_source), '') IS NOT NULL
                      AND COALESCE(spp.source_product_id, '') <> pm.product_id
                  )
              )
            """
        )
        promo_brand = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM serving_product_profile spp
            JOIN product_master pm USING(product_id)
            WHERE pm.is_active = true
              AND spp.is_active = true
              AND NULLIF(BTRIM(spp.source_review_stats_source), '') IS NOT NULL
              AND (
                  pm.brand_name LIKE '【%'
                  OR pm.brand_name LIKE '[%'
              )
            """
        )
    return {"source_identity": identity or 0, "promo_prefix_brand": promo_brand or 0}


async def _count_active_rows(pool: asyncpg.Pool, table: str) -> int:
    async with pool.acquire() as conn:
        v = await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE is_active = true")
        return v or 0


async def _count_concepts(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        v = await conn.fetchval("SELECT COUNT(*) FROM concept_registry")
        return v or 0


async def _count_promoted_signals_in_window(pool: asyncpg.Pool, window: str) -> int:
    async with pool.acquire() as conn:
        v = await conn.fetchval(
            "SELECT COUNT(*) FROM agg_product_signal WHERE is_promoted = true AND window_type = $1",
            window,
        )
        return v or 0


async def validate_data(
    pool: asyncpg.Pool,
    *,
    expected_min_active_products: int = 0,
    expected_min_active_users: int = 0,
    expected_min_concepts: int = 0,
    expected_min_promoted_signals: int = 0,
    signal_window: str = "all",
    enforce_stale_policy: bool = True,
    stale_threshold_days: int = 90,
    enforce_source_grounding: bool = False,
) -> ContractValidationResult:
    """Validate data readiness with caller-provided expected minimums.

    Returns:
      - INVALID for broken invariants (promotion gate, stale-active, ID mismatch,
        and source-grounding when `enforce_source_grounding=True`).
      - EMPTY when schema is fine but actual counts fall below expected minimums.
      - OK when minimums are met and no invariants are violated.
    """
    checks: list[ContractCheck] = []
    overall = ContractStatus.OK
    counts: dict[str, int] = {}

    # --- 1. Active row counts vs expected minimums ---
    active_products = await _count_active_rows(pool, "product_master")
    active_users = await _count_active_rows(pool, "user_master")
    concept_count = await _count_concepts(pool)
    promoted_in_window = await _count_promoted_signals_in_window(pool, signal_window)
    counts["active_products"] = active_products
    counts["active_users"] = active_users
    counts["concepts"] = concept_count
    counts[f"promoted_signals.{signal_window}"] = promoted_in_window

    for name, actual, expected_min in (
        ("data.active_products", active_products, expected_min_active_products),
        ("data.active_users", active_users, expected_min_active_users),
        ("data.concepts", concept_count, expected_min_concepts),
        (f"data.promoted_signals.{signal_window}", promoted_in_window, expected_min_promoted_signals),
    ):
        if actual < expected_min:
            checks.append(ContractCheck(
                name=name,
                status=ContractStatus.EMPTY,
                message=f"{name}: got {actual}, expected_min {expected_min}",
                actual=actual,
                expected_min=expected_min,
            ))
            overall = _max_status(overall, ContractStatus.EMPTY)
        else:
            checks.append(ContractCheck(
                name=name,
                status=ContractStatus.OK,
                message=f"{name}: {actual} >= {expected_min}",
                actual=actual,
                expected_min=expected_min,
            ))

    # --- 2. Promotion-gate invariant ---
    promotion_violations = await _count_promotion_invariant_violations(pool)
    counts["promotion_invariant_violations"] = promotion_violations
    if promotion_violations > 0:
        checks.append(ContractCheck(
            name="invariant.promotion_gate",
            status=ContractStatus.INVALID,
            message=(
                f"{promotion_violations} agg_product_signal row(s) have is_promoted=true "
                f"but fail the gate (distinct_review_count, avg_confidence, or "
                f"synthetic_ratio). Re-run aggregate_product_signals to refresh."
            ),
            actual=promotion_violations,
        ))
        overall = ContractStatus.INVALID
    else:
        checks.append(ContractCheck(
            name="invariant.promotion_gate",
            status=ContractStatus.OK,
            message="all promoted rows satisfy the corpus-promotion gate",
        ))

    # --- 3. Stale-active invariant ---
    if enforce_stale_policy:
        stale = await _count_stale_active_violations(pool, stale_threshold_days)
        counts["stale_active_product_signals"] = stale["product_signals"]
        counts["stale_active_user_preferences"] = stale["user_preferences"]
        if stale["product_signals"] > 0:
            checks.append(ContractCheck(
                name="invariant.stale_active.agg_product_signal",
                status=ContractStatus.INVALID,
                message=(
                    f"{stale['product_signals']} active agg_product_signal row(s) "
                    f"have last_seen_at older than {stale_threshold_days} days. "
                    f"Run mark_stale_agg_signals_inactive."
                ),
                actual=stale["product_signals"],
            ))
            overall = ContractStatus.INVALID
        if stale["user_preferences"] > 0:
            checks.append(ContractCheck(
                name="invariant.stale_active.agg_user_preference",
                status=ContractStatus.INVALID,
                message=(
                    f"{stale['user_preferences']} active agg_user_preference row(s) "
                    f"have updated_at older than {stale_threshold_days} days."
                ),
                actual=stale["user_preferences"],
            ))
            overall = ContractStatus.INVALID
        if stale["product_signals"] == 0 and stale["user_preferences"] == 0:
            checks.append(ContractCheck(
                name="invariant.stale_active",
                status=ContractStatus.OK,
                message=f"no active rows older than {stale_threshold_days} days",
            ))

    # --- 4. Product ID consistency ---
    mismatches = await _count_product_id_mismatches(pool)
    counts["product_id_mismatches.purchase_event_raw"] = mismatches["purchase_event_raw"]
    counts["product_id_mismatches.agg_product_signal"] = mismatches["agg_product_signal"]
    counts["product_id_mismatches.serving_product_profile"] = mismatches["serving_product_profile"]
    total_mismatches = sum(mismatches.values())
    if total_mismatches > 0:
        checks.append(ContractCheck(
            name="invariant.product_id_consistency",
            status=ContractStatus.INVALID,
            message=(
                f"product_ids missing from product_master: "
                f"{mismatches['purchase_event_raw']} in purchase_event_raw, "
                f"{mismatches['agg_product_signal']} in agg_product_signal, "
                f"{mismatches['serving_product_profile']} in serving_product_profile"
            ),
            actual=total_mismatches,
        ))
        overall = ContractStatus.INVALID
    else:
        checks.append(ContractCheck(
            name="invariant.product_id_consistency",
            status=ContractStatus.OK,
            message="all referenced product_ids exist in product_master",
        ))

    # --- 5. Source-grounded production-readiness invariants ---
    if enforce_source_grounding:
        source_violations = await _count_source_grounding_violations(pool)
        counts["source_grounding.source_identity"] = source_violations["source_identity"]
        counts["source_grounding.promo_prefix_brand"] = source_violations["promo_prefix_brand"]
        total_source_violations = sum(source_violations.values())
        if total_source_violations > 0:
            checks.append(ContractCheck(
                name="invariant.source_grounding",
                status=ContractStatus.INVALID,
                message=(
                    "source-grounded production readiness failed: "
                    f"{source_violations['source_identity']} serving/product source "
                    "identity mismatch row(s), "
                    f"{source_violations['promo_prefix_brand']} source-backed promo-prefix "
                    "brand row(s)"
                ),
                actual=total_source_violations,
            ))
            overall = ContractStatus.INVALID
        else:
            checks.append(ContractCheck(
                name="invariant.source_grounding",
                status=ContractStatus.OK,
                message="source identity and source-backed product truth are consistent",
            ))

    return ContractValidationResult(status=overall, checks=tuple(checks), counts=counts)


async def validate_all(
    pool: asyncpg.Pool,
    *,
    expected_min_active_products: int = 0,
    expected_min_active_users: int = 0,
    expected_min_concepts: int = 0,
    expected_min_promoted_signals: int = 0,
    signal_window: str = "all",
    enforce_stale_policy: bool = True,
    stale_threshold_days: int = 90,
    enforce_source_grounding: bool = False,
) -> ContractValidationResult:
    """Run schema validation, then data validation. Combined result.

    Schema INVALID short-circuits (data checks would error against missing
    tables anyway).
    """
    schema_result = await validate_schema(pool)
    if schema_result.status == ContractStatus.INVALID:
        return schema_result

    data_result = await validate_data(
        pool,
        expected_min_active_products=expected_min_active_products,
        expected_min_active_users=expected_min_active_users,
        expected_min_concepts=expected_min_concepts,
        expected_min_promoted_signals=expected_min_promoted_signals,
        signal_window=signal_window,
        enforce_stale_policy=enforce_stale_policy,
        stale_threshold_days=stale_threshold_days,
        enforce_source_grounding=enforce_source_grounding,
    )
    combined_status = _max_status(schema_result.status, data_result.status)
    return ContractValidationResult(
        status=combined_status,
        checks=schema_result.checks + data_result.checks,
        counts=data_result.counts,
    )
