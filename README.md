# GraphRapping

Beauty product semantic signal graph recommendation system.

Reviews → KG extraction → canonical facts → promoted signals → personalized recommendations.

## Current Baseline

The active local baseline is the final source-grounded 906-review fixture:

- 906 reviews in `mockdata/review_triples_raw.json`
- 517 products in `mockdata/product_catalog_es.json`
- 38 brands, 517 products, and 50 users in `mockdata/shared_entities.json`
- 516 clean `product_review_stats` rows and 516 clean `review_summary_sidecar`
  rows in the refreshed local DB

Product/source identity is joined by `source_channel + source_key_type +
source_product_id`. `product_id` remains the downstream compatibility key, but
`source_product_id` alone is not a clean source identity because one product is
marked `SOURCE_KEY_COLLISION`.

See:

- [DB consumer contract](docs/architecture/db_consumer_contract.md)
- [906 fixture lineage](docs/architecture/v260605_906_fixture_lineage.md)
- [Final baseline cleanup decision](DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md)

## Architecture

5-layer pipeline with Common Concept Plane (`concept_id`) joining Product and User:

```
Layer 0  Product/User Master Truth
Layer 1  Raw Evidence (NER/BEE/REL extraction)
Layer 2  Canonical Fact (68 relations)
Layer 2.5 Wrapped Signal (projection registry)
Layer 3  Aggregate/Serving (windowed, corpus-promoted)
Layer 4  Recommendation (candidate → score → rerank → explain)
```

Evidence graph (per-review, Layers 0-2) is separate from serving graph (corpus-promoted, Layers 2.5-3).
Only signals passing 3 promotion gates reach recommendations.

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Key Concepts

- **Corpus Promotion**: distinct_review_count >= 2 (30d) / >= 3 (90d, all), avg_confidence >= 0.6, synthetic_ratio <= 0.5
- **Common Concept Plane**: Brand, Category, Ingredient, BEEAttr, Keyword, Concern, Goal, Tool, Context
- **19 Scoring Features**: keyword_match, residual_bee_attr_match, concern_fit, concern_bridge_fit, goal_fit_master, family ownership features, tool/co-use features, etc.
- **Provenance**: signal_evidence table is source of truth for explanation chains

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Static check snapshot
python -m ruff check src --statistics
python -m mypy src

# Postgres integration — local Postgres 16 on localhost:5432
#   Runs PG-bound tests against a real database.
#   The script auto-detects mode: if GRAPHRAPPING_TEST_DATABASE_URL is set,
#   it uses your local DB; otherwise it spawns an ephemeral postgres:16 container.
createdb -h localhost -U postgres graphrapping   # one-time
export GRAPHRAPPING_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/graphrapping"
export DATABASE_URL="$GRAPHRAPPING_DATABASE_URL"
export GRAPHRAPPING_TEST_DATABASE_URL="$GRAPHRAPPING_DATABASE_URL"
bash scripts/run_postgres_integration.sh

# Subset: just the single-file check
python -m pytest tests/test_postgres_integration.py -q --timeout=120

# Operator CLI — migrate / full-load / incremental / validate / monitor / snapshot
# (migrate/full-load/incremental/validate/monitor use the GRAPHRAPPING_DATABASE_URL /
# DATABASE_URL exported above; --help on any subcommand works without a DSN)
python -m src.cli migrate
python -m src.cli full-load
python -m src.cli incremental
python -m src.cli validate
python -m src.cli monitor  # retention/unbounded-growth risk report (quarantine/agg_*/raw layer/table sizes)

# snapshot needs no DB — regression-checks golden-profile rankings against
# tests/fixtures/ranking_snapshots/dense_golden.json
python -m src.cli snapshot diff      # compare current output to the stored baseline
python -m src.cli snapshot generate  # after a reviewed scoring/rule change, update the baseline

# Start demo web server
uvicorn src.web.server:app --reload
```

### Environment Variables (web server / alerting)

| Variable | Meaning | Default |
|----------|---------|---------|
| `GRAPHRAPPING_SERVING_MODE` | Recommendation data source: `demo` (in-memory `DemoState`) or `db` (`serving_product_profile`/`serving_user_profile` from Postgres, requires `GRAPHRAPPING_DATABASE_URL`/`DATABASE_URL`) | `demo` |
| `GRAPHRAPPING_SERVING_REFRESH_SEC` | DB serving store cache refresh interval, in seconds (`db` mode only). `0` disables the cache (every request does a full reload) — not recommended for production | `300` |
| `GRAPHRAPPING_CANDIDATE_PREFILTER` | Candidate path: `auto` (SQL prefilter on in `db` mode, off in `demo` mode), `on` (force SQL prefilter), `off` (full traversal, no SQL pre-narrowing) | `auto` |
| `GRAPHRAPPING_ALERT_WEBHOOK_URL` | Webhook URL for a best-effort JSON POST on pipeline failure / retention-threshold breach. Unset or blank means no-op (no network attempt) | unset (disabled) |
| `GRAPHRAPPING_RETENTION_ALERT_ENABLED` | Opt-in gate for the post-run retention-threshold alert (DB pipeline entrypoints only). Set to `1` to enable | unset (`0`/off) |

Each subcommand exposes its own `--help`. The underlying functions
(`src.db.migrate.migrate`, `src.jobs.run_full_load_db.run_full_load_to_db`,
`src.jobs.run_incremental_pipeline_db.run_incremental_to_db`,
`src.db.contract_validator.validate_all`) remain directly importable for
scripts and tests — see `src/cli.py` for the full mapping.

## CI

GitHub Actions quality gate runs on push/PR:

```bash
python -m ruff check src
python -m mypy src
python -m pytest tests/ -q
```

A `postgres-service` job also runs automatically on every push/PR, against a
Postgres 16 service container, executing the PG-bound test files that the
quality job's DB-less run always skips (see `.github/workflows/ci.yml`).

Docker-backed Postgres integration (`scripts/run_postgres_integration.sh`) is a
separate, manual `workflow_dispatch` job — two Postgres-bound CI jobs exist in
total (automatic `postgres-service` + manual `postgres-integration`):

```bash
bash scripts/run_postgres_integration.sh
```

## Project Structure

```
src/
  common/       # IDs, enums, config loader, text normalization
  normalize/    # BEE normalizer
  kg/           # Evidence graph pipeline (per-review)
  canonical/    # Canonical fact builder
  wrap/         # Signal emitter + projection registry
  mart/         # Aggregation + serving views
  rec/          # Candidate generation, scoring, explanation
  user/         # User fact canonicalization + adapters
  ingest/       # Product/purchase/review ingestion
  db/           # Repos, migration, unit of work
  web/          # API server
  jobs/         # Daily/incremental pipeline runners
configs/        # projection_registry.csv, scoring_weights.yaml
sql/            # DDL scripts
tests/          # Test suite
```
