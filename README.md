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
- **13 Scoring Features**: keyword_match, bee_attr_match, concern_fit, goal_fit_master, goal_fit_review_signal, skin_type_fit, purchase_loyalty_score, novelty_bonus, etc.
- **Provenance**: signal_evidence table is source of truth for explanation chains

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# DB migration
python -m src.db.migrate

# Run pipeline (batch mode)
python -m src.jobs.run_daily_pipeline --kg-mode=on

# Start web server
python -m src.web.server
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
