# GraphRapping Architecture

Beauty product semantic signal graph recommendation system.

## 5-Layer Architecture

```
Layer 0  Product/User Master Truth (immutable source)
Layer 1  Raw Evidence (ner_raw, bee_raw, rel_raw)
Layer 2  Canonical Fact (68 relations, deterministic ID)
Layer 2.5 Wrapped Signal (projection registry → serving signals)
Layer 3  Aggregate/Serving (windowed aggregation, corpus promotion)
Layer 4  Recommendation (candidate → score → rerank → explain)
```

## Current Data Baseline

The active local baseline is fixed to the final 906-review source-grounded
fixture:

- 906 raw reviews.
- 517 product master rows.
- 516 clean source review stats rows.
- 516 clean review-summary sidecar rows.
- 1 `SOURCE_KEY_COLLISION` product excluded from clean source-summary matching.

Clean source joins use `source_channel + source_key_type + source_product_id`.
`product_id` is the downstream compatibility key, not a complete source
identity by itself.

## Common Concept Layer

Shared join plane between Product and User via `concept_id`:
- Brand, Category, Ingredient, BEEAttr, Keyword, Concern, Goal, Tool, Context

## KG Pipeline (per-review evidence graph)

```
MentionExtractor → SameEntityMerger → Canonicalizer → Adapter → CanonicalFactBuilder → SignalEmitter
```

Output is **evidence-scope** (per-review), NOT global KG.
Promotion gate in Adapter classifies edges as PROMOTE / KEEP_EVIDENCE_ONLY / DROP / QUARANTINE.

## Evidence Graph vs Serving Graph

```
Evidence Graph (per-review scope)
  Layers 0-2: product_master, raw evidence, canonical facts
  Source: src/kg/ pipeline (MentionExtractor → Canonicalizer → Adapter)
  Scope: Single review, immutable once created
  Purpose: Audit trail, debug, analyst exploration

Serving Graph (corpus-promoted scope)
  Layers 2.5-3: wrapped signals, aggregated product/user profiles
  Source: src/wrap/ + src/mart/ pipeline
  Scope: Cross-review aggregation, windowed, promoted-only
  Purpose: Recommendation, personalization, product exploration
```

**Key principle**: Evidence graph is never directly consumed by recommendation.
Only signals that pass all 3 promotion gates (adapter → signal_emitter → aggregator)
reach the serving graph. `promoted_only=True` is the default in `build_serving_product_profile()`.

### kg_mode Contract

- `off`: Legacy NER/BEE/REL processing only (no KG pipeline)
- `shadow`: Both legacy and KG pipelines run; KG writes to separate builder for comparison
- `on`: KG pipeline is sole fact source; legacy processing skipped

### kg_mode Resolution (P0-3)

All entry points resolve kg_mode through `src.common.config_loader.get_kg_mode()`:
1. **Explicit function argument** (highest priority)
2. **Environment variable** `GRAPHRAPPING_KG_MODE` (`off|shadow|on`; empty string is invalid)
3. **Caller-specific default**:
   - `run_full_load` / `run_incremental` / `run_batch` / `process_review` / `build_review_persist_bundle`: `"off"`
   - `load_demo_data` (demo UI): `"on"` (KG visualization is the demo's intent)

Invalid values (e.g. `"On"`, `"true"`) raise `ValueError` immediately — fail-closed.

## Promotion Architecture (3 layers)

1. **Adapter** (per-edge): synthetic/auto → evidence-only, standard → promote
2. **SignalEmitter** (per-fact): projection_registry promotion_mode (IMMEDIATE/CORPUS_THRESHOLD/NEVER)
3. **Aggregator** (corpus): distinct_review_count >= 2 (30d) / >= 3 (90d, all), avg_confidence >= 0.6, synthetic_ratio <= 0.5

## Key Invariants

- Layer 2 canonical fact semantics are never broken
- 68 canonical relations preserved
- Layer 3 signals ONLY through projection registry
- Product master truth is never overwritten by review signals
- Product/brand canonical labels from product master are truth nodes/links, not
  review-derived evidence counts
- Review-summary text is a mart sidecar, not graph evidence
- Reviewer proxy and real user are never merged
- Signal provenance source of truth: `signal_evidence` table
- `source_fact_ids` on `wrapped_signal` is cache only
- `catalog_validation_signal` excluded from candidate/scoring/standard explanation
- Serving product profile uses promoted signals only (promoted_only=True default)
- Evidence graph is per-review scope; serving graph is corpus-aggregated

## Data Contracts

- Signal dedup key: `(review_id, target_product_id, edge_type, dst_id, polarity, negated, qualifier_fingerprint, registry_version)`
- Fact dedup key: `(review_id, subject_iri, predicate, object_ref, polarity, qualifier_fingerprint)`
- Concept join key: `concept_id` (not IRI) in serving/runtime
- `recommended_to` with UserSegment object: qualifier NOT required (direct projection)

## Recommendation Flow

```
serving_user_profile + serving_product_profiles
  → generate_candidates (hard filter + concept overlap)
  → scorer (19 features + evidence shrinkage)
  → reranker (diversity bonus)
  → explainer (score-faithful paths + provenance)
  → hook_generator + next_question
```

## Scoring Features (19)

keyword_match, residual_bee_attr_match, context_match, concern_fit,
concern_bridge_fit, ingredient_match, brand_match_conf_weighted, goal_fit_master,
category_affinity, freshness_boost, skin_type_fit, purchase_loyalty_score, novelty_bonus,
exact_owned_penalty, owned_family_penalty, same_family_explore_bonus,
repurchase_family_affinity, tool_alignment, coused_product_bonus
