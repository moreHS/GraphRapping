# Evidence-First Personalization Recommendation

## Background

The current GraphRapping recommendation layer can rank products through source review stats, novelty, and broad category handling even when there is no strong user-aligned evidence. Manual frontend inspection showed recommendations that did not clearly use either product master truth or review-derived graph relations in a meaningful way.

Personal-agent contains richer real-user profile data, including category-specific purchase brands, current-use product summaries, repurchase product summaries, repurchase category ranks, seasonal products, and chat-derived preferences. GraphRapping currently adapts only part of that data.

## Decision

Adopt an evidence-first recommendation architecture:

- Candidate eligibility must be established before scoring.
- Product master truth and review-derived graph relations are both first-class recommendation evidence.
- Source review stats are trust/tie-break signals only.
- Brand, category, ingredient, main-benefit, product, and family truth from product master are not subordinate to review graph signals.
- Personal-agent's 3-group profile is the canonical real-user input.
- The first implementation pass must avoid DB schema changes and AmoreSimulation contract changes.

## Considered Options

1. Keep current scoring and tune weights.
   - Rejected because source-only and profile-unrelated recommendations can still pass.

2. Keep ES-style filtering from personal-agent and only enrich explanations.
   - Rejected because this still does not use GraphRapping's integrated evidence layer as the core reason.

3. Introduce evidence-qualified candidate gating, then layered scoring.
   - Chosen because it makes product master truth, review graph relations, and purchase behavior explicit eligibility reasons while keeping source stats in the right role.

## Trade-Offs

- Some users may receive no evidence-qualified recommendation when product/profile alignment and review graph coverage are both sparse. This is acceptable and more honest than returning arbitrary source-only products.
- Runtime profile context may temporarily contain fields not persisted in DB. This avoids premature schema changes while keeping real data available.
- More tests are required because candidate gating, scoring, and explanations now have explicit contracts.

## Follow-Up

Implementation plan: `docs/superpowers/plans/2026-06-19-evidence-first-personalization-redesign.md`
