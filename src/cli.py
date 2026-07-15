"""
Phase 1.4: CLI entrypoint for GraphRapping operators.

Thin argparse wrapper around existing library entrypoints — no new pipeline
logic lives here. Every subcommand assembles inputs the same way the
reference scripts/tests already do, then delegates:

  migrate      -> src.db.migrate.migrate
  full-load    -> src.jobs.run_full_load_db.run_full_load_to_db
                  (mirrors scripts/run_906_full_load_db.py)
  incremental  -> src.jobs.run_incremental_pipeline_db.run_incremental_to_db
                  (mirrors tests/test_incremental_pipeline_db.py's
                  _build_incremental_context helper — no prior CLI/script
                  existed for this entrypoint)
  validate     -> src.db.contract_validator.validate_all
  validate-ontology -> src.kg.ontology_validator.validate_current_ontology_configs
                  (static cross-check of the core KG ontology config files —
                  configs/kg_entity_types.json, kg_relation_types.json,
                  predicate_contracts.csv, projection_registry.csv,
                  relation_canonical_map.json — for internal consistency.
                  Exit code gates on ERROR-severity violations only; warning-
                  severity findings (orphan entity types, and the optional
                  --liveness dead-vocabulary report which runs the in-memory
                  demo pipeline) are printed but never fail. No DB/pool
                  involved; distinct from `validate` above, which checks
                  DB-persisted data contracts.)
  monitor      -> src.db.retention_monitor.run_retention_monitor
                  (read-only unbounded-growth risk report: quarantine_*,
                  agg_product_signal/agg_user_preference, raw-layer row
                  counts, and table sizes. Flags threshold breaches with a
                  non-zero exit code but issues no DELETE/DROP. See
                  DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md.)
  snapshot     -> scripts.generate_ranking_snapshot.{build_snapshot,
                  write_snapshot, load_snapshot, diff_snapshots,
                  format_diff_report} (generate: overwrite the stored
                  ranking-snapshot baseline; diff: compare current pipeline
                  output to it, exit 1 on any change). No DB/pool involved —
                  mirrors scripts/generate_ranking_snapshot.py's own CLI,
                  exposed here too so operators have one entrypoint.

Design:
- No new dependencies: argparse + asyncio only.
- Each subcommand creates its own short-lived `asyncpg.Pool` via
  `asyncpg.create_pool` directly (same pattern as
  `scripts/run_906_full_load_db.py`), and closes it in a `finally` block.
  `src.db.connection.create_pool`'s module-level cached singleton is
  intentionally NOT used here — it is documented for long-running services,
  not one-shot CLI invocations.
- `--dsn` resolution is deferred to command execution time (not argparse
  parse time) via `src.db.connection.resolve_database_url`, so `--help`
  works even without GRAPHRAPPING_DATABASE_URL / DATABASE_URL set.
- Failures print a single-line `error: ...` message to stderr and exit
  non-zero instead of raising a raw traceback, except that unexpected
  (non-RuntimeError-from-us) exceptions still include the exception text
  for operator diagnosis.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import asyncpg

from scripts.generate_ranking_snapshot import (
    DEFAULT_FIXTURE as SNAPSHOT_DEFAULT_FIXTURE,
    DEFAULT_KG_MODE as SNAPSHOT_DEFAULT_KG_MODE,
    DEFAULT_TOP_K as SNAPSHOT_DEFAULT_TOP_K,
    SNAPSHOT_PATH,
    build_snapshot,
    diff_snapshots,
    format_diff_report,
    load_snapshot,
    write_snapshot,
)
from src.db.connection import resolve_database_url
from src.db.contract_validator import ContractStatus, validate_all
from src.db.migrate import migrate
from src.db.retention_monitor import (
    DEFAULT_AGG_PRODUCT_SIGNAL_ACTIVE_THRESHOLD,
    DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD,
    DEFAULT_QUARANTINE_PER_TABLE_THRESHOLD,
    DEFAULT_QUARANTINE_TOTAL_THRESHOLD,
    DEFAULT_TABLE_SIZE_BYTES_THRESHOLD,
    RetentionMonitorResult,
    run_retention_monitor,
)
from src.jobs.run_full_load import FullLoadConfig
from src.jobs.run_full_load_db import run_full_load_to_db
from src.jobs.run_incremental_pipeline_db import run_incremental_to_db
from src.kg.ontology_validator import (
    OntologyViolation,
    collect_liveness_report,
    collect_ontology_warnings,
    validate_current_ontology_configs,
)

DEFAULT_REVIEW_JSON = "mockdata/review_triples_raw.json"
DEFAULT_PRODUCT_JSON = "mockdata/product_catalog_es.json"
DEFAULT_USER_PROFILES_JSON = "mockdata/user_profiles_normalized.json"
DEFAULT_SOURCE_REVIEW_STATS_JSON = (
    "data/source_snapshots/product_review_stats_snowflake_latest.json"
)
_POOL_OPTIONS: dict[str, Any] = {"min_size": 1, "max_size": 2}


def _load_json(path: str) -> Any:
    return __import__("json").loads(Path(path).read_text(encoding="utf-8"))


def _resolve_dsn(dsn: str | None) -> str:
    """Resolve --dsn at execution time so --help never requires env vars."""
    return resolve_database_url(dsn)


async def _with_pool(
    dsn: str | None, body: Callable[[asyncpg.Pool], Coroutine[Any, Any, int]],
) -> int:
    """Create a short-lived pool, run `body(pool)`, always close the pool."""
    resolved = _resolve_dsn(dsn)
    pool = await asyncpg.create_pool(resolved, **_POOL_OPTIONS)
    try:
        return await body(pool)
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


async def _run_migrate(dsn: str | None) -> int:
    async def _body(pool: asyncpg.Pool) -> int:
        applied = await migrate(pool)
        if applied:
            print("Applied migrations:")
            for name in applied:
                print(f"  - {name}")
        else:
            print("No migrations applied (all DDL files already up to date or missing).")
        return 0

    return await _with_pool(dsn, _body)


# ---------------------------------------------------------------------------
# full-load
# ---------------------------------------------------------------------------


async def _run_full_load(args: argparse.Namespace) -> int:
    async def _body(pool: asyncpg.Pool) -> int:
        products = _load_json(args.product_json)
        users = _load_json(args.user_profiles_json)
        config = FullLoadConfig(
            review_json_path=args.review_json,
            product_es_records=products,
            user_profiles=users,
            kg_mode=args.kg_mode,
            source_review_stats_json_path=args.source_review_stats_json,
        )
        result = await run_full_load_to_db(
            pool,
            config,
            validate_after=not args.skip_validation,
        )
        print(f"run_id: {result.run_id}")
        print(f"review_count: {result.in_memory.review_count}")
        print(f"signal_count: {result.in_memory.signal_count}")
        print(f"quarantine_count: {result.in_memory.quarantine_count}")
        print(f"serving_product_count: {result.in_memory.serving_product_count}")
        print(f"persisted: {result.persisted}")
        if result.validation is not None:
            print(f"validation_status: {result.validation.status.value}")
            if result.validation.status == ContractStatus.INVALID:
                _print_validation_checks(result.validation.checks)
                return 1
        return 0

    return await _with_pool(args.dsn, _body)


# ---------------------------------------------------------------------------
# incremental
# ---------------------------------------------------------------------------


def _build_incremental_context(product_json: str) -> dict[str, Any]:
    """Rebuild the product_index / normalizers / registry needed by
    `run_incremental_to_db`. Mirrors
    `tests/test_incremental_pipeline_db.py::_build_incremental_context` —
    no prior CLI/script assembled these inputs.
    """
    from src.common.config_loader import load_predicate_contracts
    from src.link.product_matcher import ProductIndex
    from src.loaders.product_loader import load_products_from_es_records
    from src.normalize.bee_normalizer import BEENormalizer
    from src.normalize.relation_canonicalizer import RelationCanonicalizer
    from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
    from src.wrap.projection_registry import ProjectionRegistry

    products = _load_json(product_json)
    product_result = load_products_from_es_records(products, sale_status_filter=None)
    product_index = product_result.product_index or ProductIndex.build([])
    bee_norm = BEENormalizer()
    bee_norm.load_dictionaries()
    rel_canon = RelationCanonicalizer()
    rel_canon.load()
    proj_registry = ProjectionRegistry()
    proj_registry.load()
    deriver = ToolConcernSegmentDeriver()
    deriver.load_dictionaries()
    return {
        "product_index": product_index,
        "product_masters": product_result.product_masters,
        "concept_links": product_result.concept_links,
        "bee_normalizer": bee_norm,
        "relation_canonicalizer": rel_canon,
        "projection_registry": proj_registry,
        "deriver": deriver,
        "predicate_contracts": load_predicate_contracts(),
    }


async def _run_incremental(args: argparse.Namespace) -> int:
    async def _body(pool: asyncpg.Pool) -> int:
        ctx = _build_incremental_context(args.product_json)
        result = await run_incremental_to_db(
            pool,
            **ctx,
            batch_size=args.batch_size,
            kg_mode=args.kg_mode,
            validate_after=not args.skip_validation,
        )
        print(f"run_id: {result.run_id}")
        print(f"persisted: {result.persisted}")
        if result.validation is not None:
            print(f"validation_status: {result.validation.status.value}")
            if result.validation.status == ContractStatus.INVALID:
                _print_validation_checks(result.validation.checks)
                return 1
        return 0

    return await _with_pool(args.dsn, _body)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _print_validation_checks(checks: tuple[Any, ...]) -> None:
    for check in checks:
        if check.status != ContractStatus.OK:
            print(f"  [{check.status.value}] {check.name}: {check.message}")


async def _run_validate(args: argparse.Namespace) -> int:
    async def _body(pool: asyncpg.Pool) -> int:
        result = await validate_all(
            pool,
            expected_min_active_products=args.expected_min_active_products,
            expected_min_active_users=args.expected_min_active_users,
            expected_min_concepts=args.expected_min_concepts,
            expected_min_promoted_signals=args.expected_min_promoted_signals,
            signal_window=args.signal_window,
            enforce_stale_policy=not args.no_enforce_stale_policy,
            stale_threshold_days=args.stale_threshold_days,
            enforce_source_grounding=args.enforce_source_grounding,
        )
        print(f"status: {result.status.value}")
        print("checks:")
        for check in result.checks:
            print(f"  [{check.status.value}] {check.name}: {check.message}")
        if result.counts:
            print("counts:")
            for name, value in sorted(result.counts.items()):
                print(f"  {name}: {value}")
        return 1 if result.status == ContractStatus.INVALID else 0

    return await _with_pool(args.dsn, _body)


# ---------------------------------------------------------------------------
# validate-ontology
# ---------------------------------------------------------------------------


def _print_ontology_violations(violations: list[OntologyViolation]) -> None:
    for v in violations:
        print(f"  [{v.rule}] {v.file}: {v.item} - {v.reason}")


async def _run_validate_ontology(args: argparse.Namespace) -> int:
    """Cross-check the core ontology config files (no DB/pool needed).

    Only ERROR-severity static cross-config violations gate the exit code
    (1 on any). WARNING-severity findings are printed for visibility but never
    change the exit code: (g) orphan entity types are shown on every run
    (cheap static analysis), and (h) the vocabulary-liveness report is added
    when --liveness is passed — which runs the demo pipeline in-process, hence
    it is opt-in and not part of the default/CI step.

    Stays `async def` so it fits `_DISPATCH`'s Coroutine-returning Callable type
    and `main()`'s single `asyncio.run(...)` call site — mirrors `_run_snapshot`.
    Distinct from `_run_validate` (DB-persisted data contracts, requires --dsn);
    this checks static config files (plus, with --liveness, one pipeline run).
    """
    violations = validate_current_ontology_configs()
    if violations:
        print(f"status: {len(violations)} violation(s)")
        _print_ontology_violations(violations)
    else:
        print("status: OK")

    warnings = collect_ontology_warnings()
    if args.liveness:
        report = collect_liveness_report(fixture=args.fixture)
        print(
            f"liveness: fixture={report.fixture} kg_mode={report.kg_mode} "
            f"signals={report.total_signals} "
            f"dead_families={report.dead_signal_families} "
            f"dead_object_types={report.dead_object_types}"
        )
        warnings = warnings + report.warnings()

    if warnings:
        print(f"warnings: {len(warnings)} (non-failing)")
        _print_ontology_violations(warnings)
    else:
        print("warnings: none")

    return 1 if violations else 0


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


def _print_retention_result(result: RetentionMonitorResult) -> None:
    print("quarantine:")
    for c in result.quarantine_counts:
        print(f"  {c.table}: {c.row_count}")
    print(f"  total: {result.quarantine_total}")

    print("agg_product_signal:")
    for w in result.agg_product_signal_counts:
        print(f"  {w.window_type}: total={w.total} active={w.active} inactive={w.inactive}")

    pref = result.agg_user_preference
    print(f"agg_user_preference: total={pref.total} active={pref.active} inactive={pref.inactive}")

    print("raw_layer:")
    for c in result.raw_layer_counts:
        print(f"  {c.table}: {c.row_count}")

    print("table_sizes:")
    for s in result.table_sizes:
        print(f"  {s.table}: {s.pretty_total} ({s.total_bytes} bytes)")

    if result.warnings:
        print("warnings:")
        for warning in result.warnings:
            print(
                f"  [{warning.metric}] {warning.message} "
                f"(actual={warning.actual}, threshold={warning.threshold})"
            )
    else:
        print("warnings: none")


async def _run_monitor(args: argparse.Namespace) -> int:
    async def _body(pool: asyncpg.Pool) -> int:
        raw_overrides: dict[str, int] = {}
        for table, value in (
            ("review_raw", args.review_raw_threshold),
            ("ner_raw", args.ner_raw_threshold),
            ("bee_raw", args.bee_raw_threshold),
            ("rel_raw", args.rel_raw_threshold),
        ):
            if value is not None:
                raw_overrides[table] = value

        result = await run_retention_monitor(
            pool,
            quarantine_total_threshold=args.quarantine_total_threshold,
            quarantine_per_table_threshold=args.quarantine_per_table_threshold,
            agg_product_signal_active_threshold=args.agg_product_signal_active_threshold,
            agg_user_preference_active_threshold=args.agg_user_preference_active_threshold,
            raw_table_row_thresholds=raw_overrides or None,
            table_size_bytes_threshold=args.table_size_bytes_threshold,
        )
        _print_retention_result(result)
        return 1 if result.warnings else 0

    return await _with_pool(args.dsn, _body)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def _run_snapshot_generate(args: argparse.Namespace) -> int:
    snapshot = build_snapshot(fixture=args.fixture, kg_mode=args.kg_mode, top_k=args.top_k)
    write_snapshot(snapshot, args.snapshot_path)
    print(
        f"updated snapshot: {args.snapshot_path} "
        f"({snapshot['combination_count']} combinations)"
    )
    return 0


def _run_snapshot_diff(args: argparse.Namespace) -> int:
    current = build_snapshot(fixture=args.fixture, kg_mode=args.kg_mode, top_k=args.top_k)
    try:
        baseline = load_snapshot(args.snapshot_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    diff_lines = diff_snapshots(baseline, current)
    print(format_diff_report(diff_lines, snapshot_path=args.snapshot_path))
    return 1 if diff_lines else 0


_SNAPSHOT_DISPATCH: dict[str, Callable[[argparse.Namespace], int]] = {
    "generate": _run_snapshot_generate,
    "diff": _run_snapshot_diff,
}


async def _run_snapshot(args: argparse.Namespace) -> int:
    """Dispatch snapshot generate/diff.

    No DB/pool needed here (unlike every other subcommand) — this stays
    `async def` purely so it fits _DISPATCH's Coroutine-returning Callable
    type and `main()`'s single `asyncio.run(...)` call site can stay uniform
    across subcommands.
    """
    # Mirror scripts/generate_ranking_snapshot.py's guard: a non-positive
    # top_k yields an empty/meaningless snapshot, so reject it before building.
    if args.top_k <= 0:
        print("error: --top-k must be positive", file=sys.stderr)
        return 2
    return _SNAPSHOT_DISPATCH[args.snapshot_command](args)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graphrapping",
        description=(
            "GraphRapping operator CLI: migrate / full-load / incremental / validate / "
            "validate-ontology / monitor / snapshot."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_dsn(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dsn",
            default=None,
            help=(
                "Postgres DSN. Defaults to GRAPHRAPPING_DATABASE_URL, then "
                "DATABASE_URL. Required (via argument or env) at run time."
            ),
        )

    migrate_p = subparsers.add_parser("migrate", help="Apply DDL migrations.")
    add_dsn(migrate_p)

    full_load_p = subparsers.add_parser("full-load", help="Run the full load pipeline.")
    add_dsn(full_load_p)
    full_load_p.add_argument("--review-json", default=DEFAULT_REVIEW_JSON)
    full_load_p.add_argument("--product-json", default=DEFAULT_PRODUCT_JSON)
    full_load_p.add_argument("--user-profiles-json", default=DEFAULT_USER_PROFILES_JSON)
    full_load_p.add_argument(
        "--source-review-stats-json", default=DEFAULT_SOURCE_REVIEW_STATS_JSON,
    )
    full_load_p.add_argument("--kg-mode", default="off", choices=("off", "on"))
    full_load_p.add_argument(
        "--skip-validation", action="store_true",
        help="Skip contract_validator.validate_all after load.",
    )

    incremental_p = subparsers.add_parser(
        "incremental", help="Run the incremental pipeline (processes reviews since last watermark).",
    )
    add_dsn(incremental_p)
    incremental_p.add_argument(
        "--product-json", default=DEFAULT_PRODUCT_JSON,
        help="Product catalog used to rebuild the product index/masters/concept links.",
    )
    incremental_p.add_argument("--batch-size", type=int, default=1000)
    incremental_p.add_argument("--kg-mode", default=None, choices=(None, "off", "on"))
    incremental_p.add_argument(
        "--skip-validation", action="store_true",
        help="Skip contract_validator.validate_all after the run.",
    )

    validate_p = subparsers.add_parser(
        "validate", help="Run contract_validator.validate_all and report readiness.",
    )
    add_dsn(validate_p)
    validate_p.add_argument("--expected-min-active-products", type=int, default=0)
    validate_p.add_argument("--expected-min-active-users", type=int, default=0)
    validate_p.add_argument("--expected-min-concepts", type=int, default=0)
    validate_p.add_argument("--expected-min-promoted-signals", type=int, default=0)
    validate_p.add_argument("--signal-window", default="all")
    validate_p.add_argument(
        "--no-enforce-stale-policy", action="store_true",
        help="Disable the stale-active invariant check (enabled by default).",
    )
    validate_p.add_argument("--stale-threshold-days", type=int, default=90)
    validate_p.add_argument(
        "--enforce-source-grounding", action="store_true",
        help="Enable production source-grounding invariants (off by default).",
    )

    validate_ontology_p = subparsers.add_parser(
        "validate-ontology",
        help=(
            "Cross-check kg_entity_types/kg_relation_types/predicate_contracts/"
            "projection_registry/relation_canonical_map configs for internal "
            "consistency (no DB). Exit code reflects ERROR-severity violations "
            "only; warnings (orphan types, --liveness report) are informational."
        ),
    )
    validate_ontology_p.add_argument(
        "--liveness", action="store_true",
        help=(
            "Also run the in-memory demo pipeline (no DB) and report vocabulary "
            "defined in projection_registry.csv but generated 0 times "
            "(warning-only; not part of the default/CI step)."
        ),
    )
    validate_ontology_p.add_argument(
        "--fixture", choices=("dense_golden", "wide"), default="dense_golden",
        help="Fixture for the --liveness pipeline run (default: %(default)s).",
    )

    monitor_p = subparsers.add_parser(
        "monitor",
        help=(
            "Report retention/unbounded-growth risk metrics (quarantine_*, agg_*, "
            "raw layer, table sizes) and flag threshold breaches."
        ),
    )
    add_dsn(monitor_p)
    monitor_p.add_argument(
        "--quarantine-total-threshold", type=int, default=DEFAULT_QUARANTINE_TOTAL_THRESHOLD,
        help="Combined quarantine_* row-count threshold (default: %(default)s).",
    )
    monitor_p.add_argument(
        "--quarantine-per-table-threshold", type=int, default=DEFAULT_QUARANTINE_PER_TABLE_THRESHOLD,
        help="Per-table quarantine_* row-count threshold (default: %(default)s).",
    )
    monitor_p.add_argument(
        "--agg-product-signal-active-threshold", type=int,
        default=DEFAULT_AGG_PRODUCT_SIGNAL_ACTIVE_THRESHOLD,
        help="Per-window agg_product_signal active row-count threshold (default: %(default)s).",
    )
    monitor_p.add_argument(
        "--agg-user-preference-active-threshold", type=int,
        default=DEFAULT_AGG_USER_PREFERENCE_ACTIVE_THRESHOLD,
        help="agg_user_preference active row-count threshold (default: %(default)s).",
    )
    monitor_p.add_argument(
        "--review-raw-threshold", type=int, default=None,
        help="Override the review_raw row-count threshold (default: retention_monitor's own per-table default).",
    )
    monitor_p.add_argument(
        "--ner-raw-threshold", type=int, default=None,
        help="Override the ner_raw row-count threshold (default: retention_monitor's own per-table default).",
    )
    monitor_p.add_argument(
        "--bee-raw-threshold", type=int, default=None,
        help="Override the bee_raw row-count threshold (default: retention_monitor's own per-table default).",
    )
    monitor_p.add_argument(
        "--rel-raw-threshold", type=int, default=None,
        help="Override the rel_raw row-count threshold (default: retention_monitor's own per-table default).",
    )
    monitor_p.add_argument(
        "--table-size-bytes-threshold", type=int, default=DEFAULT_TABLE_SIZE_BYTES_THRESHOLD,
        help="Per-table physical size threshold in bytes (default: %(default)s).",
    )

    snapshot_p = subparsers.add_parser(
        "snapshot", help="Generate or diff the ranking-snapshot regression fixture (no DB).",
    )
    snapshot_sub = snapshot_p.add_subparsers(dest="snapshot_command", required=True)

    def add_snapshot_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--fixture", default=SNAPSHOT_DEFAULT_FIXTURE,
            help="Fixture name passed to build_audit_report (default: %(default)s).",
        )
        p.add_argument("--kg-mode", choices=("on", "off"), default=SNAPSHOT_DEFAULT_KG_MODE)
        p.add_argument("--top-k", type=int, default=SNAPSHOT_DEFAULT_TOP_K)
        p.add_argument(
            "--snapshot-path", type=Path, default=SNAPSHOT_PATH,
            help="Path to the stored snapshot JSON (default: tests/fixtures/ranking_snapshots/dense_golden.json).",
        )

    snapshot_generate_p = snapshot_sub.add_parser(
        "generate", help="Overwrite the stored snapshot with the current pipeline output.",
    )
    add_snapshot_args(snapshot_generate_p)

    snapshot_diff_p = snapshot_sub.add_parser(
        "diff", help="Diff current pipeline output against the stored snapshot (exit 1 on any change).",
    )
    add_snapshot_args(snapshot_diff_p)

    return parser


_DISPATCH: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "migrate": lambda args: _run_migrate(args.dsn),
    "full-load": _run_full_load,
    "incremental": _run_incremental,
    "validate": _run_validate,
    "validate-ontology": _run_validate_ontology,
    "monitor": _run_monitor,
    "snapshot": _run_snapshot,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH[args.command]
    try:
        return asyncio.run(handler(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, don't traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
