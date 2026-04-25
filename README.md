# GraphRapping

Beauty product semantic signal graph recommendation system.

Reviews → KG extraction → canonical facts → promoted signals → personalized recommendations.

## Architecture

5-layer pipeline with Common Concept Plane (`concept_id`) joining Product and User:

```
Layer 0  Product/User Master Truth
Layer 1  Raw Evidence (NER/BEE/REL extraction)
Layer 2  Canonical Fact (65 relations)
Layer 2.5 Wrapped Signal (projection registry)
Layer 3  Aggregate/Serving (windowed, corpus-promoted)
Layer 4  Recommendation (candidate → score → rerank → explain)
```

Evidence graph (per-review, Layers 0-2) is separate from serving graph (corpus-promoted, Layers 2.5-3).
Only signals passing 3 promotion gates reach recommendations.

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Key Concepts

- **Corpus Promotion**: Signals need review_count >= 3, confidence >= 0.6, synthetic_ratio <= 0.5
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

# Optional real Postgres integration checks
GRAPHRAPPING_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/graphrapping_test \
  python -m pytest tests/test_postgres_integration.py -q

# Optional Docker-backed Postgres integration check
bash scripts/run_postgres_integration.sh

# Start demo web server
uvicorn src.web.server:app --reload
```

DB migration and batch pipeline are currently library entrypoints (`src.db.migrate.migrate`,
`src.jobs.run_daily_pipeline.run_batch`) rather than CLI commands.

## CI

GitHub Actions quality gate runs on push/PR:

```bash
python -m ruff check src
python -m mypy src
python -m pytest tests/ -q
```

Docker-backed Postgres integration is a manual workflow_dispatch job and runs:

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
