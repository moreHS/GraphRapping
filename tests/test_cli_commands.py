"""
CLI subcommand tests (migrate / full-load / incremental / validate / snapshot)
plus `main()` dispatch and the exception → exit-code boundary.

No real DB and no real pipeline: `asyncpg.create_pool` is monkeypatched on
`src.cli.asyncpg` to hand back a fake pool (mirrors `test_cli_monitor.py`), and
each subcommand's underlying entrypoint (dotted into `src.cli`) is replaced with
a stub that captures the kwargs it was called with. These tests therefore
exercise only the CLI's argument wiring, printing, and exit-code logic.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import cli
from src.db.contract_validator import ContractCheck, ContractStatus


class _FakePool:
    """Stand-in for asyncpg.Pool; only `close()` is exercised by `_with_pool`."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def _stub_create_pool(monkeypatch: pytest.MonkeyPatch) -> list[_FakePool]:
    """Replace asyncpg.create_pool (as dotted in src.cli) with a fake pool
    factory so no subcommand ever opens a real connection."""
    created: list[_FakePool] = []

    async def _fake_create_pool(_url: str, **_kwargs: Any) -> _FakePool:
        pool = _FakePool()
        created.append(pool)
        return pool

    monkeypatch.setattr(cli.asyncpg, "create_pool", _fake_create_pool)
    return created


_DSN = ["--dsn", "postgresql://fake/db"]


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_prints_applied_and_returns_zero(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _fake_migrate(pool: Any) -> list[str]:
        return ["001_init.sql", "002_add_cols.sql"]

    monkeypatch.setattr(cli, "migrate", _fake_migrate)
    args = cli.build_parser().parse_args(["migrate", *_DSN])

    exit_code = await cli._run_migrate(args.dsn)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Applied migrations:" in out
    assert "001_init.sql" in out
    assert _stub_create_pool[0].closed is True


@pytest.mark.asyncio
async def test_migrate_prints_none_when_no_migrations_applied(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _fake_migrate(pool: Any) -> list[str]:
        return []

    monkeypatch.setattr(cli, "migrate", _fake_migrate)

    exit_code = await cli._run_migrate("postgresql://fake/db")

    assert exit_code == 0
    assert "No migrations applied" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# full-load
# ---------------------------------------------------------------------------


class _FakeInMemory:
    review_count = 906
    signal_count = 2801
    quarantine_count = 9255
    serving_product_count = 517


class _FakeFullLoadResult:
    def __init__(self, validation: Any = None) -> None:
        self.run_id = 42
        self.in_memory = _FakeInMemory()
        self.persisted = {"product_masters": 517}
        self.validation = validation


@pytest.fixture
def _stub_full_load_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid touching mockdata files: both product/user loads return []."""
    monkeypatch.setattr(cli, "_load_json", lambda _path: [])


@pytest.mark.asyncio
async def test_full_load_prints_summary_and_returns_zero(
    _stub_create_pool: list[_FakePool],
    _stub_full_load_inputs: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(pool: Any, config: Any, **kwargs: Any) -> _FakeFullLoadResult:
        captured["config"] = config
        captured["kwargs"] = kwargs
        return _FakeFullLoadResult(validation=None)

    monkeypatch.setattr(cli, "run_full_load_to_db", _fake_run)
    args = cli.build_parser().parse_args(["full-load", *_DSN])

    exit_code = await cli._run_full_load(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "run_id: 42" in out
    assert "review_count: 906" in out
    assert "signal_count: 2801" in out
    # No --skip-validation → validate_after True.
    assert captured["kwargs"]["validate_after"] is True
    assert _stub_create_pool[0].closed is True


@pytest.mark.asyncio
async def test_full_load_skip_validation_flag_disables_validate_after(
    _stub_create_pool: list[_FakePool],
    _stub_full_load_inputs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_run(pool: Any, config: Any, **kwargs: Any) -> _FakeFullLoadResult:
        captured["kwargs"] = kwargs
        return _FakeFullLoadResult(validation=None)

    monkeypatch.setattr(cli, "run_full_load_to_db", _fake_run)
    args = cli.build_parser().parse_args(["full-load", *_DSN, "--skip-validation"])

    await cli._run_full_load(args)

    assert captured["kwargs"]["validate_after"] is False


@pytest.mark.asyncio
async def test_full_load_returns_one_on_invalid_validation(
    _stub_create_pool: list[_FakePool],
    _stub_full_load_inputs: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Validation:
        status = ContractStatus.INVALID
        checks = (
            ContractCheck(
                name="invariant.promotion_gate",
                status=ContractStatus.INVALID,
                message="gate failed",
            ),
        )

    async def _fake_run(pool: Any, config: Any, **kwargs: Any) -> _FakeFullLoadResult:
        return _FakeFullLoadResult(validation=_Validation())

    monkeypatch.setattr(cli, "run_full_load_to_db", _fake_run)
    args = cli.build_parser().parse_args(["full-load", *_DSN])

    exit_code = await cli._run_full_load(args)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "validation_status: INVALID" in out
    assert "invariant.promotion_gate" in out


# ---------------------------------------------------------------------------
# incremental
# ---------------------------------------------------------------------------


class _FakeIncrementalResult:
    def __init__(self, validation: Any = None) -> None:
        self.run_id = 7
        self.persisted = {"review_bundles": 3}
        self.validation = validation


@pytest.mark.asyncio
async def test_incremental_prints_summary_and_returns_zero(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The real context builder loads dictionaries/registries — stub it out.
    monkeypatch.setattr(cli, "_build_incremental_context", lambda _product_json: {})
    captured: dict[str, Any] = {}

    async def _fake_incr(pool: Any, **kwargs: Any) -> _FakeIncrementalResult:
        captured["kwargs"] = kwargs
        return _FakeIncrementalResult(validation=None)

    monkeypatch.setattr(cli, "run_incremental_to_db", _fake_incr)
    args = cli.build_parser().parse_args(["incremental", *_DSN])

    exit_code = await cli._run_incremental(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "run_id: 7" in out
    assert captured["kwargs"]["validate_after"] is True
    assert captured["kwargs"]["batch_size"] == 1000
    assert _stub_create_pool[0].closed is True


@pytest.mark.asyncio
async def test_incremental_returns_one_on_invalid_validation(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Validation:
        status = ContractStatus.INVALID
        checks = (
            ContractCheck(name="x", status=ContractStatus.INVALID, message="bad"),
        )

    monkeypatch.setattr(cli, "_build_incremental_context", lambda _product_json: {})

    async def _fake_incr(pool: Any, **kwargs: Any) -> _FakeIncrementalResult:
        return _FakeIncrementalResult(validation=_Validation())

    monkeypatch.setattr(cli, "run_incremental_to_db", _fake_incr)
    args = cli.build_parser().parse_args(["incremental", *_DSN])

    exit_code = await cli._run_incremental(args)

    assert exit_code == 1


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class _FakeValidation:
    def __init__(self, status: ContractStatus) -> None:
        self.status = status
        self.checks = (
            ContractCheck(name="schema.product_master", status=ContractStatus.OK, message="ok"),
        )
        self.counts = {"active_products": 517}


@pytest.mark.asyncio
async def test_validate_prints_status_counts_and_returns_zero_on_ok(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_validate(pool: Any, **kwargs: Any) -> _FakeValidation:
        captured["kwargs"] = kwargs
        return _FakeValidation(ContractStatus.OK)

    monkeypatch.setattr(cli, "validate_all", _fake_validate)
    args = cli.build_parser().parse_args(
        ["validate", *_DSN, "--expected-min-active-products", "1"]
    )

    exit_code = await cli._run_validate(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "status: OK" in out
    assert "active_products: 517" in out
    assert captured["kwargs"]["expected_min_active_products"] == 1
    assert _stub_create_pool[0].closed is True


@pytest.mark.asyncio
async def test_validate_returns_one_on_invalid(
    _stub_create_pool: list[_FakePool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _fake_validate(pool: Any, **kwargs: Any) -> _FakeValidation:
        return _FakeValidation(ContractStatus.INVALID)

    monkeypatch.setattr(cli, "validate_all", _fake_validate)
    args = cli.build_parser().parse_args(["validate", *_DSN])

    exit_code = await cli._run_validate(args)

    assert exit_code == 1
    assert "status: INVALID" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# validate-ontology (no DB/pool)
# ---------------------------------------------------------------------------


def test_validate_ontology_dispatches_and_returns_zero_on_clean_configs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real execution, no mocking: validate-ontology needs no DB/pool and the
    current configs/ files have zero ERROR-severity violations (see
    src.kg.ontology_validator), so exercising main()'s actual dispatch
    end-to-end is fast and safe. Warning-severity findings (the 4 known orphan
    entity types) are printed but never affect the exit code."""
    rc = cli.main(["validate-ontology"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "status: OK" in out
    # v2: the known orphan types are surfaced as non-failing warnings.
    assert "warnings: 4 (non-failing)" in out
    assert "[orphan_entity_type]" in out


def test_validate_ontology_returns_one_on_error_violations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ERROR-severity violations gate the exit code; warnings do not."""
    from src.kg.ontology_validator import OntologyViolation

    monkeypatch.setattr(
        cli, "validate_current_ontology_configs",
        lambda: [OntologyViolation(
            rule="canonical_map_meta_count",
            file="relation_canonical_map.json",
            item="meta.total_labels=65",
            reason="meta.total_labels declares 65 but label_to_canonical has 68 entries",
        )],
    )
    monkeypatch.setattr(cli, "collect_ontology_warnings", lambda: [])

    rc = cli.main(["validate-ontology"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "status: 1 violation(s)" in out
    assert "[canonical_map_meta_count]" in out


@pytest.mark.asyncio
async def test_validate_ontology_liveness_flag_reports_dead_vocab_without_failing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--liveness adds the (data-dependent) dead-vocabulary report as warnings
    only: exit stays 0 on clean configs. The heavy pipeline runner is
    monkeypatched — its real execution is a manual/documented step, not CI."""
    from src.kg.ontology_validator import build_liveness_report

    captured_kwargs: dict[str, Any] = {}

    def _fake_collect(*, fixture: str) -> Any:
        captured_kwargs["fixture"] = fixture
        return build_liveness_report(
            fixture=fixture,
            kg_mode="on",
            total_signals=42,
            defined_signal_families={"BEE_ATTR", "TOOL"},
            generated_signal_families={"BEE_ATTR"},
            defined_object_types={"BEEAttr", "Tool"},
            generated_object_types={"BEEAttr"},
        )

    monkeypatch.setattr(cli, "collect_liveness_report", _fake_collect)
    args = cli.build_parser().parse_args(["validate-ontology", "--liveness", "--fixture", "wide"])

    exit_code = await cli._run_validate_ontology(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured_kwargs["fixture"] == "wide"
    assert "liveness: fixture=wide" in out
    assert "dead_families=['TOOL']" in out
    assert "[dead_signal_family]" in out
    assert "[dead_object_type]" in out


# ---------------------------------------------------------------------------
# snapshot (no DB/pool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_generate_writes_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "build_snapshot", lambda **_kw: {"combination_count": 3})
    written: dict[str, Any] = {}
    monkeypatch.setattr(
        cli, "write_snapshot", lambda snap, path: written.update(snap=snap, path=path)
    )
    args = cli.build_parser().parse_args(["snapshot", "generate"])

    exit_code = await cli._run_snapshot(args)

    assert exit_code == 0
    assert written["snap"] == {"combination_count": 3}
    assert "updated snapshot" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_snapshot_diff_returns_one_when_changes_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "build_snapshot", lambda **_kw: {"combinations": {}})
    monkeypatch.setattr(cli, "load_snapshot", lambda _path: {"combinations": {}})
    monkeypatch.setattr(cli, "diff_snapshots", lambda _b, _c: ["a rank changed"])
    monkeypatch.setattr(cli, "format_diff_report", lambda _lines, snapshot_path: "report")
    args = cli.build_parser().parse_args(["snapshot", "diff"])

    exit_code = await cli._run_snapshot(args)

    assert exit_code == 1


@pytest.mark.asyncio
async def test_snapshot_diff_returns_zero_when_no_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "build_snapshot", lambda **_kw: {"combinations": {}})
    monkeypatch.setattr(cli, "load_snapshot", lambda _path: {"combinations": {}})
    monkeypatch.setattr(cli, "diff_snapshots", lambda _b, _c: [])
    monkeypatch.setattr(cli, "format_diff_report", lambda _lines, snapshot_path: "no changes")
    args = cli.build_parser().parse_args(["snapshot", "diff"])

    exit_code = await cli._run_snapshot(args)

    assert exit_code == 0


@pytest.mark.asyncio
async def test_snapshot_diff_returns_two_when_baseline_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "build_snapshot", lambda **_kw: {"combinations": {}})

    def _raise(_path: Any) -> Any:
        raise FileNotFoundError("no snapshot found")

    monkeypatch.setattr(cli, "load_snapshot", _raise)
    args = cli.build_parser().parse_args(["snapshot", "diff"])

    exit_code = await cli._run_snapshot(args)

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("subcommand", ["generate", "diff"])
@pytest.mark.asyncio
async def test_snapshot_rejects_nonpositive_top_k(
    subcommand: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fix 6: mirror the standalone script's guard — non-positive --top-k must
    fail with a non-zero exit before any snapshot is built."""
    built = {"called": False}

    def _build(**_kw: Any) -> dict[str, Any]:
        built["called"] = True
        return {}

    monkeypatch.setattr(cli, "build_snapshot", _build)
    args = cli.build_parser().parse_args(["snapshot", subcommand, "--top-k", "0"])

    exit_code = await cli._run_snapshot(args)

    assert exit_code == 2
    assert built["called"] is False
    assert "--top-k must be positive" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() dispatch + exception boundary
# ---------------------------------------------------------------------------


def test_main_dispatches_to_command_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_run_migrate(dsn: str | None) -> int:
        seen["dsn"] = dsn
        return 0

    monkeypatch.setattr(cli, "_run_migrate", _fake_run_migrate)

    rc = cli.main(["migrate", *_DSN])

    assert rc == 0
    assert seen["dsn"] == "postgresql://fake/db"


def test_main_propagates_handler_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_validate(_args: Any) -> int:
        return 1

    monkeypatch.setattr(cli, "_run_validate", _fake_run_validate)

    rc = cli.main(["validate", *_DSN])

    assert rc == 1


def test_main_returns_one_and_prints_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _boom(_dsn: str | None) -> int:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "_run_migrate", _boom)

    rc = cli.main(["migrate", *_DSN])

    assert rc == 1
    assert "error: kaboom" in capsys.readouterr().err


def test_main_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """No subcommand → argparse errors out with a non-zero SystemExit."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code != 0
