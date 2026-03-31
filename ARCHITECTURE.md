# GraphRapping Architecture

Beauty product semantic signal graph recommendation system.

## 5-Layer Architecture

```
Layer 0  Product/User Master Truth (immutable source)
Layer 1  Raw Evidence (ner_raw, bee_raw, rel_raw)
Layer 2  Canonical Fact (65 relations, deterministic ID)
Layer 2.5 Wrapped Signal (projection registry → serving signals)
Layer 3  Aggregate/Serving (windowed aggregation, corpus promotion)
Layer 4  Recommendation (candidate → score → rerank → explain)
```

## Common Concept Layer

Shared join plane between Product and User via `concept_id`:
- Brand, Category, Ingredient, BEEAttr, Keyword, Concern, Goal, Tool, Context

## KG Pipeline (per-review evidence graph)

```
MentionExtractor → SameEntityMerger → Canonicalizer → Adapter → CanonicalFactBuilder → SignalEmitter
```

Output is **evidence-scope** (per-review), NOT global KG.
Promotion gate in Adapter classifies edges as PROMOTE / KEEP_EVIDENCE_ONLY / DROP / QUARANTINE.

## Promotion Architecture (3 layers)

1. **Adapter** (per-edge): synthetic/auto → evidence-only, standard → promote
2. **SignalEmitter** (per-fact): projection_registry promotion_mode (IMMEDIATE/CORPUS_THRESHOLD/NEVER)
3. **Aggregator** (corpus): review_count >= 3, confidence >= 0.6, synthetic_ratio <= 0.5

## Key Invariants

- Layer 2 canonical fact semantics are never broken
- 65 canonical relations preserved
- Layer 3 signals ONLY through projection registry
- Product master truth is never overwritten by review signals
- Reviewer proxy and real user are never merged
- Signal provenance source of truth: `signal_evidence` table
- `source_fact_ids` on `wrapped_signal` is cache only
- `catalog_validation_signal` excluded from candidate/scoring/standard explanation

## Data Contracts

- Signal dedup key: `(review_id, target_product_id, edge_type, dst_id, polarity, negated, qualifier_fingerprint, registry_version)`
- Fact dedup key: `(review_id, subject_iri, predicate, object_ref, polarity, qualifier_fingerprint)`
- Concept join key: `concept_id` (not IRI) in serving/runtime
- `recommended_to` with UserSegment object: qualifier NOT required (direct projection)

## Recommendation Flow

```
serving_user_profile + serving_product_profiles
  → generate_candidates (hard filter + concept overlap)
  → scorer (13 features + evidence shrinkage)
  → reranker (diversity bonus)
  → explainer (score-faithful paths + provenance)
  → hook_generator + next_question
```

## Scoring Features (13)

keyword_match, residual_bee_attr_match, context_match, concern_fit,
ingredient_match, brand_match_conf_weighted, goal_fit_master, goal_fit_review_signal,
category_affinity, freshness_boost, skin_type_fit, purchase_loyalty_score, novelty_bonus
