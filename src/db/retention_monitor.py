"""
Phase 1.3: retention monitoring for known unbounded-growth risk tables.

Read-only visibility into the 3 unbounded-accumulation risks documented in
`docs/architecture/db_consumer_contract.md` §12.3:

1. `agg_product_signal` (the `all` window especially) — 90-day stale cleanup
   is opt-in (`GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`,
   [run_incremental_pipeline.py](../jobs/run_incremental_pipeline.py)) and a
   popular product's `last_seen_at` keeps advancing, so its `is_active=true`
   row never ages out even when cleanup runs.
2. `quarantine_*` (5 tables, `sql/ddl_quarantine.sql`) — no TTL/retention
   policy at all. Every pipeline run can add rows; nothing ever removes them.
3. `ner_raw` / `bee_raw` / `rel_raw` / `review_raw` — `review_version`-scoped
   append-only tables (`sql/ddl_raw.sql`). A frequently re-reviewed corpus
   grows these without bound.

This module answers "how much has accumulated, and is it past a watchable
threshold" — nothing else. It issues no DELETE/DROP/partition statement;
TTL/cleanup *execution* is out of scope for fixture-stage GraphRapping (see
`fable_doc/03_improvement_plan.md` Phase 1.3 and
`DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md` for the design
and the policy decision this monitor is meant to inform).

Mirrors `src/db/contract_validator.py`'s style: small `get_*` functions each
issue their own query and return a frozen dataclass; `run_retention_monitor`
composes them and evaluates caller-overridable thresholds into a `warnings`
list. Default thresholds are grounded in the 906-review / 517-product fixture
baseline (`docs/architecture/db_consumer_contract.md` §3/§12,
`docs/architecture/v260605_906_fixture_lineage.md` §4) with headroom so a
routine fixture reload does not trip them — see the DECISIONS doc for the
per-threshold rationale and the production recalibration plan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import asyncpg

# ---------------------------------------------------------------------------
# Monitored table groups (sql/ddl_quarantine.sql, sql/ddl_raw.sql, sql/ddl_mart.sql)
# ---------------------------------------------------------------------------

QUARANTINE_TABLES: tuple[str, ...] = (
    "quarantine_product_match",
    "quarantine_placeholder",
    "quarantine_unknown_keyword",
    "quarantine_projection_miss",
    "quarantine_untyped_entity",
)

# review_version-scoped append-only tables (db_consumer_contract.md §12.3 risk #3).
RAW_APPEND_ONLY_TABLES: tuple[str, ...] = (
    "review_raw",
    "ner_raw",
    "bee_raw",
    "rel_raw",
)

# Superset used for the operator size report (consumer contract §12.4: "per
# 5-layer table"). Quarantine + raw append-only layer + the two aggregate
# tables that carry the opt-in cleanup gate.
SIZE_MONITORED_TABLES: tuple[str, ...] = (
    *QUARANTINE_TABLES,
    *RAW_APPEND_ONLY_TABLES,
    "agg_product_signal",
    "agg_user_preference",
)


# ---------------------------------------------------------------------------
# Result shapes (mirrors src/db/contract_validator.py's frozen-dataclass style)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableRowCount:
    table: str
    row_count: int


@dataclass(frozen=True)
class ActiveSplitCount:
    """Row count split by `is_active`, for tables carrying a soft-delete flag."""

    total: int
    active: int
    inactive: int


@dataclass(frozen=True)
class AggProductSignalWindowCount:
    window_type: str
    total: int
    active: int
    inactive: int


@dataclass(frozen=True)
class TableSize:
    table: str
    relation_bytes: int
    total_bytes: int
    pretty_total: str


@dataclass(frozen=True)
class RetentionWarning:
    metric: str
    message: str
    actual: int
    threshold: int


@dataclass(frozen=True)
class RetentionMonitorResult:
    quarantine_counts: tuple[TableRowCount, ...] = ()
    quarantine_total: int = 0
    agg_product_signal_counts: tuple[AggProductSignalWindowCount, ...] = ()
    agg_user_preference: ActiveSplitCount = field(
        default_factory=lambda: ActiveSplitCount(total=0, active=0, inactive=0)
    )
    raw_layer_counts: tuple[TableRowCount, ...] = ()
    table_sizes: tuple[TableSize, ...] = ()
    warnings: tuple[RetentionWarning, ...] = ()


# ---------------------------------------------------------------------------
# Default thresholds. All overridable per-call via `run_retention_monitor`
# kwargs. Grounded in the 906-review/517-product fixture baseline with
# headroom (see DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md
# for the full rationale) — they are fixture-scale trip-wires meant to be
# recalibrated once real production ingestion volume is known, not
# production-calibrated limits.
# ---------------------------------------------------------------------------

# Baseline (docs/architecture/db_consumer_contract.md §3, 2026-06-18):
# kg_off quarantine total = 9,255 rows for 906 reviews / 517 products.
DEFAULT_QUARANTINE_TOTAL_THRESHOLD = 20_000
DEFAULT_QUARANTINE_PER_TABLE_THRESHOLD = 8_000

# Baseline (docs/architecture/v260605_906_fixture_lineage.md §4, 2026-06-16):
# agg_product_signal totals 6,849 rows summed across all 3 windows.
DEFAULT_AGG_PRODUCT_SIGNAL_ACTIVE_THRESHOLD = 10_000

# Baseline: 50 fixture users. Generous headroom pending real user-scale data.
DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD = 5_000

# Baseline row counts (docs/architecture/v260605_906_fixture_lineage.md §4):
# review_raw=906, ner_raw=4507, bee_raw=2783, rel_raw=20741.
DEFAULT_RAW_TABLE_ROW_THRESHOLDS: dict[str, int] = {
    "review_raw": 5_000,
    "ner_raw": 20_000,
    "bee_raw": 15_000,
    "rel_raw": 80_000,
}

# No fixture-scale byte baseline captured yet; 500MB is a generic "far too
# big for a fixture DB" trip-wire pending real measurement.
DEFAULT_TABLE_SIZE_BYTES_THRESHOLD = 500 * 1024 * 1024


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


async def get_quarantine_counts(pool: asyncpg.Pool) -> tuple[TableRowCount, ...]:
    """Row count for each of the 5 quarantine_* tables.

    No TTL exists for any of them (db_consumer_contract.md §12.3 risk #2) —
    this is read-only visibility, not enforcement.
    """
    counts: list[TableRowCount] = []
    async with pool.acquire() as conn:
        for table in QUARANTINE_TABLES:
            v = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            counts.append(TableRowCount(table=table, row_count=v or 0))
    return tuple(counts)


async def get_agg_product_signal_counts(
    pool: asyncpg.Pool,
) -> tuple[AggProductSignalWindowCount, ...]:
    """`agg_product_signal` row count grouped by `window_type`, split by `is_active`.

    The `all` window is the highest-risk one (db_consumer_contract.md §12.3
    risk #1): stale cleanup is opt-in, and a popular product's `last_seen_at`
    keeps advancing so its active row never ages out.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT window_type, is_active, COUNT(*) AS row_count
            FROM agg_product_signal
            GROUP BY window_type, is_active
            """
        )
    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        window = r["window_type"] or "unknown"
        bucket = buckets.setdefault(window, {"active": 0, "inactive": 0})
        key = "active" if r["is_active"] else "inactive"
        bucket[key] += r["row_count"]
    return tuple(
        AggProductSignalWindowCount(
            window_type=window,
            total=bucket["active"] + bucket["inactive"],
            active=bucket["active"],
            inactive=bucket["inactive"],
        )
        for window, bucket in sorted(buckets.items())
    )


async def get_agg_user_preference_count(pool: asyncpg.Pool) -> ActiveSplitCount:
    """`agg_user_preference` row count, split by `is_active`.

    Same opt-in cleanup gate and same failure mode as `agg_product_signal`
    (db_consumer_contract.md §12.1): active users never age out either.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT is_active, COUNT(*) AS row_count FROM agg_user_preference GROUP BY is_active"
        )
    active = 0
    inactive = 0
    for r in rows:
        if r["is_active"]:
            active = r["row_count"]
        else:
            inactive = r["row_count"]
    return ActiveSplitCount(total=active + inactive, active=active, inactive=inactive)


async def get_raw_layer_counts(pool: asyncpg.Pool) -> tuple[TableRowCount, ...]:
    """Row count for `review_raw` / `ner_raw` / `bee_raw` / `rel_raw`.

    All 4 are `review_version`-scoped append-only tables
    (db_consumer_contract.md §12.3 risk #3): re-reviewing the same
    `review_id` adds rows, it never replaces them in place.
    """
    counts: list[TableRowCount] = []
    async with pool.acquire() as conn:
        for table in RAW_APPEND_ONLY_TABLES:
            v = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            counts.append(TableRowCount(table=table, row_count=v or 0))
    return tuple(counts)


async def get_table_sizes(
    pool: asyncpg.Pool,
    tables: Sequence[str] = SIZE_MONITORED_TABLES,
) -> tuple[TableSize, ...]:
    """`pg_relation_size` / `pg_total_relation_size` for each table.

    db_consumer_contract.md §12.4 recommends `pg_relation_size(table_name)`
    per layer table for long-term (1yr+) operation monitoring. Tables that do
    not exist yet (e.g. a schema mid-migration) are skipped rather than
    raising, so callers can pass an arbitrary table list.
    """
    sizes: list[TableSize] = []
    async with pool.acquire() as conn:
        for table in tables:
            try:
                row = await conn.fetchrow(
                    """
                    SELECT
                        pg_relation_size($1::regclass) AS relation_bytes,
                        pg_total_relation_size($1::regclass) AS total_bytes,
                        pg_size_pretty(pg_total_relation_size($1::regclass)) AS pretty_total
                    """,
                    table,
                )
            except asyncpg.UndefinedTableError:
                continue
            if row is None:
                continue
            sizes.append(
                TableSize(
                    table=table,
                    relation_bytes=row["relation_bytes"] or 0,
                    total_bytes=row["total_bytes"] or 0,
                    pretty_total=row["pretty_total"] or "0 bytes",
                )
            )
    return tuple(sizes)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_retention_monitor(
    pool: asyncpg.Pool,
    *,
    quarantine_total_threshold: int = DEFAULT_QUARANTINE_TOTAL_THRESHOLD,
    quarantine_per_table_threshold: int = DEFAULT_QUARANTINE_PER_TABLE_THRESHOLD,
    agg_product_signal_active_threshold: int = DEFAULT_AGG_PRODUCT_SIGNAL_ACTIVE_THRESHOLD,
    agg_user_preference_active_threshold: int = DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD,
    raw_table_row_thresholds: Mapping[str, int] | None = None,
    table_size_bytes_threshold: int = DEFAULT_TABLE_SIZE_BYTES_THRESHOLD,
) -> RetentionMonitorResult:
    """Collect row counts + table sizes for the 3 documented unbounded-growth
    risks (db_consumer_contract.md §12.3) and flag threshold breaches.

    Read-only: issues no DELETE/DROP/partition statement. A threshold breach
    only produces an entry in `.warnings` — this function never raises or
    fails on a breach; callers decide what to do with it (log, alert,
    surface in a future CLI `monitor` command, etc).

    `raw_table_row_thresholds` merges onto `DEFAULT_RAW_TABLE_ROW_THRESHOLDS`
    (only the tables you pass are overridden; the rest keep their default).
    """
    merged_raw_thresholds = dict(DEFAULT_RAW_TABLE_ROW_THRESHOLDS)
    if raw_table_row_thresholds:
        merged_raw_thresholds.update(raw_table_row_thresholds)

    quarantine_counts = await get_quarantine_counts(pool)
    agg_signal_counts = await get_agg_product_signal_counts(pool)
    agg_pref = await get_agg_user_preference_count(pool)
    raw_counts = await get_raw_layer_counts(pool)
    table_sizes = await get_table_sizes(pool)

    warnings: list[RetentionWarning] = []
    quarantine_total = sum(c.row_count for c in quarantine_counts)

    # --- quarantine_* (risk #2: no TTL) ---
    if quarantine_total > quarantine_total_threshold:
        warnings.append(
            RetentionWarning(
                metric="quarantine_total",
                message=(
                    f"quarantine_* (5 tables) combined row count {quarantine_total} "
                    f"exceeds threshold {quarantine_total_threshold}. No TTL exists "
                    "for quarantine tables (db_consumer_contract.md §12.3 risk #2)."
                ),
                actual=quarantine_total,
                threshold=quarantine_total_threshold,
            )
        )
    for c in quarantine_counts:
        if c.row_count > quarantine_per_table_threshold:
            warnings.append(
                RetentionWarning(
                    metric=f"quarantine.{c.table}",
                    message=(
                        f"{c.table} row count {c.row_count} exceeds threshold "
                        f"{quarantine_per_table_threshold}."
                    ),
                    actual=c.row_count,
                    threshold=quarantine_per_table_threshold,
                )
            )

    # --- agg_product_signal (risk #1: opt-in cleanup, popular rows never stale) ---
    for w in agg_signal_counts:
        if w.active > agg_product_signal_active_threshold:
            warnings.append(
                RetentionWarning(
                    metric=f"agg_product_signal.{w.window_type}.active",
                    message=(
                        f"agg_product_signal window={w.window_type} active row count "
                        f"{w.active} exceeds threshold {agg_product_signal_active_threshold}. "
                        "Stale cleanup is opt-in (GRAPHRAPPING_AGG_CLEANUP_ENABLED=1) and a "
                        "popular product's last_seen_at never goes stale "
                        "(db_consumer_contract.md §12.3 risk #1)."
                    ),
                    actual=w.active,
                    threshold=agg_product_signal_active_threshold,
                )
            )

    # --- agg_user_preference (same opt-in cleanup gate) ---
    if agg_pref.active > agg_user_preference_active_threshold:
        warnings.append(
            RetentionWarning(
                metric="agg_user_preference.active",
                message=(
                    f"agg_user_preference active row count {agg_pref.active} exceeds "
                    f"threshold {agg_user_preference_active_threshold}. Same opt-in "
                    "cleanup gate as agg_product_signal (db_consumer_contract.md §12.1)."
                ),
                actual=agg_pref.active,
                threshold=agg_user_preference_active_threshold,
            )
        )

    # --- ner_raw / bee_raw / rel_raw / review_raw (risk #3: append-only) ---
    for c in raw_counts:
        threshold = merged_raw_thresholds.get(c.table)
        if threshold is not None and c.row_count > threshold:
            warnings.append(
                RetentionWarning(
                    metric=f"raw_layer.{c.table}",
                    message=(
                        f"{c.table} row count {c.row_count} exceeds threshold {threshold}. "
                        "review_version append-only growth (db_consumer_contract.md §12.3 risk #3)."
                    ),
                    actual=c.row_count,
                    threshold=threshold,
                )
            )

    # --- physical table size (any monitored table) ---
    for s in table_sizes:
        if s.total_bytes > table_size_bytes_threshold:
            warnings.append(
                RetentionWarning(
                    metric=f"table_size.{s.table}",
                    message=(
                        f"{s.table} total size {s.pretty_total} ({s.total_bytes} bytes) "
                        f"exceeds threshold {table_size_bytes_threshold} bytes."
                    ),
                    actual=s.total_bytes,
                    threshold=table_size_bytes_threshold,
                )
            )

    return RetentionMonitorResult(
        quarantine_counts=quarantine_counts,
        quarantine_total=quarantine_total,
        agg_product_signal_counts=agg_signal_counts,
        agg_user_preference=agg_pref,
        raw_layer_counts=raw_counts,
        table_sizes=table_sizes,
        warnings=tuple(warnings),
    )
