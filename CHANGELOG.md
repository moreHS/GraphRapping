# Changelog

## Phase 1 — Review Meaning Preservation + KG Cleanup
- Negation/intensity preserved through full pipeline (BEE → CanonicalFact → WrappedSignal)
- BEE_ATTR sentiment split removed (single entity, polarity on edge)
- Auto keywords routed to quarantine (no auto entity creation)
- Promotion gate in adapter (PROMOTE/KEEP_EVIDENCE_ONLY/DROP/QUARANTINE)
- Signal dedup key extended with negated + qualifier_fingerprint
- New enums: EvidenceKind, PromotionDecision, KeywordSource, FactStatus

## Phase 2 — User Layer Enhancement
- 5 fact family builders (state/concern/goal/context/behavior)
- Purchase features: owned_product_ids, repurchased_brand_ids
- 4 new scoring features: skin_type_fit, goal_fit_master, goal_fit_review_signal, purchase_loyalty_score, novelty_bonus
- User serving profile behavior section

## Phase 3 — Incremental Pipeline Stabilization
- P0 fix: empty child row reprocessing banned → load_full_review_snapshot
- Watermark only advances past successfully processed reviews
- Dirty product includes comparison/co-use targets

## Phase 4 — Corpus KG Aggregation
- Promotion threshold: review_count >= 3, confidence >= 0.6, synthetic_ratio <= 0.5
- corpus_weight, distinct_review_count, avg_confidence fields on aggregation
- Graph API ?view=corpus|evidence parameter

## Follow-up Fixes
- recommended_to UserSegment qualifier_required=N
- reverse transform dst_ref_kind uses subject_ref_kind (not hardcoded ENTITY)
- signal_evidence as provenance source of truth (source_fact_ids demoted to cache)
- FactProvenance extended with source_domain/source_kind for generic provenance
- Batch SQL aggregate path for dirty products
- catalog_validation fully excluded from recommendation path
