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

- **Corpus Promotion**: distinct_review_count >= 2 (30d/90d/all — Phase 7 C2), avg_confidence >= 0.6, synthetic_ratio <= 0.5
- **Common Concept Plane**: Brand, Category, Ingredient, BEEAttr, Keyword, Concern, Goal, Tool, Context
- **Evidence families (OR-eligibility)**: PRODUCT_MASTER_TRUTH / REVIEW_GRAPH_RELATION / REVIEW_GRAPH_WEAK / PURCHASE_BEHAVIOR qualify a candidate; **boost-only** types (`comparison`, `collab`, `comention`, `similar`) never qualify alone and never buy retrieval ordering — contract in [db_consumer_contract §13](docs/architecture/db_consumer_contract.md)
- **19 Scoring Features**: keyword_match, residual_bee_attr_match, concern_fit, concern_bridge_fit, goal_fit_master, family ownership features, tool/co-use features, etc. (frontend slider contract; backend-only boost weights live outside this map)
- **Provenance**: signal_evidence table is source of truth for explanation chains

## Product-Product Similarity (Phase 8)

Products connect through **shared attribute nodes** (2-hop projection over the
bipartite canonical-fact graph): `similarity(A,B) = Σ IDF(shared node)` — no
hard-AND, no node merging. The keyword axis uses a **composite key**
`keyword::{bee_attr}:{keyword}:{polarity}` sourced from the raw wrapped-signal
sidecar ("가볍다" under 제형 vs 발림성 are different nodes). IDF auto-damps hubs
(mega-brands, universal attributes) instead of hard exclusion lists.

`category_gate` is a **consumption-context parameter**, not a property of the
computation:

| Surface | Gate | What you see |
|---|---|---|
| Graph viewer (`SHARES_ATTRIBUTE` dashed edges + evidence tooltip) | ON | why two products connect |
| `GET /api/products/{id}/similar` + "비슷한 상품" widget | ON | attribute-similar products with shared-axis chips |
| `similar_product_affinity` recommendation boost (owned-product anchors) | OFF | bounded re-score (≤ +0.02) of already-eligible candidates |
| `related_products` on `/api/search` & `/api/ask` ("관련 상품 더보기") | upstream (query) | discovery section, hard exclusions preserved |

Surface policy: a neighbor whose only shared evidence is the brand axis is not
shown on similar-product surfaces (DECISIONS/2026-07-18_phase8_brand_only_neighbor_policy.md).

## Real User Profiles (opt-in, purchase-history backfill)

`scripts/fetch_user_profiles_pg.py` pulls K pseudonymized real profiles from the
personalization agent's Azure PG view (read-only; credentials referenced from
that project's `.env`, never copied) and resolves purchase representative codes
(9-digit `rprs_prd_cd`) against the catalog's `REPRESENTATIVE_PROD_CODE` to embed
`purchase_events` — one purchase occurrence = one event. Output goes ONLY to the
git-ignored `mockdata/real/` directory.

```bash
python scripts/fetch_user_profiles_pg.py --limit 50    # writes mockdata/real/ (never committed)
export GRAPHRAPPING_USER_PROFILES_JSON=mockdata/real/user_profiles_real_normalized.json
uvicorn src.web.server:app  # + POST /api/pipeline/run — G4 boost fires on real owned edges
```

Unset env = the synthetic fixture path, byte-identical (tests/snapshots never
depend on real data). **Operational constraint: real-profile mode is for the
loopback-bound local demo only — do not expose publicly.** Details:
[DECISIONS/2026-07-18_purchase_history_backfill.md](DECISIONS/2026-07-18_purchase_history_backfill.md).

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
| `GRAPHRAPPING_QUERY_LLM` | Query-understanding LLM provider (Phase 6): `azure` (recommended when enabled) / `anthropic` / `off`. Unset behaves as `off` (dictionary fallback; no network). Requires the `query-llm` extra (`pip install -e '.[query-llm]'`) — if httpx is missing the module warns and falls back | unset (`off`) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint, e.g. `https://<resource>.openai.azure.com` (required when `GRAPHRAPPING_QUERY_LLM=azure`) | unset |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key (required when `GRAPHRAPPING_QUERY_LLM=azure`). Read from env only; never logged | unset |
| `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI chat deployment name (required when `GRAPHRAPPING_QUERY_LLM=azure`) | unset |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI REST API version, e.g. `2024-10-21` (required when `GRAPHRAPPING_QUERY_LLM=azure`) | unset |
| `ANTHROPIC_API_KEY` | Anthropic API key (required when `GRAPHRAPPING_QUERY_LLM=anthropic`; model defaults to `claude-haiku-4-5`). Read from env only; never logged | unset |
| `GRAPHRAPPING_ENABLE_PIPELINE_RUN` | Set `1` to allow `POST /api/pipeline/run` (demo data load) | unset (disabled) |
| `GRAPHRAPPING_USER_PROFILES_JSON` | Opt-in path to a user-profile file that replaces the fixture default for the demo pipeline — used for the pseudonymized real-profile mode (`mockdata/real/...`). Unset = fixture file, byte-identical. Loopback-only; see Real User Profiles section | unset |
| `ES_CLOUD_URL` / `ES_CLOUD_KEY` | Elasticsearch base URL + API key (`Authorization: ApiKey …`) for the product-master re-extraction backend (`scripts/fetch_product_catalog_es.py`). Read from env/`.env` only; never logged | unset |
| `ES_AMORE_INDEX` / `ES_INNI_INDEX` | Product index names (Amore ch. 031 / Innisfree ch. 036); the ES export defaults to their de-duplicated union (override with `--indices`) | unset |
| `AIBE_DB_URL` · `AIBE_DB_PORT` · `AIBE_DB_NM` · `AIBE_DB_USER` · `AIBE_DB_PW` · `AIBE_DB_SCHEMA` | Azure PG credentials for the user-profile backfill (`scripts/fetch_user_profiles_pg.py`), resolved **env-first, then the `--env-file` personal-agent `.env` fallback**. Never logged | unset (falls back to the legacy personal-agent `.env`) |

DB-related variables can be centralized in a **git-ignored `.env`** at the repo
root (`cp .env.example .env`); the connector scripts load it opt-in at startup
and shell/CI values always take precedence. See
[DECISIONS/2026-07-20_ic3_env_and_es_backend.md](DECISIONS/2026-07-20_ic3_env_and_es_backend.md).

Each subcommand exposes its own `--help`. The underlying functions
(`src.db.migrate.migrate`, `src.jobs.run_full_load_db.run_full_load_to_db`,
`src.jobs.run_incremental_pipeline_db.run_incremental_to_db`,
`src.db.contract_validator.validate_all`) remain directly importable for
scripts and tests — see `src/cli.py` for the full mapping.

## Demo UI

- **User / developer mode**: the `🛠 개발자` toggle (also `?dev=1` or
  `localStorage.gr_dev_mode`) switches the recommendation tester between the
  default user-facing controls and the raw weight/mode/shrinkage/diversity
  sliders plus per-result score-layer breakdown.
- **Intent presets**: 3 presets from `GET /api/recommend/presets` replace the
  raw controls in user mode.
- **Integrated query bar**: `POST /api/ask` resolves to a query-scoped
  recommendation when a user is selected, or a plain concept search when none
  is selected. Query understanding uses an LLM when `GRAPHRAPPING_QUERY_LLM`
  is set, falling back to a dictionary matcher by default.
- **Inline "why this" graph**: each recommendation card can expand a small
  subgraph of the explanation paths behind that recommendation.
- **Graph viewer similarity edges** (Phase 8 G2): the corpus product graph draws
  undirected dashed `SHARES_ATTRIBUTE` edges between attribute-similar products
  (top-3 per anchor); hovering shows the shared-axis evidence and score.
- **"비슷한 상품" widget** (G3): the product detail panel lists attribute-similar
  products with shared-axis chips (`GET /api/products/{id}/similar`; hidden when
  empty).
- **"관련 상품 더보기"** (G5): search/ask results append a related-products
  section anchored on the top primary results, each entry attributed to its
  anchor ("'X'과 속성 공유") with evidence chips.

## Development History

The full development narrative — discussions, decisions, and per-phase
execution reports — is consolidated in
[fable_doc/09_development_history.md](fable_doc/09_development_history.md).
Individual decision records live in [DECISIONS/](DECISIONS/), detailed plans and
reviews in [fable_doc/](fable_doc/).

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
